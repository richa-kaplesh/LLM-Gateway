# Task: LLM Gateway dashboard — Overview page and navigation fixes

Audience: recruiters. In 5 seconds they must see (1) proof the redesign worked and (2) that the gateway is live.
Work only in `frontend/`. No backend changes. Reuse existing components (`components/ui/*`), existing colour tokens in `index.css`, and existing API functions in `lib/api.ts`.

## 1. Navigation
- New route `/` → `OverviewPage` (new file `pages/OverviewPage.tsx`).
- Move the chat (`QueryPage`) to `/try`. Sidebar and MobileNav label: "Try it".
- Remove `DashboardPage` and its route; redirect `/dashboard` → `/`. Delete `DashboardPage.tsx` after confirming nothing else imports it. Delete `lib/api.ts.bak` and `pages/BaselinePage.tsx.bak`.
- Sidebar order: Overview, Keys, Experiments, Baseline, Try it.

## 2. Config file: `src/config/overview.ts`
```ts
export const OVERVIEW = {
  beforeSnapshotLabel: 'before: provider routing',   // label of a saved metric snapshot
  afterSnapshotLabel: 'after: deployment routing',
  deploymentRoutingLiveSince: '',                     // ISO timestamp, filled in by Richa
  evalNote: '50-question QueryMind evaluation on the live deployment',
}
```

## 3. Overview page layout (top to bottom)
1. **Header**: title "LLM Gateway". One sentence below: "Turns 8 free-tier API keys into one reliable pool: routes every call to the key with the most room and fails over in milliseconds." Right side: small "Live" dot (same as Keys page).
2. **Proof row** — 3 cards, built from two snapshots fetched with `fetchMetricsSnapshots()` and matched by label from config:
   - Gateway error rate (`error_rate_pct`), p95 latency (`latency.all.p95`), fallback rate (`fallback_rate_pct`). Use the actual field names in `MetricsSummary`.
   - Each card: metric name; the AFTER value large; "was <before>" small and muted; a delta chip (green if improved, red if worse; lower is better for all three).
   - Under the row, one muted line: `<evalNote> · before: <date window, n requests> · after: <date window, n requests>`, plus a link "All numbers →" to `/baseline`.
   - Show a small warning badge if either snapshot has fewer than 30 requests (same badge style as Baseline).
   - If either snapshot is missing: hide the proof row entirely. Never show placeholders or zeros.
3. **Live pool** — compact grid of all deployments from `fetchStatus()`:
   - Summary line: "8 / 8 ready · next capacity: now".
   - One small tile per deployment: id, tier badge (smart/fast), status dot + word (Ready / Cooling down / Breaker open), one bar for daily headroom (requests-per-day remaining).
   - Status colour is the only colour on tiles. Green = ready, amber = cooling down, red = breaker open.
   - Link "Open Keys →" to `/keys`.
   - Poll every 10 s; stop polling when `document.hidden`.

## 4. Loading, cold start, errors (Overview and Keys)
- Skeletons while loading.
- If loading takes more than 4 s, show above the skeletons: "Waking the server. The free tier sleeps when idle; this usually takes about 30 seconds."
- If nothing arrives after 60 s, or a request fails: "Couldn't reach the gateway." with a Retry button. No raw error text on screen.

## 5. Number formatting (new helper in `lib/format.ts`, use it on Overview; Baseline later)
- Latency: under 1000 ms → "607 ms"; 1000 ms and above → "22.5 s".
- Percent: one decimal ("6.7%").
- Money: 2 decimals; below $0.01 show "< $0.01".
- Counts: thousands separator.

## 6. Baseline default window
- Add a preset "Since redesign" using `OVERVIEW.deploymentRoutingLiveSince`; make it the default when that value is set. Keep All time, Last 24 h, Custom.

## 7. Try it page
- Replace `window.prompt` in `postWithKey` with an inline API-key input on the Try it page (stored in localStorage as now). When there is no key, show: "Sending queries needs an API key. Everything else on this dashboard is public." Never use browser dialogs.

## 8. Type and spacing rules for new code
- Minimum font size 12 px. Use only Tailwind sizes text-xs, text-sm, text-base, text-xl, text-2xl.
- No new hex colours; use existing tokens.

## Done when
- `npm run build` and lint pass.
- Overview renders correctly in four cases: both snapshots present; one missing; backend asleep (cold start message appears); backend down (Retry shown).
- Screenshots of Overview at 1280 px and 375 px wide.
