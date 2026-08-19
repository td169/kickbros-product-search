#!/usr/bin/env python3
"""
One-time setup script: resolves each hotel in HOTELS below (name + tier, from Temi's own list)
to a real Booking.com URL and a representative photo, then upserts into Supabase's
`tracked_hotels` table. Not part of the recurring scan — run this once when the hotel list
changes, not on every scheduled price scrape (see scripts/scan_trips.py, which reads from
`tracked_hotels` afterward).

Uses Booking.com's own search (via the vendored bookingcom.scrape_search — same one used
elsewhere, see scripts/vendor/bookingcom.py for provenance) rather than a generic web search,
since these are small individual-listing-style properties that don't reliably surface through
Google — Booking.com's own search is the authoritative source for its own inventory.

Usage:
    SCRAPFLY_KEY=... SUPABASE_URL=... SUPABASE_ANON_KEY=... python3 scripts/resolve_hotels.py
    SCRAPFLY_KEY=... python3 scripts/resolve_hotels.py --debug-one "Hôtel Aoriste & Spa"
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

HOTELS = [
    # Luxury
    ("Stylish Flat Close to Louvre & Palais-Royal", "luxury"),
    ("PoeticStay - Louvre - Aboukir", "luxury"),
    ("Hôtel Aoriste & Spa", "luxury"),
    ("New studio - 2P - Place-Vendome", "luxury"),
    ("Yuna Saint-Honoré - Serviced Apartments", "luxury"),
    ("Stylish Apartment - 1BR-4P - Vendôme Square", "luxury"),
    ("Habitat Parisien - Louvre Rivoli", "luxury"),
    ("Cozy Apartment a c 1BR 4P - Tuileries", "luxury"),
    ("Saint-Honoré - Vendôme - Luxury Designer Flat", "luxury"),
    # Standard
    ("Hôtel De Castiglione", "standard"),
    ("Normandy Le Chantier", "standard"),
    ("4 Guests Flat Palais Royal 1", "standard"),
    ("Lion d'Or Paris Hotel", "standard"),
    ("Hôtel Gaillon Opera", "standard"),
    ("Hôtel Thérèse", "standard"),
    ("Hotel Lumen Paris Louvre", "standard"),
    ("Hôtel Le Pradey", "standard"),
    ("Hôtel Molière", "standard"),
]


def log(msg):
    print(msg, flush=True)


def normalize(s):
    return "".join(c for c in s.lower() if c.isalnum())


async def resolve_one(name):
    import bookingcom
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    results = await bookingcom.scrape_search(query=f"{name}, Paris", checkin=today, checkout=tomorrow, max_pages=1)
    if not results:
        return None, f"no search results at all for {name!r}"

    # Try to find a result whose own name actually matches — scrape_search resolves the query
    # to a *destination* first, which for an exact small-listing name often still just falls
    # back to "Paris" broadly, returning a big generic list rather than the one specific
    # property. Only trust a match if the returned property's own name is a close match.
    target = normalize(name)
    best = None
    for r in results:
        try:
            prop_name = r["basicPropertyData"].get("pageName") or ""
            display_name = r.get("displayName", {}).get("text", "")
        except (KeyError, AttributeError, TypeError):
            display_name = ""
        cand = normalize(display_name)
        if cand and (cand in target or target in cand):
            best = r
            break
    if not best:
        return None, f"none of {len(results)} result(s) name-matched {name!r} closely enough"
    return best, None


def extract_url_and_image(result):
    url = result.get("basicPropertyData", {}).get("pageName")
    # Fall back to whatever URL-shaped field is present; the exact key varies by result shape.
    photo = None
    photos = result.get("basicPropertyData", {}).get("photos")
    if photos:
        main = photos.get("main", {})
        photo = (main.get("highResUrl") or main.get("lowResUrl") or {}).get("relativeUrl")
    return url, photo


async def main_async(args):
    import bookingcom

    if args.debug_one:
        result, err = await resolve_one(args.debug_one)
        if err:
            log(f"FAILED: {err}")
            return
        url, photo = extract_url_and_image(result)
        log(f"MATCHED displayName={result.get('displayName')}")
        log(f"  pageName/url={url}")
        log(f"  photo={photo}")
        log(f"  location={result.get('location', {}).get('displayLocation')}")
        log(f"  keys={list(result.keys())}")
        log(f"  basicPropertyData keys={list(result.get('basicPropertyData', {}).keys())}")
        return

    rows = []
    for name, tier in HOTELS:
        log(f"resolving {name!r} ({tier})...")
        try:
            result, err = await resolve_one(name)
        except Exception as e:
            log(f"  error: {e}")
            continue
        if err:
            log(f"  {err} -- SKIPPED, needs manual URL lookup")
            continue
        url, photo = extract_url_and_image(result)
        log(f"  -> url={url} photo={'yes' if photo else 'no'}")
        rows.append({"name": name, "tier": tier, "booking_url": url, "image_url": photo})

    if not args.dry_run and rows:
        supabase_url = os.environ["SUPABASE_URL"]
        supabase_key = os.environ["SUPABASE_ANON_KEY"]
        resp = requests.post(
            f"{supabase_url.rstrip('/')}/rest/v1/tracked_hotels?on_conflict=name",
            headers={
                "apikey": supabase_key, "Authorization": f"Bearer {supabase_key}",
                "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            data=json.dumps(rows), timeout=30,
        )
        log(f"upsert: {resp.status_code} {'' if resp.ok else resp.text[:300]}")

    log(f"\nResolved {len(rows)}/{len(HOTELS)}. Anything skipped needs a manual booking_url added directly in Supabase.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug-one", default=None, help="Test resolution for a single hotel name, print raw match, write nothing")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
