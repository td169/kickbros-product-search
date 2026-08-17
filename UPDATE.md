# UPDATE.md

A running log of what's changed in the app, in plain language. Newest entry at the top. This
is for tracking progress at a glance — for the technical "how it works" reference, see
[CLAUDE.md](CLAUDE.md).

---

## 2026-08-17 (later) — Trips became a calendar, plus a couple of real bugs fixed

### Trips tab redesign

- Trips is now a proper **month calendar** instead of a list — opens on today's real month.
  Tap the month name to jump to any month/year via a picker.
- Days covered by a trip show as a solid green bubble — a single day is a full circle, a
  multi-day trip is a connected pill, like highlighting a word in a word search.
- A "previous trips" strip now sits above the bottom tab bar (same idea as the "paste a link"
  bar on Prices) for quick access to any trip regardless of what month the calendar's showing.
- Trip Detail now has editable Start/End date fields, and "New trip" collects them up front —
  this is what feeds the calendar highlighting above.

### Two real bugs found and fixed while double-checking your last few requests

- **Profit wasn't actually green** — a CSS rule was scoped to the wrong container, so it was
  quietly falling back to grey everywhere except the Trips summary line. Fixed and confirmed
  live.
- **One duplicate catalog entry** — a Louis Vuitton item had two rows (an old "stock" one with
  no image, a newer "sold" one with an image). Found it, kept the more complete row, deleted
  the stale one.

Also: what looked like the earlier fixes "not working" for a bit was actually GitHub itself
having a partial outage that blocked the site from redeploying for a few hours — nothing wrong
with the app, just stuck on an old build until GitHub's service recovered.

---

## 2026-08-17 — First update (everything since CLAUDE.md was written)

CLAUDE.md was written right after the dark-mode/one-screen-card redesign. Everything below has
happened since then.

### The catalog moved to a shared cloud database

- Replaced the old "History" (saved only in one browser's storage, so your phone and laptop
  never saw the same list) with a shared **Catalog**, backed by Supabase — every check you run
  now shows up on every device.
- Imported your old Excel trip-tracking sheets and the older "Sheet1" master list into it, with
  duplicates removed and Goyard's pricing rule respected (it sells at French retail, not the
  usual margin formula).
- Added Quantity, Total profit/spend, and a Stock/Sold/Missed-sale status to every item.

### Settings, filtering, and search

- Settings (your API tokens, Supabase login, pricing rules) moved out of a popover into its own
  bottom tab.
- The top-left icon now opens a brand filter for the Catalog.
- Added a live search bar to the Catalog.

### A dedicated Trips tab

- New **Trips** tab, listing your real Paris trips (anything shaped like "PARIS \<month>
  \<year>") with item counts, revenue, and profit.
- **New trip** button lets you start a trip from scratch — name it, set the flight cost and
  hotel cost, and it opens straight into that trip.
- Inside a trip, you can add products two ways: paste a link (same as the main Prices screen)
  or pick something already in your Catalog. Net profit automatically accounts for flight +
  hotel cost.
- The product card's sale price is now directly editable — this is where you record what an
  item actually sold for on a trip, rather than just the calculator's suggestion.

### Bug fixes

- **Images not saving** — fixed on two fronts: a timing bug where checking a new product before
  the previous one's image finished loading would silently drop it, and a missing database
  column that was blocking every image (and, it turned out, every other edit — name, price,
  everything) from saving at all. Both are fixed and confirmed working now.
- **Duplicate catalog entries** — re-checking the same product used to create a brand new row
  every time instead of updating the existing one. Found and cleaned up a real example of this
  in your catalog (one item had been checked 4 times, leaving 3 empty duplicates behind).
- **Search bar occasionally missing** — on a completely fresh page load, the "paste a link" bar
  at the bottom could fail to appear until you tapped another tab first. Fixed.

### Catalog now shows less clutter

- Removed the "Clear catalog" button — too easy to hit by accident with how much is in there
  now.
- Each catalog row now just shows the numbers that matter — FR price, UK price, sale price, and
  profit (in green) — no brand label, no status tag cluttering the row.
