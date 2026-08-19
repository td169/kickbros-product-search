#!/usr/bin/env python3
"""
Scheduled scan for "Plan next trip": finds the cheapest valid 24-hour Paris trip pattern
(land by 11:00, one night, depart 21:00-23:59 the next day, outbound Mon/Tue/Wed only) and
writes results to Supabase for the app's Trips tab to read and rank.

This is server-side only — see CLAUDE.md for why (Duffel's API doesn't send CORS headers, so
it can't be called from the browser; the hotel scrape has the same constraint for a different
reason, it needs a Python runtime and a hidden Scrapfly key).

Run via .github/workflows/scan-trips.yml, or locally for testing:

    SCRAPFLY_KEY=... DUFFEL_KEY=... SUPABASE_URL=... SUPABASE_ANON_KEY=... \
        python3 scripts/scan_trips.py --dry-run

--dry-run prints what would be searched/scraped and upserted without spending money on paid
API calls or writing to Supabase — always run this first when testing changes to this script.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

# ---- fixed business rules (see CLAUDE.md — do not make these generic) ----
# Outbound Mon(0)/Tue(1)/Wed(2) only — Thursday would put the return on Friday, which the
# pattern also bans. Resolved this way after finding the original spec's own rules conflicted;
# see CLAUDE.md's "Plan next trip" section for the full reasoning.
VALID_OUTBOUND_WEEKDAYS = {0, 1, 2}
PARIS_TZ = ZoneInfo("Europe/Paris")
UK_TZ = ZoneInfo("Europe/London")
DEFAULT_ARRIVAL_CUTOFF = "11:00"
DEFAULT_DEPARTURE_WINDOW = ("21:00", "23:59")
ROLLING_WINDOW_DAYS = 61  # matches the hotel scraper's own default price_n_days
# A first real run at 0.2s between calls hit Duffel's rate limit almost immediately (nearly all
# 225 searches came back 429). 1.0s is untested but a much safer starting point — tune this
# down carefully if it turns out to be overly conservative, watching for 429s in the log either way.
DUFFEL_REQUEST_DELAY = 1.0

# Fixed set — do not expand to other London airports (Gatwick/Heathrow/City) via config.
ORIGIN_AIRPORTS = ["SEN", "LTN", "STN"]
# Beauvais (BVA) was removed entirely per Temi's request — not just defaulted off. CDG/Orly only.
DESTINATION_AIRPORTS = ["CDG", "ORY"]

DUFFEL_URL = "https://api.duffel.com/air/offer_requests"


def log(msg):
    print(msg, flush=True)


def valid_outbound_dates(start: date, days: int, target_month: Optional[str]):
    """Yields date objects for every valid (Mon/Tue/Wed) outbound day in the window.

    If target_month is given ("YYYY-MM"), restricts to that month; otherwise uses a rolling
    window of `days` days starting from `start`.
    """
    if target_month:
        year, month = (int(x) for x in target_month.split("-"))
        d = date(year, month, 1)
        while d.month == month:
            if d >= start and d.weekday() in VALID_OUTBOUND_WEEKDAYS:
                yield d
            d += timedelta(days=1)
    else:
        for i in range(days):
            d = start + timedelta(days=i)
            if d.weekday() in VALID_OUTBOUND_WEEKDAYS:
                yield d


def parse_hhmm(s: str):
    h, m = (int(x) for x in s.split(":"))
    return h, m


def to_aware_iso(naive_str, tz):
    """Duffel returns segment times as naive local wall-clock strings, no timezone info at all
    (confirmed via --debug-offer against the live API). Postgres' timestamptz columns silently
    reinterpret a naive string using the session's own timezone (UTC on Supabase) rather than
    leaving the wall-clock value alone — so a genuinely-valid 22:05 Paris departure was getting
    stored as 22:05 UTC, which reads back as 00:05 the next day in Paris (CEST, UTC+2). This
    attaches the correct IANA timezone for whichever airport that time is actually local to
    (`tz`) before the string ever reaches Supabase, so the stored instant is unambiguous and
    reads back correctly everywhere, including in the browser's own Europe/Paris formatting."""
    dt = datetime.fromisoformat(naive_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.isoformat()


def duffel_search(session, origin, destination, out_date, ret_date, duffel_key, max_retries=4):
    """POSTs one round-trip offer_request, retrying on 429 with backoff. A first real run hit
    Duffel's rate limit almost immediately at the original 0.2s pace between calls (225 searches
    nearly all came back 429) — this is why both the delay in scan_flights() and this retry
    logic exist now; without the retry, a rate-limited request was just silently dropped."""
    for attempt in range(max_retries):
        resp = session.post(
            DUFFEL_URL,
            headers={
                "Authorization": f"Bearer {duffel_key}",
                "Duffel-Version": "v2",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "data": {
                    "slices": [
                        {"origin": origin, "destination": destination, "departure_date": out_date.isoformat()},
                        {"origin": destination, "destination": origin, "departure_date": ret_date.isoformat()},
                    ],
                    "passengers": [{"type": "adult"}],
                    "cabin_class": "economy",
                }
            },
            timeout=30,
        )
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            log(f"    rate limited, waiting {wait:.0f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        break
    if not resp.ok:
        log(f"    Duffel error {resp.status_code} for {origin}->{destination} {out_date}: {resp.text[:200]}")
        return []
    data = resp.json().get("data", {})
    return data.get("offers", [])


def offer_is_valid(offer, arrival_cutoff_hhmm, dep_window_start_hhmm, dep_window_end_hhmm):
    """Checks the pattern: outbound lands by cutoff, return departs within the evening window,
    both in Europe/Paris local time (the airport the times matter at)."""
    try:
        outbound_segments = offer["slices"][0]["segments"]
        return_segments = offer["slices"][1]["segments"]
        arrival = datetime.fromisoformat(outbound_segments[-1]["arriving_at"])
        departure = datetime.fromisoformat(return_segments[0]["departing_at"])
    except (KeyError, IndexError, ValueError):
        return False

    if arrival.tzinfo is None:
        arrival = arrival.replace(tzinfo=PARIS_TZ)
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=PARIS_TZ)
    arrival_paris = arrival.astimezone(PARIS_TZ)
    departure_paris = departure.astimezone(PARIS_TZ)

    cutoff_h, cutoff_m = parse_hhmm(arrival_cutoff_hhmm)
    if (arrival_paris.hour, arrival_paris.minute) > (cutoff_h, cutoff_m):
        return False

    start_h, start_m = parse_hhmm(dep_window_start_hhmm)
    end_h, end_m = parse_hhmm(dep_window_end_hhmm)
    dep_time = (departure_paris.hour, departure_paris.minute)
    if not (start_h, start_m) <= dep_time <= (end_h, end_m):
        return False

    return True


def scan_flights(duffel_key, dry_run, arrival_cutoff, dep_start, dep_end, target_month):
    rows = []
    dates = list(valid_outbound_dates(date.today() + timedelta(days=1), ROLLING_WINDOW_DAYS, target_month))
    combos = [(d, o, dest) for d in dates for o in ORIGIN_AIRPORTS for dest in DESTINATION_AIRPORTS]
    log(f"Flights: {len(dates)} valid outbound dates x {len(ORIGIN_AIRPORTS)} origins x "
        f"{len(DESTINATION_AIRPORTS)} destinations = {len(combos)} searches")

    if dry_run:
        for d, o, dest in combos[:5]:
            log(f"  [dry-run] would search {o} -> {dest}, out {d}, back {d + timedelta(days=1)}")
        if len(combos) > 5:
            log(f"  [dry-run] ...and {len(combos) - 5} more")
        return rows, dates

    session = requests.Session()
    for i, (out_date, origin, dest) in enumerate(combos, 1):
        ret_date = out_date + timedelta(days=1)
        log(f"  [{i}/{len(combos)}] {origin} -> {dest}, out {out_date}, back {ret_date}")
        offers = duffel_search(session, origin, dest, out_date, ret_date, duffel_key)
        valid_offers = [o for o in offers if offer_is_valid(o, arrival_cutoff, dep_start, dep_end)]
        if not valid_offers:
            time.sleep(DUFFEL_REQUEST_DELAY)
            continue
        cheapest = min(valid_offers, key=lambda o: float(o["total_amount"]))
        out_seg = cheapest["slices"][0]["segments"]
        ret_seg = cheapest["slices"][1]["segments"]
        rows.append({
            "outbound_date": out_date.isoformat(),
            "return_date": ret_date.isoformat(),
            "origin_airport": origin,
            "destination_airport": dest,
            # UK legs get Europe/London, Paris legs get Europe/Paris — the two differ by an hour
            # for part of the year (BST vs CEST), so this isn't just cosmetic.
            "outbound_departure": to_aware_iso(out_seg[0]["departing_at"], UK_TZ),
            "outbound_arrival": to_aware_iso(out_seg[-1]["arriving_at"], PARIS_TZ),
            "outbound_airline": out_seg[0].get("operating_carrier", {}).get("name"),
            "return_departure": to_aware_iso(ret_seg[0]["departing_at"], PARIS_TZ),
            "return_arrival": to_aware_iso(ret_seg[-1]["arriving_at"], UK_TZ),
            "return_airline": ret_seg[0].get("operating_carrier", {}).get("name"),
            "total_price": float(cheapest["total_amount"]),
            "currency": cheapest.get("total_currency", "GBP"),
        })
        time.sleep(DUFFEL_REQUEST_DELAY)
    return rows, dates


def load_tracked_hotels(supabase_url, supabase_key):
    """The hotel list lives in Supabase now, not a hardcoded constant here — populated by the
    one-off scripts/resolve_hotels.py, editable by adding/removing rows directly in Supabase
    without a code change. Only returns hotels that actually have a resolved booking_url."""
    if not supabase_url or not supabase_key:
        return []
    resp = requests.get(
        f"{supabase_url.rstrip('/')}/rest/v1/tracked_hotels?select=name,booking_url",
        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
        timeout=30,
    )
    if not resp.ok:
        log(f"  load_tracked_hotels failed: {resp.status_code} {resp.text[:300]}")
        return []
    return [h for h in resp.json() if h.get("booking_url")]


def scan_hotels(dry_run, supabase_url, supabase_key):
    rows = []
    hotels = load_tracked_hotels(supabase_url, supabase_key)
    if not hotels:
        log("  no hotels found in tracked_hotels (or Supabase creds not given) — nothing to "
            "scan. Run scripts/resolve_hotels.py first to populate it.")
        return rows

    if dry_run:
        for hotel in hotels:
            log(f"  [dry-run] would scrape {hotel['name']} ({hotel['booking_url']}) for "
                f"{ROLLING_WINDOW_DAYS} days from today")
        return rows

    import bookingcom  # vendored — see scripts/vendor/bookingcom.py for provenance/license

    async def run():
        for hotel in hotels:
            log(f"  scraping {hotel['name']}...")
            try:
                result = await bookingcom.scrape_hotel(
                    hotel["booking_url"], checkin=date.today().isoformat(), price_n_days=ROLLING_WINDOW_DAYS
                )
            except Exception as e:
                log(f"    failed: {e}")
                continue
            for day in result.get("price", []):
                try:
                    stay_date = date.fromisoformat(day["checkin"])
                except (KeyError, ValueError):
                    continue
                if stay_date.weekday() not in VALID_OUTBOUND_WEEKDAYS or not day.get("available"):
                    continue
                # avgPriceFormatted is a display string like "£342" (with country set to GB in
                # the vendored scraper's BASE_CONFIG, it comes back with a £ symbol) — a first
                # real run crashed on `float("£342")` directly. It never shows pence/cents in
                # practice (every value seen has been a whole number), so any "." that appears
                # is a thousands separator, not a decimal point — a run against the full 18-hotel
                # list found 6 rows where keeping the "." turned e.g. "1.400" (real: £1,400) into
                # float 1.4, six wildly-wrong nightly prices slipping through undetected. Strip
                # ALL non-digit characters (not just non-digit-non-period) instead.
                price_str = re.sub(r"[^\d]", "", str(day.get("avgPriceFormatted", "")))
                if not price_str:
                    continue
                nightly_price = float(price_str)
                # Defense in depth: no genuine Paris hotel/apartment in this list charges under
                # £30/night — discard anything that low rather than trust a number that's
                # obviously still wrong, whatever the cause.
                if nightly_price < 30:
                    log(f"    discarding implausible price {nightly_price} for {stay_date} "
                        f"(raw: {day.get('avgPriceFormatted')!r})")
                    continue
                rows.append({
                    "hotel_name": hotel["name"],
                    "booking_url": hotel["booking_url"],
                    "stay_date": stay_date.isoformat(),
                    "nightly_price": nightly_price,
                    "currency": "GBP",  # matches BASE_CONFIG's country:"GB" in bookingcom.py
                })

    asyncio.run(run())
    return rows


def upsert_supabase(table, rows, on_conflict, supabase_url, supabase_key):
    if not rows:
        log(f"  nothing to upsert into {table}")
        return
    resp = requests.post(
        f"{supabase_url.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}",
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        data=json.dumps(rows),
        timeout=30,
    )
    if not resp.ok:
        log(f"  Supabase upsert to {table} failed: {resp.status_code} {resp.text[:300]}")
    else:
        log(f"  upserted {len(rows)} row(s) into {table}")


def delete_stale_flight_rows(dates, supabase_url, supabase_key):
    """Upserting only ever adds/refreshes rows for combos that ARE currently valid — a combo
    that used to have a valid offer but doesn't anymore (schedule/pricing changed between runs)
    would otherwise sit there forever with stale, possibly-wrong data. Found exactly this after
    the timezone storage bug fix: 3 rows from the earlier corrupted run stayed behind because
    the corrected run found no valid offer for those exact date+route combos, so nothing
    upserted over them. Delete every row in the scanned date range before upserting the fresh
    set, so the table always reflects exactly what this run actually found."""
    if not dates:
        return
    resp = requests.delete(
        f"{supabase_url.rstrip('/')}/rest/v1/flight_prices"
        f"?outbound_date=gte.{min(dates).isoformat()}&outbound_date=lte.{max(dates).isoformat()}",
        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
        timeout=30,
    )
    if not resp.ok:
        log(f"  delete_stale_flight_rows failed: {resp.status_code} {resp.text[:300]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-month", default=None, help="YYYY-MM, defaults to a rolling window")
    parser.add_argument("--arrival-cutoff", default=DEFAULT_ARRIVAL_CUTOFF)
    parser.add_argument("--departure-window-start", default=DEFAULT_DEPARTURE_WINDOW[0])
    parser.add_argument("--departure-window-end", default=DEFAULT_DEPARTURE_WINDOW[1])
    parser.add_argument("--skip-flights", action="store_true",
                         help="Skip the (expensive, paid-per-search) flight scan — for iterating on the hotel side alone")
    parser.add_argument("--skip-hotels", action="store_true", help="Skip the hotel scrape")
    parser.add_argument("--debug-offer", default=None,
                         help="ORIGIN,DEST,YYYY-MM-DD — runs a single Duffel search and prints every "
                              "returned offer's raw segment times + validation verdict, no Supabase writes")
    args = parser.parse_args()

    duffel_key = os.environ.get("DUFFEL_KEY")
    scrapfly_key = os.environ.get("SCRAPFLY_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY")

    if args.debug_offer:
        origin, dest, out_date_str = args.debug_offer.split(",")
        out_date = date.fromisoformat(out_date_str)
        ret_date = out_date + timedelta(days=1)
        session = requests.Session()
        offers = duffel_search(session, origin, dest, out_date, ret_date, duffel_key)
        log(f"{len(offers)} offer(s) returned for {origin}->{dest} {out_date}")
        for i, o in enumerate(offers):
            out_segs = o["slices"][0]["segments"]
            ret_segs = o["slices"][1]["segments"]
            valid = offer_is_valid(o, args.arrival_cutoff, args.departure_window_start, args.departure_window_end)
            log(f"  offer {i}: total_amount={o.get('total_amount')} {o.get('total_currency')} valid={valid}")
            log(f"    outbound: {out_segs[0]['departing_at']} -> {out_segs[-1]['arriving_at']}")
            log(f"    return:   {ret_segs[0]['departing_at']} -> {ret_segs[-1]['arriving_at']}")
        return

    if not args.dry_run:
        needed = [("DUFFEL_KEY", duffel_key)] if not args.skip_flights else []
        needed += [("SCRAPFLY_KEY", scrapfly_key)] if not args.skip_hotels else []
        needed += [("SUPABASE_URL", supabase_url), ("SUPABASE_ANON_KEY", supabase_key)]
        missing = [n for n, v in needed if not v]
        if missing:
            log(f"Missing required env vars: {', '.join(missing)} (or pass --dry-run)")
            sys.exit(1)

    if args.skip_flights:
        log("=== Flights (skipped) ===")
    else:
        log("=== Flights ===")
        flight_rows, scanned_dates = scan_flights(duffel_key, args.dry_run, args.arrival_cutoff,
                                                    args.departure_window_start, args.departure_window_end, args.target_month)
        log(f"Found {len(flight_rows)} valid flight combination(s)")
        if not args.dry_run:
            delete_stale_flight_rows(scanned_dates, supabase_url, supabase_key)
            upsert_supabase("flight_prices", flight_rows, "outbound_date,origin_airport,destination_airport",
                             supabase_url, supabase_key)

    if args.skip_hotels:
        log("\n=== Hotels (skipped) ===")
    else:
        log("\n=== Hotels ===")
        hotel_rows = scan_hotels(args.dry_run, supabase_url, supabase_key)
        log(f"Found {len(hotel_rows)} hotel/date price(s)")
        if not args.dry_run:
            upsert_supabase("hotel_prices", hotel_rows, "hotel_name,stay_date", supabase_url, supabase_key)

    log("\nDone." if not args.dry_run else "\n[dry-run] Done — nothing written, no paid calls made.")


if __name__ == "__main__":
    main()
