# Orbit notification dashboard

The React dashboard displays the bounded Redis feed of notifications that were
successfully delivered to Discord. It refreshes every 15 seconds and supports
free-text and webhook-channel filtering.

Run the FastAPI service on port 8000, then start the Vite development server:

```bash
poetry run python -m orbit.api
cd ui
npm ci
npm run dev
```

Vite proxies `/api` requests to the local FastAPI service. Production hosting
should route the UI and `/api` through the same origin.
