# Orbit operations dashboard

The React dashboard provides a realtime operations view over the bounded Redis
feed of notifications that were successfully delivered to Discord. It includes
runtime health, 24-hour signal and alert summaries, channel activity, active
symbol detection, and a searchable event stream. Discord delivery remains active
and is mirrored to the dashboard after each successful webhook response.

Run the FastAPI service on port 8000, then start the Vite development server:

```bash
poetry run python -m orbit.api
cd ui
npm ci
npm run dev
```

Vite proxies `/api` requests to the local FastAPI service. Production hosting
should route the UI and `/api` through the same origin.
