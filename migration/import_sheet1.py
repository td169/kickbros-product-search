#!/usr/bin/env python3
"""
One-time migration: import the older "Sheet1" master list from "Personal Shopping.xlsx" into
the Supabase `products` table. Separate script from import_trips.py because Sheet1 has a
different, older column layout (RRP / RRP AFTER / RRP UK / RRP AFTER / SALE PRICE / PROFIT /
... / RESELLER PRICE / ... / Website Price / URL) — no trip_label, no Quantity / Total Profit /
Total Spend columns. It looks like it predates the per-trip tracking format.

Run import_trips.py FIRST — this script deduplicates against whatever is already in Supabase,
so it needs the trip-sheet rows to already be there to compare against.

Deduplicates two ways:
  - within Sheet1 itself: exact match on (normalized name, eu_retail, uk_retail). Same-named
    items with genuinely different prices (e.g. two different "Chanel Trainers" checks) are
    kept — only true repeats are dropped.
  - against Supabase: normalized product_name match against every row already there (from the
    trip-sheet import). Sheet1 predates the trip sheets, so anything that shows up in both is
    the same real-world purchase, already captured with better data (trip label, quantity,
    status) by the trip-sheet import.

Goyard rule: sale_price is set to eu_retail, not the sheet's own SALE PRICE column — Goyard
items sell at EU/French retail price rather than through the usual détaxe-margin formula (the
sheet's own Goyard rows already showed #VALUE!/#DIV/0! errors from trying to force them
through it).

Usage:
    SUPABASE_URL=https://xxxxx.supabase.co SUPABASE_ANON_KEY=eyJ... \
        python3 import_sheet1.py "/path/to/Personal Shopping.xlsx" [--dry-run]
"""

import json
import os
import re
import sys
import urllib.request

import openpyxl

from import_trips import BRAND_KEYWORDS, infer_brand, clean_num, insert_rows


def normalize(name):
    return re.sub(r'\s+', ' ', (name or '')).strip().lower()


def fetch_existing_names(url, key):
    endpoint = url.rstrip('/') + '/rest/v1/products?select=product_name&limit=2000'
    req = urllib.request.Request(endpoint, headers={'apikey': key, 'Authorization': f'Bearer {key}'})
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read())
    return {normalize(r['product_name']) for r in data if r.get('product_name')}


def parse_sheet1(ws):
    rows = list(ws.iter_rows(values_only=True))
    parsed = []
    seen_keys = set()
    dup_within = []

    for r in rows[3:]:  # header is row 3 (index 2); data starts row 4
        name = r[0] if len(r) > 0 else None
        if not name or not str(name).strip():
            continue
        name = str(name).strip()

        eu_retail = clean_num(r[1] if len(r) > 1 else None)
        eu_after_tax = clean_num(r[2] if len(r) > 2 else None)
        uk_retail = clean_num(r[3] if len(r) > 3 else None)
        uk_after_tax = clean_num(r[4] if len(r) > 4 else None)
        sale_price = clean_num(r[5] if len(r) > 5 else None)
        profit = clean_num(r[6] if len(r) > 6 else None)
        reseller_price = clean_num(r[11] if len(r) > 11 else None)

        key = (normalize(name), eu_retail, uk_retail)
        if key in seen_keys:
            dup_within.append(name)
            continue
        seen_keys.add(key)

        brand = infer_brand(name)
        if brand == 'Goyard':
            sale_price = eu_retail

        parsed.append({
            'brand': brand,
            'product_name': name,
            'trip_label': 'Sheet1 (legacy)',
            'eu_retail': eu_retail,
            'eu_after_tax': eu_after_tax,
            'uk_retail': uk_retail,
            'uk_after_tax': uk_after_tax,
            'sale_price': sale_price,
            'profit': profit,
            'quantity': 1,
            'total_profit': profit,
            'total_spend': eu_retail,
            'reseller_price': reseller_price,
            'status': 'stock',
        })

    return parsed, dup_within


def main():
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']
    if not args:
        print(__doc__)
        sys.exit(1)
    xlsx_path = args[0]

    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_ANON_KEY')
    if not url or not key:
        print('Set SUPABASE_URL and SUPABASE_ANON_KEY env vars (needed even for --dry-run, to check for duplicates already in Supabase).')
        sys.exit(1)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    rows, dup_within = parse_sheet1(wb['Sheet1'])

    print(f'Parsed {len(rows) + len(dup_within)} named rows from Sheet1')
    if dup_within:
        print(f'\nDropped {len(dup_within)} exact duplicate rows within Sheet1 itself:')
        for n in dup_within:
            print(f'    {n!r}')

    existing = fetch_existing_names(url, key)
    to_insert, dup_vs_supabase = [], []
    for row in rows:
        if normalize(row['product_name']) in existing:
            dup_vs_supabase.append(row['product_name'])
        else:
            to_insert.append(row)

    if dup_vs_supabase:
        print(f'\nSkipped {len(dup_vs_supabase)} rows already present in Supabase (from the trip-sheet import):')
        for n in dup_vs_supabase:
            print(f'    {n!r}')

    unmatched = [r['product_name'] for r in to_insert if not r['brand']]
    if unmatched:
        print(f'\n{len(unmatched)} rows with no brand match (imported with brand left blank for manual review):')
        for n in unmatched:
            print(f'    {n!r}')

    goyard_count = sum(1 for r in to_insert if r['brand'] == 'Goyard')
    print(f'\n{"=" * 60}')
    print(f'{len(to_insert)} rows to insert ({goyard_count} Goyard, sale_price set to eu_retail)')
    print(f'{"=" * 60}')

    if dry_run:
        print('\n--dry-run: nothing written to Supabase.')
        return

    print(f'\nInserting {len(to_insert)} rows into Supabase...')
    CHUNK = 50
    for i in range(0, len(to_insert), CHUNK):
        chunk = to_insert[i:i + CHUNK]
        status = insert_rows(url, key, chunk)
        print(f'  rows {i+1}-{i+len(chunk)}: HTTP {status}')
    print('Done.')


if __name__ == '__main__':
    main()
