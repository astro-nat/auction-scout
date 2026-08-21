# AuctionScout

<!-- deployed via Railway auto-deploy since 2026-08-21 -->

Auction-lot sourcing for resellers: scrapes live HiBid auctions, matches lots
against a curated BOLO (be-on-lookout) brand database, enriches them with
Claude (text + vision), prices them against real eBay comps, and grades every
lot with ROI math — max bid, expected profit, GOLD MINE/PASS.

FastAPI + Postgres backend, React frontend, Docker Compose for local dev,
deployable to Railway. Replaces an earlier Streamlit prototype.

## What it does

1. **Scan** — discovers open HiBid auctions near your zip (GraphQL API),
   parses each auction's real buyer premium from its terms.
2. **Import** — pulls every open lot: bids, descriptions, images, and a
   shipping-difficulty tier (EASY / NEUTRAL / HARD).
3. **Enrich** (per lot, cheapest signal first):
   - **BOLO match** — deterministic regex over 22 curated brand files
     (326 brands); free and instant.
   - **Claude** — text pass when the description has signal, vision on the
     photo otherwise; produces an eBay-searchable title + condition verdict.
   - **Comps** — SoldComps (optional) or eBay Browse listings, with
     relevance/quantity/outlier filters.
   - **ROI** — max-bid ceiling at your target ROI, after buyer premium, sales
     tax, platform fees, shipping penalty. Viable lots get 🟢 GOLD MINE.
4. **Inspect** — itemized vision pass for mixed lots ("Lot of 10 CDs"):
   reads each item off the photo and prices them individually.
5. **Correct** — click any value in the UI to fix it; corrections persist and
   are never overwritten by re-enrichment.

The UI is responsive (cards + search/sort on phones, sortable/filterable
table on desktop) and follows the system light/dark theme.

## First-time setup

```bash
cp .env.example .env
# then edit .env and add your real keys (see below)
```

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Claude enrichment (text + vision) |
| `EBAY_APP_ID` / `EBAY_CERT_ID` | for comps | eBay Browse API (price comps + image search) |
| `SOLDCOMPS_API_KEY` | optional | real sold prices instead of active listings |
| `SOURCING_ZIP` / `SOURCING_RADIUS_MILES` | defaults exist | where to scan for auctions |
| `TARGET_ROI_PCT` | default 500 | ROI bar a lot must clear to be a GOLD MINE |

## Run everything locally

```bash
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (interactive docs at /docs)
- Postgres: localhost:5432 (user: htown, db: auction_scout)

Tables are auto-created from the SQLAlchemy models on first boot — fine for
dev, not a substitute for real migrations (see "Not here yet").

Typical flow, all from the UI: **Scan nearby auctions → Import lots →
Enrich all** (or "Enrich visible" to prioritize what's on screen), then sort
by Est Resale / filter to gold mines.

## API quick reference

```
POST /auctions/scan              discover open auctions near the zip
POST /auctions/{id}/import       pull all open lots for one auction
POST /auctions/{id}/enrich-all   queue enrichment for pending/failed lots
POST /lots/enrich-batch          queue an explicit ordered list of lots
POST /lots/{lot_id}/enrich       enrich one lot
POST /lots/{lot_id}/inspect      itemized vision pass for mixed lots
PATCH /lots/{lot_id}/enrichment  apply a user correction (persisted)
GET  /lots?auction_id=&status=&roi_status=&bolo_only=
```

## Deploy (Railway)

The repo is deploy-ready: per-service `railway.json`, Dockerfiles that bind
`$PORT`, DB-retry on boot, and `postgres://` URL normalization.

- Three services: Postgres (Railway plugin) + backend (root dir `backend`)
  + frontend (root dir `frontend`).
- Backend variables: `DATABASE_URL` (reference the Postgres service),
  `ANTHROPIC_API_KEY`, `EBAY_APP_ID`, `EBAY_CERT_ID`,
  `FRONTEND_ORIGIN` (the frontend's public URL), `PORT`.
- Frontend variables: `VITE_API_BASE` (the backend's public URL — baked in
  at build time), `PORT`.
- Generate a public domain for each service (Settings → Networking); the
  domain's target port must match the service's `PORT`.

Pushes to `main` auto-deploy both services.

## Repo layout

```
backend/app/
  main.py               FastAPI app + CORS + table creation (with DB retry)
  database.py           engine/session; normalizes managed-Postgres URLs
  models.py             Auction, Lot, Enrichment (SQLAlchemy)
  schemas.py            Pydantic request/response shapes
  config.py             env-driven settings + logistics regexes
  routers/
    auctions.py         scan / import / enrich-all
    lots.py             lot listing with filters
    enrichment.py       enrich / inspect / batch / corrections
  services/
    hibid.py            HiBid GraphQL scraper (discovery, meta, lots)
    bolo.py             deterministic BOLO matcher (ported from prototype)
    pricing.py          comps lookup + anti-garbage filters
    ebay.py             eBay OAuth, keyword + image search
    financials.py       ROI math (max bid, profit, DTS)
  workers/enrich.py     the ONLY file that imports the Anthropic SDK —
                        full per-lot pipeline runs here as a background task
  data/*_bolo.json      22 curated BOLO brand files

frontend/src/
  App.jsx               auctions panel, view filters, responsive shell
  components/LotTable.jsx  table (desktop) / cards (mobile), sorting,
                        per-column filters, click-to-correct, enrich/inspect
  api.js                fetch wrappers (VITE_API_BASE-aware)
  index.css             light/dark theme tokens
```

## What's deliberately NOT here yet

- **Alembic migrations** — tables are auto-created; add Alembic before the
  data matters. Schema changes currently need a manual `ALTER` or a dev-DB
  reset (`docker compose down -v`).
- **A real job queue** — enrichment runs via FastAPI `BackgroundTasks`:
  fine for one worker, but no retry-on-crash or concurrency control, and a
  backend restart drops the in-flight queue (orphans are auto-reset to
  `pending` on startup). Swap for Arq/Celery when it matters.
- **Sell-through data** — DTS (days-to-sell) is stubbed to 0 in the ROI
  check; wire up sold/active counts to make illiquid items fail viability.
