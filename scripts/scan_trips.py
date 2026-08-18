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
DEFAULT_ARRIVAL_CUTOFF = "11:00"
DEFAULT_DEPARTURE_WINDOW = ("21:00", "23:59")
ROLLING_WINDOW_DAYS = 61  # matches the hotel scraper's own default price_n_days
# A first real run at 0.2s between calls hit Duffel's rate limit almost immediately (nearly all
# 225 searches came back 429). 1.0s is untested but a much safer starting point — tune this
# down carefully if it turns out to be overly conservative, watching for 429s in the log either way.
DUFFEL_REQUEST_DELAY = 1.0

# Fixed set — do not expand to other London airports (Gatwick/Heathrow/City) via config.
ORIGIN_AIRPORTS = ["SEN", "LTN", "STN"]
# CDG/ORY always scanned; BVA always scanned too (server-side) so the data exists whenever the
# client's "include Beauvais" checkbox gets turned on — the checkbox only controls whether BVA
# rows are *displayed*, not whether they're fetched.
DESTINATION_AIRPORTS = ["CDG", "ORY", "BVA"]

# Real hotels confirmed (address + live Booking.com URL both checked at implementation time) to
# sit on Rue Saint-Honoré between Place de la Concorde and the Louvre — NOT the full length of
# the street out toward Les Halles. Verify/update this list if hotel results look wrong or
# empty; two candidates that seemed promising by name got dropped after checking further —
# "Hôtel Londres Saint-Honoré" is actually on Rue Saint-Roch (a side street, not Rue
# Saint-Honoré itself), and "Hôtel Royal Saint-Honoré" (221 rue Saint-Honoré, otherwise a great
# match) closed for renovation in May 2025 — hotel names in this area lean on the street's
# cachet loosely, so always confirm the actual street address before adding one here.
PINNED_HOTELS = [
    {
        "name": "Hôtel Louvre Saint-Honoré",  # 141 rue Saint-Honoré, 75001
        "url": "https://www.booking.com/hotel/fr/louvresainthonore.html",
    },
    {
        "name": "Le Relais Saint-Honoré",  # 308 rue Saint-Honoré, 75001
        "url": "https://www.booking.com/hotel/fr/lerelaissainthonore.html",
    },
]

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
        return rows

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
            "outbound_departure": out_seg[0]["departing_at"],
            "outbound_arrival": out_seg[-1]["arriving_at"],
            "outbound_airline": out_seg[0].get("operating_carrier", {}).get("name"),
            "return_departure": ret_seg[0]["departing_at"],
            "return_arrival": ret_seg[-1]["arriving_at"],
            "return_airline": ret_seg[0].get("operating_carrier", {}).get("name"),
            "total_price": float(cheapest["total_amount"]),
            "currency": cheapest.get("total_currency", "GBP"),
        })
        time.sleep(DUFFEL_REQUEST_DELAY)
    return rows


def scan_hotels(dry_run):
    rows = []
    if dry_run:
        for hotel in PINNED_HOTELS:
            log(f"  [dry-run] would scrape {hotel['name']} ({hotel['url']}) for "
                f"{ROLLING_WINDOW_DAYS} days from today")
        return rows

    import bookingcom  # vendored — see scripts/vendor/bookingcom.py for provenance/license

    async def run():
        for hotel in PINNED_HOTELS:
            log(f"  scraping {hotel['name']}...")
            try:
                result = await bookingcom.scrape_hotel(
                    hotel["url"], checkin=date.today().isoformat(), price_n_days=ROLLING_WINDOW_DAYS
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
                rows.append({
                    "hotel_name": hotel["name"],
                    "booking_url": hotel["url"],
                    "stay_date": stay_date.isoformat(),
                    "nightly_price": float(day["avgPriceFormatted"]),
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-month", default=None, help="YYYY-MM, defaults to a rolling window")
    parser.add_argument("--arrival-cutoff", default=DEFAULT_ARRIVAL_CUTOFF)
    parser.add_argument("--departure-window-start", default=DEFAULT_DEPARTURE_WINDOW[0])
    parser.add_argument("--departure-window-end", default=DEFAULT_DEPARTURE_WINDOW[1])
    args = parser.parse_args()

    duffel_key = os.environ.get("DUFFEL_KEY")
    scrapfly_key = os.environ.get("SCRAPFLY_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY")

    if not args.dry_run:
        missing = [n for n, v in [("DUFFEL_KEY", duffel_key), ("SCRAPFLY_KEY", scrapfly_key),
                                   ("SUPABASE_URL", supabase_url), ("SUPABASE_ANON_KEY", supabase_key)] if not v]
        if missing:
            log(f"Missing required env vars: {', '.join(missing)} (or pass --dry-run)")
            sys.exit(1)

    log("=== Flights ===")
    flight_rows = scan_flights(duffel_key, args.dry_run, args.arrival_cutoff,
                                args.departure_window_start, args.departure_window_end, args.target_month)
    log(f"Found {len(flight_rows)} valid flight combination(s)")
    if not args.dry_run:
        upsert_supabase("flight_prices", flight_rows, "outbound_date,origin_airport,destination_airport",
                         supabase_url, supabase_key)

    log("\n=== Hotels ===")
    hotel_rows = scan_hotels(args.dry_run)
    log(f"Found {len(hotel_rows)} hotel/date price(s)")
    if not args.dry_run:
        upsert_supabase("hotel_prices", hotel_rows, "hotel_name,stay_date", supabase_url, supabase_key)

    log("\nDone." if not args.dry_run else "\n[dry-run] Done — nothing written, no paid calls made.")


if __name__ == "__main__":
    main()
