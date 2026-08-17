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

### Tab state lives on `<body data-tab="...">`, not just in JS

`switchTab()` sets `document.body.dataset.tab`, and CSS keys off it — e.g. the docked search
bar only shows via `body.dock-open[data-tab="prices"] .search-dock{display:block;}`. The
`<body>` tag **must** carry `data-tab="prices"` in the static HTML itself (not only set at
runtime), matching `id="tabPrices"` already having `class="tab-btn active"` in the markup —
otherwise, on a completely fresh page load before any tab is ever clicked, the attribute is
simply absent and the entire docked search input (the app's main entry point) is invisible.
This exact regression shipped invisibly for a while because every manual/automated test
happened to click some other tab first, incidentally setting the attribute as a side effect.

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

Because calls are sequential and each takes a moment, the user can easily open a new product
before a previous one's image search resolves. `trySerperFill`/`tryImageFallback` used to guard
every step with `if (current.catalogId !== id) return;`, which bailed out of the whole
in-flight chain the instant that happened — silently dropping the image for the product it
belonged to. The fix: the image portion of both functions now always calls
`updateCatalogEntry(id, { image_url: img })` regardless of what's on screen, and only
conditionally touches the live `<img>`/`current.imageUrl` when `current.catalogId === id`.
Persistence and on-screen rendering are deliberately decoupled for this reason — don't
reintroduce a single guard that does both. (`applyName`/`applyPrice` inside `trySerperFill`
stay screen-only-guarded on purpose: unlike the image, they read/write live DOM fields, so
applying them to a stale id would mean writing into fields that belong to whatever's on screen
now, which would corrupt the wrong product's name/price instead.)

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

Schema has an `image_url` column (added later — persists whatever image Apify's `og:image` or
a Serper Images fallback found, so it survives reopening the entry) but no `manual_mode` or
`rate` column: manual-mode styling on reopen is inferred by looking up the stored `brand` name
against `BRANDS[...].scrapeBlocked`, and reopening an old entry fetches a *fresh* exchange
rate rather than restoring a stale historical one. `sku` is `getSku(url)` — just the URL's
last path segment — computed for every brand now, not only Dior/LV (that's still a separate,
localStorage-only concept: the known-SKU *cache*, below).

**Until the `image_url` column actually exists in the live Supabase table, every
`updateCatalogEntry` call made through `buildCatalogPatch()` fails outright** — PostgREST
rejects the entire PATCH (not just the unknown column) with `PGRST204`, logged via
`console.error` but never surfaced in the UI. Since `buildCatalogPatch()` is what
`scheduleCatalogSync()` sends after every `prodName`/`frPrice`/`ukPrice`/`quantity`/`saleVal`
edit, this silently breaks *all* live-editing of an existing catalog row, not just its image —
confirmed live: editing the name/prices on a freshly-created entry left the row exactly as
the initial `addCatalogEntry` insert, even though the UI showed the edited values. Writes that
build their own narrower patch object without `image_url` (e.g. the status-pill's
`{ status: next }`, or tagging a trip with `{ trip_label }`) are unaffected. Check with a
lightweight probe (`select=id,image_url&limit=1` against `/rest/v1/products`) before assuming
catalog editing works end-to-end; the anon key can't run the `alter table` itself.

Re-checking the same product (same brand + `sku`) updates the existing row instead of inserting
a new one — `findExistingBySku(brandLabel, sku)` runs before both `runCheck` and
`showManualMode` decide whether to `addCatalogEntry` or `updateCatalogEntry`. This matters
because pasting the same link twice used to silently create a second row every time; a real
example of exactly that (one Louis Vuitton item checked 4 times in one session, 3 of the rows
completely empty) was found and cleaned up in the live catalog while building this. When
merging in a fresh scrape's result (`runCheck` only — `showManualMode` never overwrites with a
"fresh" value since it doesn't scrape), a `null`/failed price from *this* check must never
clobber a good price the existing row already had — always merge as
`info.ukPrice != null ? info.ukPrice : existing.uk_retail`, never just `info.ukPrice`.

Catalog rows (`buildCatalogRow`, shared by the Catalog tab, Trip Detail, and the catalog
picker) intentionally show as little as possible: no brand text, no status pill (status is only
editable from the full card now). The sub-line is just the bare figures in a fixed order — FR
retail, UK retail, sale price, then profit (green if positive, red if negative via
`trip-net-pos`/`trip-net-neg`, the same classes Trips uses) — no "FR price:"-style labels, no
trip label, no date.

### Trips tab (Catalog `trip_label` values shown as trips, plus its own `trips` table)

There's no per-check "trip label" input on the Prices card (removed — not every check is for a
trip). A trip is any `trip_label` value that is *either* (a) present as a row in its own
`trips` table — i.e. deliberately created via the "New trip" modal — *or* (b) a value already
on some catalog row that matches `TRIP_LABEL_RE` (`/^paris\s+[a-z]+\s+\d{4}$/i`, e.g.
`"PARIS JAN 2025"`), which is how the old Excel-imported trips show up despite never having a
`trips` row of their own. Older one-off labels that don't fit either path (`"1st time"`,
`"SALES MISSED PARIS MID JULY 202"`, `"Sheet1 (legacy)"`) still show up fine in Catalog
search/filter, they just aren't treated as a trip. `renderTrips()` unions both sources before
rendering the list.

Revenue and profit (both in the compact Trips-tab list and on the Trip Detail page) are computed
client-side from `catalogRows`, summing only rows with `status === 'sold'` (stock hasn't sold
yet, a missed sale made nothing) — `revenue` is `sale_price * quantity`, `profit` prefers
`total_profit` and falls back to `profit * quantity` when it's null (older imported rows may not
have `total_profit` populated).

Flight and hotel cost are trip-level, not per-product, so they live in their own `trips` table
(`trip_label` primary key, `flight_cost`, `hotel_cost`) rather than as columns on `products` —
see `loadTripCosts`/`saveTripCost`. **This table does not exist by default and must be created
manually in the Supabase SQL editor** (the anon key can't run DDL):

```sql
create table trips (
  trip_label text primary key,
  flight_cost numeric default 0,
  hotel_cost numeric default 0,
  updated_at timestamptz default now()
);
alter table trips enable row level security;
create policy "allow all" on trips for all using (true) with check (true);
```

Until that table exists: `loadTripCosts` fails closed (logs the error, returns `{}`) so
Excel-imported trips still render with revenue/profit and just a blank cost field; "New trip"
surfaces the failure via `alert()` and deliberately leaves its modal open so the user can retry
once the table exists, rather than silently discarding what they typed.

**Trip Detail** (`#tripDetailView`, opened via `openTripDetail(label)` from either the Trips
list or right after creating a trip) is a separate screen from the four bottom-tab views — it's
not in the `views` map that `switchTab` cycles through, so `switchTab` explicitly removes its
`active` class on every tab change to make sure it doesn't linger on top of whichever tab was
actually selected. From here you can add products two ways:
- **"+ Paste a link"** reveals an inline URL input reusing `startCheckFromUrl()` — the same
  brand-detection/scrape logic as the main dock's checkBtn (factored out into that shared
  function for exactly this reuse). Before calling it, `current.tripLabel` is set to the open
  trip's label and the app switches to the Prices tab to show the resulting card — the card
  itself doesn't change layout, it just gains a visible "Add to trip →" button
  (`#tripAddRow`/`#addToTripBtn`, toggled in `renderCard()` off `!!current.tripLabel`) that,
  when clicked, tags the just-created catalog row with `{ trip_label }` and returns to Trip
  Detail. `current.tripLabel` is cleared (a) on that click, (b) by `switchTab()` whenever the
  target isn't `'prices'`, and (c) at the top of `reopenCatalogEntry()` — opening an
  *existing* item from anywhere should never be mistaken for "currently adding to a trip".
- **"+ From catalog"** opens a picker (`#catalogPickerOverlay`) listing existing catalog rows
  not already tagged with this trip; tapping one just calls
  `updateCatalogEntry(id, { trip_label })` directly, no card involved.

The product card's "Your sale price" field (`#saleVal`) is a plain editable `<input>`, not a
computed-only span — this is deliberate, since a trip is where the *actual* final sale price
gets recorded, not just the calculator's suggestion. `recalc()` keeps writing the freshly
suggested price into it on every input change **until the user types into it themselves**
(tracked by the module-level `saleOverridden` flag, reset to `false` on every fresh
`renderCard()` call); once overridden, `profitSale`/`totalProfit`/the "vs UK RRP" discount all
switch to using that typed figure instead of the theoretical one, but `resellerPrice` stays
based on the un-overridden suggestion (reseller/B2B pricing is a separate business rule,
unrelated to what one customer happened to pay). Reopening an existing entry via
`reopenCatalogEntry()` treats its stored `sale_price` the same way — pre-fills it and marks it
overridden — so revisiting an item to fix its FR/UK RRP never silently nudges an
already-recorded sale price.

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
