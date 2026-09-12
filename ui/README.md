# Orbit operations dashboard

The React dashboard provides a realtime operations view over the bounded Redis
feed of notifications that were successfully delivered to Discord. It includes
runtime health, bounded-buffer signal and alert summaries, channel activity, active
symbol detection, and a searchable event stream. Discord integration remains
unchanged, and successful webhook responses are mirrored to the dashboard.

Run the FastAPI service on port 8000, then start the Vite development server:

```bash
poetry run python -m orbit.api
cd ui
npm ci
npm run dev
```

Vite proxies `/api` requests to the local FastAPI service. Production hosting
should route the UI and `/api` through the same origin.

The dashboard also exposes the same immutable-ledger Testnet evidence used by
the GitHub reporter:

- `/daily` renders one completed UTC day, from midnight through the following
  midnight.
- `/weekly` renders completed Saturday-through-Friday UTC weeks, using a
  half-open Saturday-midnight to Saturday-midnight boundary.

Both pages allow navigation through completed periods. The live command center
continues to show closed trades by the browser's local calendar day.
