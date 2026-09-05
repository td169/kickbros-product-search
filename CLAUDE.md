# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KICKBROS Product Search is a single-file client-side tool for a designer resale/arbitrage
business. You paste a product link (Dior, Louis Vuitton, Gucci, Moncler, Balenciaga, Goyard,
Hermès, Prada, Loro Piana, Chanel, Burberry), and it finds the matching FR and UK listings, gets
both prices, and works out what to charge after the French détaxe (tax) refund — cost, sale
price, reseller price, and profit.

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

- **Non-blocked brands** (Gucci, Moncler, Balenciaga, Goyard, Hermès, Burberry) → `runCheck` →
  `extractInfo` → `runApifyScrape`, which POSTs a `pageFunction` to Apify's `web-scraper` actor
  (user's own token). **`pageFunctionSource()` is serialized via `.toString()` and executed
  remotely inside Apify's sandbox** — it has no access to anything in the outer script's closure
  and must stay fully self-contained (its own regexes, no shared helpers).
  **`context.page` is NOT a real Puppeteer/Playwright page object for this actor — confirmed
  against Apify's own docs and a live run.** An earlier version of `pageFunctionSource()` used
  `context.page.title()`/`context.page.$eval()`/`context.page.evaluate()`, all of which threw
  "Cannot read properties of undefined (reading 'title')" on every single real invocation —
  meaning `runApifyScrape` silently failed for every non-blocked brand, always falling straight
  through to whatever `trySerperPriceFallback` could separately find instead, with the scrape
  itself contributing nothing. This actor runs `pageFunction` directly inside the browser page,
  so DOM globals (`document`, `window`) are used straight away, and waiting for content to settle
  uses the actor's own `context.waitFor(predicate, { timeoutSecs })` in place of Puppeteer's
  `page.waitForFunction`. Re-verified live (Burberry UK/FR, Hermès UK — real `structuredPrice`
  from each page's schema.org JSON-LD) that the corrected version actually returns real data.
- **Blocked brands** (Dior, Louis Vuitton, Prada, Loro Piana, Chanel) → `showManualMode`. Direct
  scraping was tested and confirmed blocked at the network level for Dior/LV — Apify with a
  residential proxy, a plain fetch with browser headers, and a third-party metadata API
  (microlink.io) all got 403s. Prada, Loro Piana, and Chanel were added later on different
  evidence per brand (see below) — Loro Piana and Chanel the same way as Dior/LV (plain
  fetch/curl 403s outright on every URL variant tried, confirmed live), Prada on a subtler
  signal: a plain fetch/curl gets the real page fine, but a real headless browser hangs or
  errors on every attempt, consistent with anti-automation blocking that specifically targets
  headless-browser fingerprints (which is what Apify's actor looks like to the site) rather than
  raw HTTP requests — **unconfirmed against a real Apify run, flip `scrapeBlocked` off for Prada
  if a live scrape actually succeeds.** Blocked brands skip the scrape attempt entirely and
  instead: (a) build both links instantly so the user can open them, (b) let the user type
  name/price directly into always-editable fields, and (c) if a Serper API token is set,
  best-effort auto-suggest name/image/price via `trySerperFill` / `tryImageFallback`.
- **Scrape-first-then-Serper, for every brand**: even a non-blocked brand's Apify scrape can
  come back without a price on one or both sides (bot-protection hit at runtime, page layout
  changed, etc). `runCheck` now always follows up with `trySerperPriceFallback` for whichever
  side(s) are still missing a price after the scrape — same Serper lookup manual-mode brands
  use, just price/name-only (never touches the image; a missing image is `tryImageFallback`'s
  job, and re-running an image search here could stomp a perfectly good scraped `og:image`).
  Manual-entry is the last resort now, not the first response to an incomplete scrape.
- **`runCheck`/`showManualMode` can both use a brand-specific `sku(url)` extractor** (see
  Chanel/Burberry below) instead of the generic `getSku` — `runCheck` now takes a `brandKey`
  parameter for exactly this (previously it only had `brandLabel`, which isn't enough to look up
  `BRANDS[brandKey].sku`).
- **Multi-stage, code-anchored Serper price/name lookup, used by both `trySerperFill` and
  `trySerperPriceFallback`**: `serperLookup` tries Google's organic `/search` results first
  (regex-extracting a `£`/`€` amount out of the matched result's title+snippet), then a
  `"<product code> <brand name>"` text-query retry if the URL-as-query search didn't land on a
  verified product page, then `serperShoppingLookup` (the `/shopping` endpoint) if organic still
  found no price. All three stages run strictly sequentially — see the "never concurrently" rule
  below.
  - **A same-domain result is never trusted just because it "looks like a product page."** The
    old heuristic (`pathname.length > 15`) matched a Dior *customer-service contact page* just as
    happily as the real product — confirmed live. `isOurProductPage(link)` now requires the
    candidate's own URL to actually contain `getSku(url)` (the product code) whenever one exists,
    which is far more specific; the path-length heuristic only survives as a fallback for the rare
    case where there's no code to check against at all.
  - **The exact URL as a search query doesn't always find what Google has actually indexed** —
    confirmed live on Dior, where the URL-as-query search surfaced nothing but homepage/category
    pages (no real product page at all, even same-domain) for one specific item, while a
    `"<product code> <brand name>"` text query found the exact right page. But that retry query
    has its own failure mode: it can rank a *different, unrelated same-brand product* above the
    actual target (confirmed live: Google's top same-domain result for `"<code> Dior"` was a
    different product's page entirely) — so its candidates are filtered to ones whose own URL
    contains the searched-for code before being considered at all, same principle as above.
  - **Title and price are always extracted from the same single winning result, never stitched
    across two different candidates.** An earlier version picked title from one query's result and
    price from another's independently — which let a bad match's title (Dior's own homepage)
    survive next to a price pulled from a completely different, unrelated candidate. Confirmed
    live that this combination produced a real-but-wrong price for the wrong product.
  - **Shopping is only ever queried using a name pulled from a *confirmed* product page** (i.e.
    `isOurProductPage` was true for it), or the raw URL if organic found nothing at all anywhere.
    Querying Shopping with an unconfirmed match's title (a homepage, a category page) risks a
    false "this merchant name looks like the brand" match against a completely unrelated product
    with a similar-sounding name — confirmed live: this is exactly how Dior briefly produced a
    real-but-wrong price for the wrong product, before this was fixed to require a confirmed page.
  - **Some product pages are indexed by Google with no price anywhere in the organic
    title/snippet text at all** (seen on certain LV items and on Prada) — Shopping's structured
    `price` field (sourced from the merchant's own feed, not scraped text) can still find one.
  - Verifying a Shopping result can't reuse hostname matching at all: confirmed live that every
    Shopping result's `link` is a Google-internal redirect (`google.com/search?ibp=oshop&...`),
    never the merchant's actual URL, for every brand tested. Serper does label each result with a
    `source` field (the merchant name as Google understands it, e.g. `"Prada"`,
    `"Loro Piana S.p.A."`) — `serperShoppingLookup` matches that against the brand label instead
    (via `normalizeBrandName`, which strips accents/case — distinct from the unrelated
    `normalizeForMatch` used by client duplicate-detection). Confirmed live that this correctly
    finds **nothing** for some items (e.g. a Chanel shoe with no Chanel-sourced Shopping listing
    at all, only resellers) — that's the intended outcome, not a bug: no verified listing means no
    price, never a reseller's possibly-different price passed off as one.
- **Per-field "still looking" indicator**: while a background price lookup (Serper organic or
  Shopping) is in flight for a specific side, a small "···" (`.price-dots`, `#frPriceLoading` /
  `#ukPriceLoading`) shows next to that field, toggled by `setPriceLoading(fieldId, bool)`. It's
  cleared the moment that attempt ends — found or not — so an empty price field with no dots next
  to it is the actual signal that every method was tried and none of them found a number, rather
  than the field just not having been looked up yet. `renderCard` resets both to hidden on every
  fresh render (new check or reopening an existing catalog entry), since the dots belong to
  whichever product is currently on screen, not to a stale in-flight lookup for a previous one.

### Not every brand's UK/FR link pair can be derived from one link

Most `build(url)` functions are a simple locale-segment swap (see Gucci/Moncler/Balenciaga/
Goyard), sometimes with a locale-specific word fixed up explicitly (LV's `/produits/` vs
`/products/`, Chanel's `/gb/fashion/` vs `/fr/mode/`). Chanel joins Prada in the
canonicalise-on-product-code group — confirmed live via search that the same code
(`G02819X01000C0204`) resolves under two completely different descriptive slugs per locale
(`ballet-flats-lambskin` vs `ballerines-agneau-metal`), so a locale swap that leaves the wrong
locale's slug in place is expected to still resolve. Unlike Prada's `.../p/<slug>/<code>`
though, Chanel's code comes *before* the slug (`.../p/<code>/<slug>/`), which means the generic
`getSku` (just the URL's last path segment) would land on the locale-specific slug instead of
the code — the two locales would then never dedupe to the same catalog row. `BRANDS.chanel` sets
its own `sku(url)` extractor (regex on `/p/<code>/`) for this reason; `showManualMode`/`runCheck`
both check for a brand-specific `sku` function before falling back to the generic `getSku`.

**Burberry** is a subdomain-locale brand (`uk.`/`fr.`, like Loro Piana) but — confirmed live via
curl — unlike Loro Piana, the trailing product code (`-p81047111`) IS shared across locales; only
the descriptive slug before it is translated (`long-castleford-trench-coat-p81047111` vs
`trench-long-castleford-p81047111`). Confirmed live that pasting the UK slug onto the `fr.`
subdomain unchanged still 200s and redirects to the correct French slug for the same code — same
canonicalise-on-code, tolerate-mismatched-slug pattern as Prada/Chanel — so `build(url)` is a
plain subdomain swap, no `findOtherLocaleLink` fallback needed. It also needs its own `sku(url)`
extractor for the same reason as Chanel (the shared code is a suffix, not the whole last path
segment) — and since Burberry is **not** `scrapeBlocked` (confirmed live: no 403s at the network
level, and a real Apify run returns a genuine `structuredPrice` on both locales), this is the
first brand where the `sku` override actually needs to be threaded through `runCheck`, not just
`showManualMode` — see `runCheck`'s new `brandKey` parameter above.

Two brands can't derive the UK/FR pair at all:

- **Hermès**: the descriptive URL slug is independently translated per product (e.g.
  `sandales-oran` vs `oran-sandal`) with no shared pattern, and — unlike Prada, which
  canonicalises on its trailing product code and tolerates a mismatched slug — Hermès 403s a
  locale-swapped URL that keeps the wrong locale's slug words (confirmed live), and 403s a
  slug-less/code-only URL too. There's no way to derive one locale's link from the other.
- **Loro Piana**: locale is a subdomain (`fr.`/`uk.`), and the two locales use *different*
  trailing product codes for what the site presents as the same item, not just different slug
  words — confirmed directly from real example links (`FAO4831_M15K` vs `FAR0854_B5VY`).

For these, `build(url)` deliberately returns an identical `{ uk, fr }` pair. `startCheckFromUrl`
already had a generic fallback for exactly this ("couldn't work out the FR/UK pair — paste both
manually below", reusing the `manualPanel`/`manualUk`/`manualFr` inputs) — rather than build
bespoke UI, these two brands just trigger it, but only as a last resort now:

- First, `startCheckFromUrl` tries `findOtherLocaleLink` — the same Google/Serper method used
  everywhere else in the app, searching for the pasted URL and looking through the results
  (organic first, then Shopping/sponsored listings) for one on the same site matching the
  *other* locale's `localePatterns` entry. If found, the pair is complete and it proceeds
  straight into `runCheck`/`showManualMode` — no user input needed at all.
- **If that comes back empty (no Serper token set, or genuinely no matching result — confirmed
  live this does happen, e.g. Google's top 10 results for a Hermès product's exact URL contained
  only that one page itself, no FR equivalent at all), the app used to show *nothing***: an empty
  manual-paste panel with no card, even though Serper could clearly still find a real name and
  often a price from the single link that WAS pasted. Resolving the UK/FR pair and finding
  name/price/image for that one link are two separate problems, and failure of the first must
  never block the second. Now: whichever locale `detectLocale` recognises gets `showManualMode`
  called immediately with that side's real URL and the other side left as `''` (never duplicated
  into both — that would make "the FR price" just re-find the UK page's price under a different
  label) — showing whatever Serper can find right away, with the missing side visibly marked
  (`.stub-missing` on its link stub, an empty always-editable price field) rather than hidden.
  `manualUk`/`manualFr` are still pre-filled and the panel still opens, so pasting the missing
  link later completes the *same* catalog row (`findExistingBySku` matches on the known side,
  which never changes across that transition) instead of creating a duplicate.
  - `showManualMode` itself also checks whether an *existing* catalog row already has the side
    that's blank this time (`ukUrl = ukUrl || (existing && existing.url_uk) || ''`, same for FR) —
    a transient `findOtherLocaleLink` failure on a re-check must not regress an item that already
    had both links on file back into "needs manual entry."
  - Only when `detectLocale` can't even tell which locale the pasted link itself is does the app
    fall back to the fully-blank manual panel, since there's nothing safe to show yet.

Hermès still scrapes normally via `runCheck` once both links exist (found automatically, typed
in, or already on file) — only the *pairing* was ever the problem, not the price lookup. Loro
Piana is manual end-to-end either way since it's also `scrapeBlocked`.

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

### A same-domain Serper result isn't automatically the right *page*

`serperLookup`'s hostname-matching fixes "wrong domain" results, but not "right domain, wrong
page." When Google hasn't indexed the exact product URL well, the only same-domain result it has
can be the brand's own homepage, a category page, or (confirmed live, Dior) even a
customer-service contact page — any of which used to get treated as a perfectly good product
name/price if it merely had a long-enough URL path. See the code-anchored `isOurProductPage`
description above (under "Multi-stage, code-anchored Serper price/name lookup") for how a
candidate now actually has to be verified against the specific product being searched for, not
just plausible-looking.

Domain matching itself is done by `sameSite(hostnameA, hostnameB)`, not a raw `.includes()` — it
strips a leading `www.` from both sides and allows either to be a subdomain of the other, which
matters in practice: `chanel.com`'s Google-indexed FR result has no `www.` prefix at all while the
site's own canonical UK link does, and a plain substring check would silently reject a perfectly
genuine same-site match over that alone. This governs organic results; `serperShoppingLookup`
can't reuse it at all (see above — Shopping results never carry a real merchant URL to check).

`cleanTitle` is a second, independent, narrower safety net on top of all the above: if a title,
once the trailing `| Brand` stripped, is *just* the brand name on its own, it returns `null`
instead of that bare name — same as "no title at all." `applyName` in both `trySerperFill` and
`trySerperPriceFallback` checks the *cleaned* result before deciding whether to touch `#prodName`,
not the raw title. This only ever catches a title that's *exactly* the brand name, though — a
longer plausible-but-wrong marketing string (e.g. Dior's actual homepage title,
`"DIOR - US Official Online Boutique | Fashion and beauty ..."`) slips straight past it, which is
why `serperLookup` now nulls out the title itself for anything that isn't a confirmed product
page, rather than relying on `cleanTitle` to catch it downstream.

**A real, pre-existing bug, unrelated to any of the above and predating this session entirely:**
`hostMatches.find(looksLikeProductPage)` (and the equivalent for Shopping-style filtering) passed
the *whole result object* to `looksLikeProductPage`, which expects a link string —
`Array.prototype.find`'s callback always receives `(element, index, array)`, so this needs to be
`hostMatches.find(r => looksLikeProductPage(r.link))`. The bug swallowed itself silently (`new
URL(object)` throws inside `looksLikeProductPage`'s own try/catch, caught, returns `false` for
every candidate) — meaning a real product page never won over the brand's own homepage even when
both were same-domain candidates in the same result set. It went unnoticed for a long time because
the code used to also fall back to *any* off-domain "looks like a product page" match (see
below), which was written correctly and happened to mask this one; removing that off-domain
fallback (per explicit user instruction not to trust unverified third-party sites — a reseller
can carry a genuinely different price for what only looks like the same item) is what surfaced
it. If a same-domain "prefer the deeper/more specific match" comparison is ever added again
elsewhere, double-check the callback actually receives a link string, not a result object.

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

Catalog rows (`buildCatalogRow`, shared by the Catalog tab, Trip Detail, a client's purchase
history, and the request-linking/client-linking pickers) intentionally show as little as
possible: no brand text, no status pill (status is only editable from the product-detail modal
now — see below). The sub-line is just the bare figures in a fixed order — FR retail, UK
retail, sale price, then profit (green if positive, red if negative via
`trip-net-pos`/`trip-net-neg`, the same classes Trips uses) — no "FR price:"-style labels, no
trip label, no date (callers that want the trip label, e.g. client purchase history, append it
to the row's `.catalog-sub` themselves after the fact rather than teaching the shared builder
about it).

Only two statuses exist: `stock` and `sold`. A third, `missed_sale`, existed originally but was
removed — the business never actually used the distinction (an unsold item just means it's
still in stock, not a separate failure state) — so `STATUS_LABEL`/`STATUS_ORDER` only list
`stock`/`sold` and `cycleStatus` just toggles between them. If a live table still has old rows
with `status = 'missed_sale'` in it, run this once in the Supabase SQL editor (the anon key
can't run DDL) to migrate them and lock the column back down:

```sql
update products set status = 'stock' where status = 'missed_sale';
alter table products drop constraint if exists products_status_check;
alter table products add constraint products_status_check check (status in ('stock', 'sold'));
```

### Product-detail modal (Catalog / Trip Detail / a client's purchase history)

Tapping an existing product from any of those three places used to route back into the
Prices-tab card (`reopenCatalogEntry`) — the same UI a live check uses. That's gone: the card is
now exclusively for a fresh, in-progress check (nothing meaningful to delete or link a client to
until it's actually saved), and viewing/editing something already in the catalog opens
`#productDetailOverlay` (`openProductDetail(entry)`) instead — a modal, not a tab switch, so
whichever list it was opened from is still there underneath once it closes. It duplicates the
card's pricing math (`recalcProductModal` mirrors `recalc()` — same détaxe/margin/SAVE_CAPS/
reseller rules) rather than sharing it, since the modal deliberately skips the card's "auto-fill
the sale price until the user overrides it" behavior (an existing catalog row already has a real
recorded price, not a fresh suggestion). `refreshAfterProductChange()` re-renders whichever of
Catalog/Trip Detail/Client Detail is actually on screen after an edit or delete, since the modal
itself doesn't track which one opened it.

This is also the only place the optional client link (`products.client_id`) lives — see Clients
below. The main Prices tab never shows client-linking UI, on purpose.

The old `deleteConfirmOverlay` confirm dialog is now generic (`confirmDeleteProduct(id)` +
module-level `pendingDeleteId`) rather than reading `current.catalogId` — its only caller is the
product-detail modal's delete button, since the card itself never shows a delete option anymore.

### Clients (Supabase `clients` / `client_requests` tables, plus `products.client_id`)

All three must be created manually in the Supabase SQL editor first (the anon key can't run
DDL, same as `trips`/`flight_prices`/`hotel_prices`):

```sql
create table clients (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  name text not null,
  whatsapp text,
  email text,
  source text,
  status text default 'active' check (status in ('active', 'inactive', 'prospect')),
  notes text,
  is_vip boolean default false,
  birthday date,
  referred_by uuid references clients(id),
  last_contact_at timestamptz,
  stats jsonb default '{}'::jsonb
);
alter table clients enable row level security;
create policy "allow all" on clients for all using (true) with check (true);

alter table products add column client_id uuid references clients(id);

create table client_requests (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  client_id uuid references clients(id) not null,
  item_description text,
  brand text,
  budget numeric,
  deposit_status text default 'unpaid' check (deposit_status in ('unpaid', 'deposit_paid', 'paid_in_full')),
  fulfilled boolean default false,
  linked_product_id uuid references products(id)
);
alter table client_requests enable row level security;
create policy "allow all" on client_requests for all using (true) with check (true);
```

Until those exist, `renderClients()`/`loadClients()` fail closed the same way `loadTripCosts`
does — empty list, logged error, the Clients tab just shows its empty state.

"Gone quiet" (`isGoneQuiet`) is computed purely from `last_contact_at` being unset or more than
60 days old — never from `last_contact_at` vs a purchase date, since there's no real WhatsApp
integration to detect actual contact automatically; "Mark contacted today" just bumps
`last_contact_at` to `now()`. The "Sort: Last purchase" toggle in the Clients tab is a separate
signal (`loadClientLastPurchaseMap`, the max `products.created_at` per `client_id`) from gone-
quiet on purpose — a client who bought recently but hasn't been messaged since can still show
both "Last purchase: 2 days ago" and the "Gone quiet" badge at the same time.

Duplicate-client detection (`findDuplicateClients`, run when "Add client" is tapped) flags either
an exact same WhatsApp number (digits-only comparison) or a near-identical name (plain
Levenshtein distance ≤ 2, only for names longer than 4 characters so short names like "Jo"/"Jon"
don't false-positive on every other short name) — same pattern as the duplicate-catalog-entry
problem `findExistingBySku` already solves for products. It doesn't block creation, just warns
once (`newClientHasDupeWarning`) and requires a second tap of "Add anyway" to actually insert.

The client picker (`#clientPickerOverlay`, `openClientPicker(mode)`) is shared by two unrelated
flows — linking a product to a client from the product-detail modal (`mode: 'productLink'`) and
setting a client's own referrer (`mode: 'referredBy'`) — rather than building two near-identical
modals. Its "+ Add new client" shortcut opens the same `#newClientOverlay` used everywhere else;
`pendingNewClientMode` remembers which of the two flows triggered it so the newly-created client
gets linked/set-as-referrer immediately instead of just landing on its own profile.

`clients.stats` (shoe size, preferred brands, etc.) is deliberately free-form — a plain key/value
list (`#statsOverlay`) backed straight by the jsonb column, no fixed schema, since the whole
point is not having to add a new column every time a new kind of detail comes up.

A client's purchase history reuses `buildCatalogRow` (same as Catalog/Trip Detail) rather than a
bespoke renderer — this also means the privacy-blur toggle and `.trip-net-pos`/`.trip-net-neg`
profit coloring apply there for free. The trip label a row belongs to isn't part of
`buildCatalogRow` itself (see above) — `renderClientPurchases` appends it to the row's
`.catalog-sub` afterwards.

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

### "Plan next trip" (Trips tab → `#planTripBtn`)

Scans for the cheapest valid 24-hour Paris trip: land by 11:00, one night, hotel on Rue
Saint-Honoré between Place de la Concorde and the Louvre, fly back 21:00–23:59 the next day,
outbound **Mon/Tue/Wed only** (not Thu — Thursday would put the return on Friday, which the
pattern also bans; the original spec's own wording conflicted on this, resolved by dropping
Thursday from the outbound set so every rule holds without exception).

**This is entirely server-side — do not try to call Duffel or scrape Booking.com from
`index.html`.** Confirmed empirically: `api.duffel.com/air/offer_requests` sends no
`Access-Control-Allow-Origin` header on either an OPTIONS preflight or the actual POST, so
browsers block it outright. The hotel side has its own server-side requirement too — same
reasoning as the Apify scraper originally being ruled out for Dior/LV, just more so: it needs a
Python runtime and a key (`SCRAPFLY_KEY`) that must never reach the browser.

Both run in `.github/workflows/scan-trips.yml` (scheduled every 3 days, plus `workflow_dispatch`
for an on-demand run with optional `target_month`/cutoff-time inputs) via `scripts/scan_trips.py`,
writing to two Supabase tables the client only ever reads from:

```sql
create table flight_prices (
  id uuid primary key default gen_random_uuid(),
  outbound_date date not null,
  return_date date not null,
  origin_airport text not null,        -- SEN / LTN / STN
  destination_airport text not null,   -- CDG / ORY / BVA
  outbound_departure timestamptz, outbound_arrival timestamptz, outbound_airline text,
  return_departure timestamptz, return_arrival timestamptz, return_airline text,
  total_price numeric, currency text default 'GBP', scraped_at timestamptz default now(),
  unique (outbound_date, origin_airport, destination_airport)
);
create table hotel_prices (
  id uuid primary key default gen_random_uuid(),
  hotel_name text, booking_url text, stay_date date,
  nightly_price numeric, currency text default 'GBP', scraped_at timestamptz default now(),
  unique (hotel_name, stay_date)
);
```
`currency` on `hotel_prices` is GBP, not EUR — `scan_hotels()` always sets it explicitly per row
(the vendored scraper's `BASE_CONFIG["country"] = "GB"` means Booking.com returns prices
pre-converted to GBP, formatted as e.g. `"£342"`; parsed by stripping everything but digits/`.`
before `float()` — a first real run crashed trying to `float("£342")` directly).

Both tables need `enable row level security` + an `allow all` policy, same as `trips` — a first
live run caught that the policy hadn't actually taken even though the tables had been created;
inserts failed with `42501` until the `create policy` statements were re-run explicitly. Both
tables exist on the live database now, with working policies. If a future session finds them
missing again (or a new environment/fork), `loadFlightPrices`/`loadHotelPrices` fail closed
(empty arrays, logged) and the Plan-next-trip screen just shows its empty state rather than
erroring — that's the fallback to expect, not a bug to chase.

The workflow needs **four** repository secrets: the already-added `SCRAPFLY_KEY`, plus
`DUFFEL_KEY`, `SUPABASE_URL`, and `SUPABASE_ANON_KEY` (the latter two only live in browser
localStorage today — the Action needs its own copies to write results; safe to expose per the
Tokens section above, this is just about the Action actually having them).

Origins are a **fixed set** (Southend/Luton/Stansted) — never add Gatwick/Heathrow/City as
options. Destinations default to CDG+Orly; Beauvais is always scanned server-side too (so the
data exists) but defaults off client-side via a checkbox, with a coach-transfer-time note, since
its ~1h15 transfer eats into the 11:00 arrival margin.

`scripts/vendor/bookingcom.py` is **vendored, not original code** — from
`github.com/scrapfly/scrapfly-scrapers` (NPOSL-3.0), kept byte-identical below its attribution
header except `BASE_CONFIG["country"]` changed from `"US"` to `"GB"`. See the header comment for
why vendoring is fine here (personal tool, not redistributed) despite the non-profit-flavored
license. The pinned hotel list inside `scripts/scan_trips.py` needs real, verified Booking.com
URLs for hotels actually on Rue Saint-Honoré between Concorde and the Louvre — check that list
first if hotel results look wrong or the scan comes back empty.

Arrival/departure time cutoffs (11:00 / 21:00–23:59) are **fixed constants** in
`scripts/scan_trips.py`, not a live control in the browser — filtering happens during the
scheduled scan, so a client-side time picker wouldn't change anything already computed. A
manually-triggered `workflow_dispatch` run can override them without any UI for it.

The client (`openPlanTrip`/`renderPlanTripResults` in `index.html`) is a pure read + rank +
"Use this trip" screen: filters `flight_prices` by month/origin/destination checkboxes, joins
each candidate to the cheapest (or a pinned) `hotel_prices` row for that date, sorts by total
ascending (Luton first on ties), shows the top 8, and "Use this trip" pre-fills the existing
New Trip modal (`openNewTripModal(prefill)`) rather than creating anything without review.
