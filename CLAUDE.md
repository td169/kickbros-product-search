# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KICKBROS Product Search is a single-file client-side tool for a designer resale/arbitrage
business. You paste a product link (Dior, Louis Vuitton, Gucci, Moncler, Balenciaga, Goyard),
and it finds the matching FR and UK listings, gets both prices, and works out what to charge
after the French détaxe (tax) refund — cost, sale price, reseller price, and profit.

The entire app is `index.html` — no build step, no dependencies, no package.json. It's a static
page with inline `<style>` and one `<script>` IIFE, deployed as-is.

## Commands

There is no build/lint/test tooling. To validate a change:

- **Syntax-check the script** (it's inline, so extract it first):
  ```bash
  python3 -c "
  import re
  html = open('index.html').read()
  m = re.search(r'<script>(.*)</script>', html, re.S)
  open('/tmp/check.js', 'w').write(m.group(1))
  " && node --check /tmp/check.js
  ```
- **Run it locally**: `open index.html` (or any static file server). It works from `file://`
  directly — all external calls (Apify, Frankfurter, Serper) have CORS enabled.
- **Deploy**: push to `main`. GitHub Pages serves this repo directly from the branch root at
  `https://td169.github.io/kickbros-product-search/` — no separate deploy step. After pushing,
  the Pages build lags a few seconds; poll `gh api repos/td169/kickbros-product-search/pages/builds/latest`
  for `"status":"built"` before assuming a change is live.
- **UI testing**: no test framework exists. Verification in this project has been done by
  driving a headless Playwright browser against `index.html` (install ad hoc with
  `npm install playwright` in a scratch dir — it isn't a project dependency) and asserting on
  DOM state, since this is a financial calculator where silent wrong numbers are the main risk.

## Architecture

### Two independent data-acquisition paths, chosen per brand

`BRANDS` (top of the script) maps each domain to a `build(url)` function that derives the
matching UK/FR URL from whichever one the user pasted, plus a `scrapeBlocked` flag.

- **Non-blocked brands** (Gucci, Moncler, Balenciaga, Goyard) → `runCheck` → `extractInfo` →
  `runApifyScrape`, which POSTs a `pageFunction` to Apify's `web-scraper` actor (user's own
  token) to run a real headless browser against both URLs. **`pageFunctionSource()` is
  serialized via `.toString()` and executed remotely inside Apify's sandbox** — it has no access
  to anything in the outer script's closure and must stay fully self-contained (its own regexes,
  no shared helpers).
- **Blocked brands** (Dior, Louis Vuitton) → `showManualMode`. Direct scraping was tested and
  confirmed blocked at the network level for both — Apify with a residential proxy, a plain
  fetch with browser headers, and a third-party metadata API (microlink.io) all got 403s. These
  skip the scrape attempt entirely and instead: (a) build both links instantly so the user can
  open them, (b) let the user type name/price directly into always-editable fields, and (c) if a
  Serper API token is set, best-effort auto-suggest name/image/price via `trySerperFill` /
  `tryImageFallback`.

### Serper calls must run strictly sequentially, never concurrently

This is the single most important non-obvious constraint in the codebase. Firing more than one
Serper request at a time — even two harmless `/search` calls with nothing else going on — was
empirically found to make Google's own ranking unstable for the "exact URL as query" pattern
this app relies on, sometimes returning a completely different product. `trySerperFill` and
`tryImageFallback` await each call before starting the next; do not refactor these back to
`Promise.all`.

Image URLs from Serper's `/search`/`organic` results point at the source site's own CDN and are
blocked by the same bot protection as the page itself. `serperImageSearch` uses the `/images`
endpoint's `thumbnailUrl` (hosted on `gstatic.com`) instead of `imageUrl`, since that's the one
that actually loads.

### Price parsing

`parseMoney` must handle UK format (`1,090.00`, comma thousands / dot decimal) and FR format
(`1 090,00`, space thousands / comma decimal) — it disambiguates by treating whichever of `,`/`.`
appears last in the string as the decimal separator. The regexes that extract a price substring
from scraped text (in `pageFunctionSource`) or a Serper snippet (in `serperLookup`) must allow
spaces inside the digit run for the same reason, or FR prices over ~€1,000 silently truncate.

### Pricing engine (`recalc`)

FR price → cost after détaxe refund → sale price at target margin → **capped by `SAVE_CAPS`**, a
table of max-allowed customer savings (in £) by UK RRP band → reseller price → profit at each.
`SAVE_CAPS` is a real business rule, not a smoothed curve — it has an intentional discontinuity
where the £2,001–£3,000 band's cap (£230) is lower than the £1,501–£2,000 band's (£270). Don't
"fix" this.

### Catalog (Supabase `products` table — shared across devices)

Replaced the old localStorage-only History. Every check (scraped or manual) is inserted via
`addCatalogEntry` and live-synced via `updateCatalogEntry`/`scheduleCatalogSync` as the user
types — see `recalc()`'s tail and the `prodName` input listener. `current.catalogId` must be
set to `null` before rendering a *new* check's card, or edits get written into the previous
row. `current.nameSource` (`'guess' | 'scrape' | 'serper' | 'user'`) exists purely to stop
async Serper results from clobbering a name the user already typed — it's in-memory app state
only, **not** synced to Supabase (the schema originally had a `name_source` column for this;
removed as unhelpful data to have sitting in the catalog, but the JS variable itself is still
load-bearing for the clobber-prevention logic and must stay).

Writes are debounced (500ms via `scheduleCatalogSync`), not fired per keystroke — Supabase
inserts return the row (`select().single()`) so `current.catalogId` can be set from the
response; there's no client-generated id anymore (`gen_random_uuid()` is server-side).

The client variable is named `db`, not `supabase` — the CDN script's global is
`window.supabase`, so naming the instance the same would shadow it and break
re-initialization when the user edits the Settings fields.

Schema has no `image_url`, `manual_mode`, or `rate` column: images are UI-only (never
persisted), manual-mode styling on reopen is inferred by looking up the stored `brand` name
against `BRANDS[...].scrapeBlocked`, and reopening an old entry fetches a *fresh* exchange
rate rather than restoring a stale historical one. `sku` is `getSku(url)` — just the URL's
last path segment — computed for every brand now, not only Dior/LV (that's still a separate,
localStorage-only concept: the known-SKU *cache*, below).

### Known-SKU cache (`localStorage`, separate from the Catalog)

Unrelated to the Supabase catalog — this is a small local cache keyed by brand+SKU
(`getSkuEntry`/`saveSkuEntry`) so re-checking the same Dior/LV item later pre-fills instantly.
Only ever populated for `scrapeBlocked` brands (see `syncSkuCache`, gated on `current.sku` /
`current.brandKey`, which are only set inside `showManualMode`). Deliberately not merged into
the Supabase catalog — it's a UX shortcut for the manual-entry form, not a data record.

### Migration script (`migration/import_trips.py`)

One-time, not wired into the app. Imports the old Excel-based trip tracking
(`Personal Shopping.xlsx`, one sheet per trip) into the `products` table. Reads
`SUPABASE_URL`/`SUPABASE_ANON_KEY` from the environment (never hardcode real credentials into
this file — it's committed to a public repo). Has an explicit `TRIP_SHEETS` allowlist rather
than pattern-matching sheet names — the workbook has draft/duplicate/unrelated sheets
(`Sheet1`, `GOYARD`, `Copy of PARIS JAN 2025`, `PARIS BUDGET 2025`, `Marketing`, ...) that
don't match the trip-sheet column layout or would double-import real data. Run
`--dry-run` first — it prints per-trip row/status counts and every row where brand inference
failed, without writing anything.

### Tokens

Apify (`kickbros_apify_token`), Serper (`kickbros_serper_token`), and the Supabase project
URL/anon key (`kickbros_supabase_url` / `kickbros_supabase_anon_key`) all live in
`localStorage` only, entered by the user in the settings panel. This repo is **public** —
never hardcode a real value for any of these into `index.html`, the migration script, or a
commit. (The Supabase anon key is safe to expose client-side by Supabase's own design — access
is controlled by the `products` table's row-level-security policy, not by keeping the key
secret — but it's still kept out of the committed source rather than hardcoded, for
consistency with the other tokens and in case the policy is tightened later.)
