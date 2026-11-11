# UPDATE.md

A running log of what's changed in the app, in plain language. Newest entry at the top. This
is for tracking progress at a glance — for the technical "how it works" reference, see
[CLAUDE.md](CLAUDE.md).

---

## 2026-11-11 (later) — A proper Home screen

The app now opens on a new **Home** tab, first in the bottom bar (Home, Prices, Catalog, Clients,
Trips, Settings). It has the KICKBROS HUB heading, your trip widget straight underneath, since
it's the time-sensitive one, then four widgets, two per row: Prices, Catalog, Clients and Trips.
Tap one to jump there. The trip widget is now dark: Paris at night, with a softly lit Eiffel
Tower, and deep green while a trip is on. The Prices tab is now just called "Prices", and the ☰
brand filter only shows on Catalog, the only place it does anything.

---

## 2026-11-11 — Trip widget on the home screen, straight into a new trip

### Trip widget on the home screen

The top of the home screen now shows your next trip as a widget: "Paris in 9 days" (or
"tomorrow"), with the dates underneath. While you're on a trip it turns green and says "Active
trip". If one trip is on and another is coming up, both show (two at most). Past trips don't
show, and with nothing planned it says "No trips planned" and takes you to Plan next trip. Tap a
trip to open it. Days are counted on UK time, so the countdown changes at midnight.

### Creating a trip opens it

"Create trip" already opened the new trip, but when you created it from Plan next trip's "Use
this trip", the Plan next trip screen stayed on top of it. Fixed. The button also can't be
double-tapped while it's saving.

---

## 2026-11-04 — Pick your own days, and "Update now" fixed

### Fix: "Closest available" could show an afternoon flight

"Closest" had no limit, so a route's only option could be hours off and still appear. Now the
closest flight only shows if it flies back from 20:00 and still lands by 11:00. Anything further off
never appears, including ones already saved by an earlier scan. A day with nothing in those times
shows no card, and the app says it has no prices saved within your times, not that there are no
flights.

### Fix: picked days wrongly said there were no flights

The scan threw away any flight outside the 11:00 landing and 21:00–23:59 return, then the app said
that day had no flights. That wasn't true: Luton to CDG on Mondays has plenty, but the Tuesday
evening flight back leaves at 20:45, 15 minutes too early, so all of them were dropped. Every route
and day now keeps its closest flight. It's marked "Closest available" with what's off about it, and
flights that fit your times still come first. The app no longer says a day has no flights. Rate
limits from the flight search also get retried properly now, and can't wipe a day's saved prices.

### Fix: "Update now" looked like it did nothing

Tapping it did start a real scan, but the screen read the *previous* scan's "finished" status
before the new one had got going. The progress bar flashed "Updated" and disappeared within a
second, the button came straight back (making it easy to pay for a second scan by accident), and
the new prices never loaded on screen. Now the bar says "Starting scan..." until the new scan
really begins, then shows its real progress, and loads the new prices when it finishes.

### Pick the days you can go

The Month filter on Plan next trip is now a calendar: tap the days you can fly out (Monday to
Thursday, up to 60 days ahead, flying back the next day) and you only see trips for those days.
They're remembered on that device. "Update now" scans flights for just the days you picked, so it
uses far fewer flight searches than a full scan (hotels are still all refreshed), and the
confirmation tells you what it'll scan. With nothing picked it scans every Mon–Thu in the next 45
days.

The automatic Thursday scan still covers Mon/Tue/Wed only, so a Thursday shows no prices until you
tap "Update now".

---

## 2026-10-04 — Weekly Thursday scans, 45-day window, "Update now" with live progress

### Trip scan now runs weekly, on a 45-day window

The flight/hotel price scan was running every 3 days across a 61-day window; it now runs once a
week, every Thursday at 06:00 UTC, and only covers the next 45 days. Older rows beyond that
window from earlier scans are left alone, not deleted — they just stop refreshing.

### "Update now" button on Plan next trip

You can trigger a fresh scan on demand from the Plan next trip screen instead of waiting for
Thursday. It asks for confirmation first (it costs real money), then kicks off the same GitHub
Action the schedule uses. A thin progress bar at the top of the screen tracks the real scan
progress (not a fake timer) while it runs — the existing flight/hotel options stay fully visible
and usable the whole time, and only get replaced once the fresh data is in. Needs a GitHub
personal access token saved in Settings (Actions: read & write on this repo only).

### "Last updated" on every option

Every flight/hotel card now shows how long ago that price was actually checked (e.g. "Last
updated: 2 days ago"), so it's clear which prices are fresh and which are from an older scan.

---

## 2026-08-19 — Three new brands, better auto-fill, missing-FR-price fix

### New brands: Hermès, Prada, Loro Piana

All three now work the same way as the existing brands — paste one link, get both. Hermès
scrapes live like Gucci/Moncler. Prada and Loro Piana go through the same guided manual-entry
flow as Dior/Louis Vuitton (open both listings, type what you see) since their sites block
automated checks. For Hermès and Loro Piana specifically, the app can't always work out the
second listing's link on its own from the URL alone — when that happens it now first tries to
find it via the same Google search the app already uses elsewhere (checking both the normal and
sponsored results), and only asks you to paste the missing link yourself — pre-filling whichever
one you already gave — if that comes up empty.

### Fix: FR price sometimes came back empty on manual-entry brands

The Google-assisted auto-fill (used for Dior/LV and the new Prada/Loro Piana) was more likely to
miss the FR price than the UK price — it wasn't telling Google to search in French, and it
trusted whatever the top search result was even when that result was the wrong page entirely.
Both fixed.

### Fix: Dior (and other manual-entry) checks sometimes named the product just "Dior"

Same root cause as the FR price bug above, one layer deeper: even after the fix, a "right
domain" search result isn't always the right *page* — when Google hasn't indexed the specific
product page, the only same-domain result on hand can be the brand's own homepage, with a title
that's just the bare brand name. That was getting filled straight into the product name field.
Now it looks for a result that actually looks like a real product page first (own site
preferred, but a reseller's real listing beats the brand's blank homepage), and separately,
never fills the name field with something that's just the brand name on its own — it leaves the
field empty for you to type instead, which was always more honest than a fake-looking entry.

### Every brand now gets a second chance before asking you to type

If a live scrape (Gucci, Moncler, Balenciaga, Goyard, Hermès) comes back without a price on one
or both sides, the app now automatically tries the same Google-assisted lookup the manual-entry
brands use before giving up — typing something in by hand is the last resort now, not the first
response to an incomplete scrape.

---

## 2026-08-19 — "Plan next trip" launched, plus navigation and polish fixes

### New: Plan next trip

The big addition this round — a screen (Trips tab → **Plan next trip**) that automatically
scans for the cheapest valid 24-hour Paris trip in a given month: land by 11:00, one night,
depart 21:00–23:59 the next day, flying out Mon/Tue/Wed only from Southend, Luton, or Stansted
into CDG or Orly.

- Runs entirely in the background on a schedule (flights via Duffel, hotels via Booking.com) and
  writes results to your database — opening the screen is instant, no live searching or waiting.
- Filter by hotel tier — **All / Standard / Luxury** — via a new pill-style selector, or switch
  to **Cheapest Per**, which shows one card per tracked hotel (its own best deal for the month)
  instead of just the overall cheapest 15.
- Results show the full date range (e.g. "Tue 25 Aug – Wed 26 Aug"), a hotel photo, and the tier
  badge. "Use this trip" pre-fills the New Trip form so you can review before creating anything.
- Tracking 18 named hotels around Rue Saint-Honoré / the Louvre, split 9 Luxury / 9 Standard.
- A handful of real bugs were caught and fixed while building this: flight times were briefly
  getting stored in the wrong timezone (making valid flights look invalid), hotel prices over
  ~£1,000 were being parsed wrong (Booking.com uses a "." as a thousands separator, not a
  decimal), and stale flight data was lingering after a route stopped being valid. All confirmed
  fixed against live scans.
- Two of the 18 tracked hotels currently show little or no pricing — confirmed this is
  Booking.com not exposing availability for those specific listings right now, not a bug on our
  end. They'll reappear automatically once that changes.

### Navigation and polish

- Fixed a few places missing a way to back out: the New Trip modal and a reopened catalog item
  both now have a proper X/back button.
- Fixed catalog items silently reordering themselves when you revisited the list, and the trip
  dock jumping around while editing.
- Renamed the Prices tab heading to **KICKBROS HUB** and dropped the old disclaimer footnote.
- Added an intro splash screen with the white KB logo on launch.

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
