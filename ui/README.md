# Command Center

The unified frontend for this project (2026-08-27 restructure) — a clean,
high-performance replacement for the legacy vanilla-JS dashboard
(`services/dashboard/`, deleted outright; see git history if it's ever
needed again). Five routes behind one persistent Sidebar + sticky Zone 1
header: Sniper HUD (`/`, the original 4-Zone live dashboard, fully real)
and four not-yet-wired tool skeletons (`/analytics`, `/optimizer`,
`/journal`, `/safety`). Sniper HUD's own default view is either "Awaiting
High Conviction Setups" or a grid of focused Action Cards, one per real,
currently active or probability-surfaced signal — no 208-symbol
spreadsheet, no default view that hides an empty state behind silence.

## Stack

React 19 + TypeScript, Vite, Tailwind CSS v4, Zustand, react-router-dom,
lucide-react. Minimal runtime dependencies by design (see the original
"Sniper HUD" rebuild's own Phase 2 spec) -- router and icons were the
first two additions beyond that original list, added for the Command
Center restructure.

## How this was scaffolded

Recorded here per the rebuild's own Phase 4 ask -- these are the exact
commands that produced this directory, not a generic template:

```bash
npm create vite@latest . -- --template react-ts
npm install
npm install -D tailwindcss @tailwindcss/vite
npm install zustand
```

Node wasn't available on the host machine this was built on, so every
command above (and every `npm run` below) was actually run inside a
`node:22-alpine` container with `ui/` bind-mounted, e.g.:

```bash
docker run --rm -v "$(pwd):/app" -w /app node:22-alpine \
  sh -c "npm install"
```

Vite is pinned to v6 (not whatever `npm create vite@latest` pulls) --
v8.2.2 was tried first and has a real dev-server bug where the `/ws`
proxy's WebSocket upgrade handling knocks out Vite's own HMR
client (`[vite] server connection lost. Polling for restart...` in a
loop). v6 doesn't have this problem; downgrade before debugging your
own proxy config if you ever hit that same symptom.

## Running it

The dev server proxies `/api` and `/ws` to the real backend (same two
upstreams as `services/dashboard/nginx.conf`), so it needs to reach the
`api` and `ws-gateway` containers. Two ways to run it:

**From the host**, once Node is available locally and the main stack's
ports are published (`docker compose up`, as usual):

```bash
cd ui
npm install
npm run dev
```

**From a container on the project's own Docker network** (what was used
to build and verify this):

```bash
docker run -d --name sniper-hud-dev \
  --network infusion-core-architecture_infusion \
  -p 5173:5173 -p 5174:5174 \
  -e VITE_API_HOST=api \
  -e VITE_WS_HOST=ws-gateway \
  -v "$(pwd)/ui:/app" -w /app node:22-alpine \
  sh -c "npm run dev -- --host 0.0.0.0"
```

`VITE_API_HOST` / `VITE_WS_HOST` default to `localhost` (the host-machine
case); set them to the compose service names when running inside the
network instead.

## Layout

```
src/
  types.ts                Wire shapes for /api/signals, /api/trade-blueprint,
                           /api/options/summary -- verified against real live
                           responses, not just the Python models.
  lib/api.ts               fetch() wrappers, one per endpoint.
  lib/candidates.ts         Normalizes SignalRow + SuppressedSignalRow into
                            one Candidate shape (mergeCandidates()) so
                            Sniper HUD's Zone 2 can render either uniformly.
  hooks/usePolling.ts       Generic poll-on-an-interval hook.
  hooks/useSignals.ts       Active-signal list (GET /api/signals, 3s).
  hooks/useSuppressedSignals.ts  Suppressed-but-scoring list (3s).
  store/useTickStore.ts     Zustand store for the live WS tick_batch feed.
                            useLtp(symbol) is the localized selector every
                            price display should use -- a tick for one
                            symbol only re-renders components subscribed
                            to THAT symbol's slice, not the whole tree.
  components/
    Layout.tsx              App shell: Sidebar + sticky Zone 1 header
                             (Live Index Pulse) + per-route <Outlet>.
                             Global demo-ticker/socket-connect side
                             effect lives here, not in any one page.
    Sidebar.tsx              Persistent, collapsible left nav (5 routes).
    ToolPageShell.tsx        Shared skeleton for the four not-yet-wired
                             tool routes -- header + metric placeholders
                             + one empty-state card. No spreadsheet
                             tables, per the Command Center restructure's
                             own Phase 4 rule.
    ActionCard.tsx           Header (symbol, direction, trade_horizon,
                             win probability, R:R, warning-tag chips,
                             OI buildup) + DynamicTimeline + Microstructure
                             Pill (POC/VAH, strike, spread, delta), one
                             per active-or-surfaced Candidate.
    DynamicTimeline.tsx      SL/Entry/T1/T2/T3 plotted on a normalized
                             risk-left/reward-right bar, live LTP marker
                             sliding via CSS transition, highlighted when
                             price is back inside the entry-zone band.
  pages/
    SniperHud.tsx            `/` -- the original 4-Zone dashboard's Zones
                             2-4 (Zone 1 moved up into Layout.tsx).
    OptionsAnalytics.tsx     `/analytics` -- skeleton, see ToolPageShell.
    TheLab.tsx                `/optimizer` -- skeleton, see ToolPageShell.
    TheLedger.tsx             `/journal` -- skeleton, see ToolPageShell.
    SafetyLogs.tsx            `/safety` -- skeleton, see ToolPageShell.
  App.tsx                   Router root: five routes behind one Layout.
```

## What's real vs. not verified yet

Everything above was verified against the live backend during the
2026-08-26 build session -- `/api/trade-blueprint/{symbol}`'s exact
field shapes, `/api/options/summary`'s `upstox_option.metrics` nesting,
the `/ws` `tick_batch` protocol. The one thing that could NOT be
verified live: an Action Card actually populated by a real active
signal -- the market was closed and, per that day's own post-market
audit, zero signals had cleared the conviction floor all session, so
`/api/signals` returned an empty list the whole time this was built.
The empty state is confirmed live-correct; the populated-card layout
was confirmed by temporarily monkey-patching `window.fetch` in a
throwaway browser tab to return a realistic mock payload (never
committed, never part of the shipped code) -- worth a real look the
next time a signal is actually live.
