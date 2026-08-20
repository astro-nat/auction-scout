# AuctionScout

Auction lot ingestion, AI-driven BOLO/brand enrichment, and ROI scoring — built to replace a Streamlit prototype with a real FastAPI + Postgres + React stack.

FastAPI + Postgres backend, React frontend, Docker Compose to run it all locally.

## First-time setup

```bash
cp .env.example .env
# then edit .env and add your real ANTHROPIC_API_KEY
```

## Run everything

```bash
docker compose up --build
```

- Backend: http://localhost:8000 (interactive docs at /docs)
- Frontend: http://localhost:5173
- Postgres: localhost:5432 (user: htown, db: auction_scout)

First boot creates the tables automatically from the SQLAlchemy models
(`Base.metadata.create_all` in `main.py`) — fine for early dev, not a
replacement for real migrations once there's data you care about (see below).

## Adding a lot manually to test the flow

```bash
curl -X POST http://localhost:8000/lots \
  -H "Content-Type: application/json" \
  -d '{"lot_id": "317398926", "title": "925 Silver Taxco Bracelet", "category": "Bracelets / Cuffs", "description": "Vintage Taxco silver bangle, turquoise inlay, 20.1g", "current_bid": 32, "next_bid": 33}'
```

Then trigger enrichment:

```bash
curl -X POST http://localhost:8000/lots/317398926/enrich
```

Watch it complete:

```bash
curl http://localhost:8000/lots/317398926   # AuctionScout backend
```

## What's deliberately NOT here yet

- **Alembic migrations** — tables are auto-created for now; add Alembic before this
  holds data you'd be upset to lose.
- **A real job queue** — enrichment runs via FastAPI `BackgroundTasks`, which is fine
  for one worker but has no retry-on-crash or concurrency control. Swap for Arq/Celery
  when that starts to matter (see `backend/app/workers/enrich.py` docstring).
- **Nginx** — not needed for local dev; added in the deploy phase.
- **The CSV importer** — write a one-off script that reads your existing
  `htown-results-*.csv` and POSTs each row to `/lots` to backfill real data.

## Repo layout

```
backend/app/
  main.py           FastAPI app + CORS + table creation
  database.py        engine/session
  models.py           Auction, Lot, Enrichment (SQLAlchemy)
  schemas.py           Pydantic request/response shapes
  routers/lots.py       GET/POST /lots
  routers/enrichment.py  POST /lots/{id}/enrich — enqueues only, never calls AI directly
  workers/enrich.py       the only file that imports the Anthropic SDK

frontend/src/
  App.jsx              top-level component, fetches lots on load
  api.js                 fetch wrappers to the backend
  components/LotTable.jsx  table + enrich button + status polling
```
