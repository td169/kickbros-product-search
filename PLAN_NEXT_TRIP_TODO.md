# Plan next trip — follow-up work

Notes from Temi on what "Plan next trip" should still get, captured 2026-08-18 so it doesn't
get lost. Do this *after* the current scan pipeline is confirmed working end-to-end (that part
was still being debugged — three real bugs found on the first live run: Duffel rate-limiting,
a missing RLS policy on the two new tables, and a bad import path for the vendored scraper —
see the git log around this date for the fixes).

## Already done

- ✅ Origin/destination checkboxes turn green when checked (`accent-color:var(--profit)`).
- ✅ Results list shows the top 15 candidates, not 8.

## Still to build: an on-demand "Scan" button, rate-limited

Temi wants a real **"Scan" button in the Plan Trip screen** that kicks off an actual fresh scan
right then — not just re-filtering whatever's already in Supabase (that already happens
automatically today whenever the month/checkboxes/hotel-pin change). She expects to use this
maybe 10x/month, so it needs to actually reach out to Duffel/Scrapfly on click, not just wait
for the 3-day schedule.

**The catch:** Duffel can't be called from the browser (no CORS — see CLAUDE.md), so a "Scan"
button that "just runs there and then" has to trigger the *server-side* workflow on demand,
not call Duffel directly. That means:

1. **Trigger mechanism**: call GitHub's REST API to fire a `workflow_dispatch` on
   `scan-trips.yml` (`POST /repos/td169/kickbros-product-search/actions/workflows/scan-trips.yml/dispatches`)
   directly from the browser. This needs a GitHub token with `workflow`/Actions-write
   permission — a new Settings field (`kickbros_github_token`, same localStorage pattern as the
   other tokens), but flag clearly to Temi that this token is more powerful than the
   Apify/Serper ones (it can trigger repo Actions, not just read data) — a fine-grained PAT
   scoped to just this repo's Actions is the safer option to point her toward, not a classic PAT.
   **Verify GitHub's API actually sends CORS headers for this call before building the UI
   around it** — same kind of empirical check that caught the Duffel problem in the first
   place. Don't assume it works.

2. **Rate limit: at most 2 scans per week, spaced out.** Temi's exact rule: one scan allowed
   Mon/Tue/Wed, another allowed later in the week, but never on the same day as the last scan
   and never exactly 2 days after it — reasoning being nothing changes drastically inside a
   week. The simplest rule that satisfies all of that: **require at least 3 days since the last
   scan** before allowing another one (that alone rules out same-day and 2-days-apart, and
   naturally caps it to ~2 per 7-day week if she uses it right at the boundary — e.g. Monday,
   then next-earliest Thursday).
   - Needs a small place to persist `last_scan_at` — a tiny new Supabase table (e.g.
     `scan_runs(id, triggered_at timestamptz)`, insert one row per trigger) works and reuses
     the existing `db.from(...)` pattern already all over `index.html`.
   - On click: read the most recent `triggered_at`; if `(now - last) < 3 days`, don't trigger —
     show "Next scan available from <date>" instead. Otherwise call the GitHub dispatch API,
     immediately insert a new `scan_runs` row (so a rapid double-click or a second device can't
     both sneak a trigger through before the first one's timestamp is visible), and tell Temi
     the scan has started — results will update once the Action finishes (a few minutes, given
     ~225 flight searches at the now-safer 1.0s pace). No need to build live progress/polling
     for v1 — "check back in a few minutes" is fine.

## Why this was deferred rather than built immediately

Temi asked for this in the same message as the other changes, but explicitly said the scan
pipeline itself needed to keep getting debugged first ("for now i need to scan until this is
done correctly but remember this should be done after"). This file is that "remember" — pick
it up once a real scheduled or on-demand run has been confirmed to write good data to both
`flight_prices` and `hotel_prices` without errors.
