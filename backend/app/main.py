import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from . import models  # noqa: F401 — import registers models on Base before create_all
from .routers import (lots, enrichment, auctions, govdeals, publicsurplus,
                      status, vinted, events)

# Dev convenience only — creates tables from models if they don't exist.
# Once this is a real app with data you care about, replace this with Alembic
# migrations instead of letting SQLAlchemy auto-create/alter tables.
# Retried because managed Postgres (Railway etc.) can take a few seconds to
# accept connections at deploy time — crashing on the first refusal means an
# endless crash-loop that looks like a broken deploy.
from sqlalchemy import text

# Poor-man's migrations until Alembic: create_all never ALTERs existing
# tables, so columns added after first deploy are appended here idempotently.
_MIGRATIONS = [
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS auth_required BOOLEAN DEFAULT FALSE",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS progress VARCHAR",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS category_lot_count INTEGER",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS category_count_for INTEGER",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS ship_cost_estimate FLOAT",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS ship_summary VARCHAR",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS ship_analyzed_at TIMESTAMP",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS queued_task VARCHAR",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS watched BOOLEAN DEFAULT FALSE",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS closing_alert_sent_at TIMESTAMP",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS hidden BOOLEAN DEFAULT FALSE",
    # Which identifiers the AI title asserted that the listing never had -
    # NULL when none. Set at enrichment and re-price; gates the gold badge.
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS identity_note VARCHAR",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS ships_to_us BOOLEAN",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS closes_at TIMESTAMP",
    "CREATE TABLE IF NOT EXISTS settings (key VARCHAR PRIMARY KEY, value VARCHAR)",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS all_in_cost NUMERIC",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS auctioneer_id INTEGER",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS hidden BOOLEAN DEFAULT FALSE",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS state VARCHAR DEFAULT 'running'",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claimed_by VARCHAR",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS queue_rank INTEGER",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS ix_enrichment_queue "
    "ON enrichment (status, queued_at, queue_rank)",
    "CREATE TABLE IF NOT EXISTS worker_heartbeats ("
    "id VARCHAR PRIMARY KEY, last_seen TIMESTAMP NOT NULL, "
    "started_at TIMESTAMP DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS task_timings ("
    "id SERIAL PRIMARY KEY, kind VARCHAR NOT NULL, phase VARCHAR NOT NULL, "
    "job_id VARCHAR, auction_id INTEGER, label VARCHAR, "
    "started_at TIMESTAMP NOT NULL, duration_ms DOUBLE PRECISION NOT NULL, "
    "items INTEGER, per_item_ms DOUBLE PRECISION, detail JSONB, "
    "created_at TIMESTAMP DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS ix_task_timings_kind ON task_timings (kind)",
    "CREATE INDEX IF NOT EXISTS ix_task_timings_created ON task_timings (created_at)",
    "CREATE TABLE IF NOT EXISTS price_observations ("
    "id SERIAL PRIMARY KEY, lot_id INTEGER NOT NULL REFERENCES lots(id), "
    "value NUMERIC, method VARCHAR NOT NULL, price_source VARCHAR, "
    "comp_count INTEGER, evidence VARCHAR, chosen BOOLEAN DEFAULT TRUE, "
    "rejected BOOLEAN DEFAULT FALSE, note VARCHAR, query VARCHAR, "
    "created_at TIMESTAMP DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS ix_price_obs_lot ON price_observations (lot_id)",
    "CREATE INDEX IF NOT EXISTS ix_price_obs_evidence ON price_observations (evidence)",
    "CREATE INDEX IF NOT EXISTS ix_price_obs_created ON price_observations (created_at)",
    "CREATE TABLE IF NOT EXISTS comp_cache ("
    "query VARCHAR NOT NULL, source VARCHAR NOT NULL, payload JSONB, "
    "hits INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT now(), "
    "PRIMARY KEY (query, source))",
    "CREATE INDEX IF NOT EXISTS ix_comp_cache_created "
    "ON comp_cache (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_auctions_auctioneer_id ON auctions (auctioneer_id)",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS gold_check VARCHAR",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS gold_check_note VARCHAR",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS lot_number VARCHAR",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS estimate_low NUMERIC",
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS estimate_high NUMERIC",
    "CREATE TABLE IF NOT EXISTS dismissed_auctions ("
    "hibid_id INTEGER PRIMARY KEY, name VARCHAR, "
    "created_at TIMESTAMP DEFAULT now())",
    # Carry across dismissals made before this table existed, so the ✕ presses
    # the user already made keep holding.
    "INSERT INTO dismissed_auctions (hibid_id, name) "
    "SELECT hibid_id, name FROM auctions "
    "WHERE hidden IS TRUE AND hibid_id IS NOT NULL "
    "ON CONFLICT (hibid_id) DO NOTHING",
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS closing_digest_sent_at TIMESTAMP",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS comps JSONB",
    "CREATE TABLE IF NOT EXISTS estimate_obs ("
    "id SERIAL PRIMARY KEY, lot_id VARCHAR UNIQUE NOT NULL, "
    "auctioneer_id INTEGER NOT NULL, estimate_low NUMERIC NOT NULL, "
    "estimate_high NUMERIC, hammer NUMERIC NOT NULL, "
    "created_at TIMESTAMP DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS ix_estimate_obs_auctioneer "
    "ON estimate_obs (auctioneer_id)",
    "ALTER TABLE enrichment ADD COLUMN IF NOT EXISTS roi_reason VARCHAR",
    # Null out values already minted for placeholder rows ("More Lots
    # Loading" priced at $474.19 off a motorcycle part and a stamp album).
    # Idempotent: matches nothing once est_resale is null. The SQL pattern
    # is a conservative subset of pricing._PLACEHOLDER_RE — only the
    # unambiguous full-title shapes.
    r"UPDATE enrichment SET est_resale=NULL, price_low=NULL, price_high=NULL, "
    r"comp_count=0, comps=NULL, max_bid=NULL, est_roi=NULL, profit=NULL, "
    r"roi_status=NULL, gold_check=NULL, gold_check_note=NULL, "
    r"price_source='placeholder title — not an item, not priced' "
    r"FROM lots WHERE enrichment.lot_id = lots.id "
    r"AND enrichment.est_resale IS NOT NULL "
    r"AND lots.title ~* '^\s*(more +lots +(loading|coming|to +come|being +added)"
    r"[\s.!*_-]*|pick[ -]?up +(location|information|info|details|instructions)"
    r"[\s.!*_-]*|do +not +bid.*|(test|sample) +lot[\s.!*_-]*)$'",
    # Second wave: policy announcements ("2026 PICK UP POLICY UPDATE -
    # PLEASE READ!!!" carried $150 of comp value). Same idempotence.
    r"UPDATE enrichment SET est_resale=NULL, price_low=NULL, price_high=NULL, "
    r"comp_count=0, comps=NULL, max_bid=NULL, est_roi=NULL, profit=NULL, "
    r"roi_status=NULL, gold_check=NULL, gold_check_note=NULL, "
    r"price_source='placeholder title — not an item, not priced' "
    r"FROM lots WHERE enrichment.lot_id = lots.id "
    r"AND enrichment.est_resale IS NOT NULL "
    r"AND lots.title ~* '^\s*(\d{4} +)?(overview +of +)?(updated? +)?"
    r"(pick[ -]?up|shipping|payment|bidding|auction) +"
    r"(polic(y|ies)|options?|schedules?|updates?|changes?)"
    r"( *([&+]|and)? *(polic(y|ies)|options?|schedules?|updates?|changes?))*"
    r"( *[-:]* *please +read!*)?[\s.!*_-]*$'",
    # GovDeals: synthetic per-seller auctions carry their identity here
    # (hibid_id stays NULL, which is what keeps them out of the HiBid-only
    # workers).
    "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS external_id VARCHAR",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_auctions_external_id "
    "ON auctions (external_id)",
]

def _run_migrations() -> list[str]:
    """Apply pending migrations, returning the ones that didn't land.

    Each ALTER needs an ACCESS EXCLUSIVE lock. During a rolling deploy the
    previous container still holds connections to these tables, so the lock
    can't be taken — wait forever and the new server never binds its port
    (a 502 that reports as a successful deploy). Cap the wait instead and
    report what's still pending.
    """
    pending = []
    for stmt in _MIGRATIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET lock_timeout = '5000ms'"))
                conn.execute(text(stmt))
        except Exception as exc:  # noqa: BLE001
            pending.append(stmt)
            print(f"Migration pending: {stmt[:70]}… — {exc}")
    return pending


def _retry_migrations_in_background(pending: list[str]) -> None:
    """Keep retrying until they land. The blocker (the old container) goes
    away within a minute of a deploy, so this converges — and until it does
    the server is at least up and serving the endpoints that do work."""
    def worker():
        remaining = list(pending)
        for _ in range(60):          # ~10 minutes of patience
            time.sleep(10)
            still = []
            for stmt in remaining:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("SET lock_timeout = '5000ms'"))
                        conn.execute(text(stmt))
                    print(f"Migration applied on retry: {stmt[:70]}…")
                except Exception:  # noqa: BLE001
                    still.append(stmt)
            remaining = still
            if not remaining:
                return
        print(f"MIGRATIONS STILL PENDING after retries: {remaining}")

    threading.Thread(target=worker, daemon=True).start()


for attempt in range(10):
    try:
        Base.metadata.create_all(bind=engine)
        _pending = _run_migrations()
        if _pending:
            _retry_migrations_in_background(_pending)
        break
    except Exception as exc:  # noqa: BLE001
        if attempt == 9:
            raise
        print(f"Database not ready (attempt {attempt + 1}/10): {exc}")
        time.sleep(3)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup work, in the lifespan style (on_event is deprecated). The API
    owns the schema and nothing else.

    Background work lives in the worker process (`python -m app.worker`):
    enrichment, repricing, shipping analysis, bid refreshes, the maintenance
    loops and the closing-soon notifier. Running them here meant they shared
    this process's connection pool, and three long jobs could starve every
    request behind them — the backend wedged twice in one day before the
    split.
    """
    # One-time repair: closing dates ingested before the timezone fix were
    # stored as naive US-Central but compared against UTC, making auctions
    # look closed 5 hours early (the auto-flush once deleted a still-open
    # auction). Shift the old rows to UTC exactly once, guarded by a
    # settings flag so restarts can't re-apply it.
    from .services import settings as settings_store
    try:
        if not settings_store.get("tz_fix_2026_09"):
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE auctions SET closing_date = closing_date + interval '5 hours' "
                    "WHERE closing_date IS NOT NULL"))
            settings_store.set("tz_fix_2026_09", "done")
            print("Applied one-time closing-date timezone shift (+5h to UTC)")
    except Exception as exc:  # noqa: BLE001
        print(f"tz fix skipped: {exc}")

    # scan/import run inside a request handler, so any row of those kinds is
    # from a process that no longer exists. The reaper would get there
    # eventually, but only after the stale threshold — and these are known
    # dead the moment this process starts.
    from .workers.resume import clear_request_scoped_jobs
    clear_request_scoped_jobs()

    yield  # the app serves requests; nothing to do at shutdown


app = FastAPI(title="AuctionScout", lifespan=lifespan)

import os

# Deployed frontend origin(s), comma-separated — e.g.
# FRONTEND_ORIGIN=https://auctionscout-frontend.up.railway.app
_frontend_origins = [
    o.strip() for o in os.environ.get("FRONTEND_ORIGIN", "").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    # Dev looseness: the Vite dev server from the laptop (localhost) or a
    # phone on the same LAN. Either this regex OR the origins list may match.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):5173",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lots.router)
app.include_router(enrichment.router)
app.include_router(auctions.router)
app.include_router(govdeals.router)
app.include_router(publicsurplus.router)
app.include_router(vinted.router)
app.include_router(status.router)
app.include_router(events.router)


@app.get("/health")
def health():
    return {"status": "ok"}
