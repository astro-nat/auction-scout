import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from . import models  # noqa: F401 — import registers models on Base before create_all
from .routers import lots, enrichment, auctions, status

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

app = FastAPI(title="AuctionScout")

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
app.include_router(status.router)


@app.on_event("startup")
def recover_orphaned_jobs():
    """BackgroundTasks don't survive a restart/deploy, but the work they were
    doing left its plan in the DB — jobs-table rows with payloads, lots still
    marked 'queued'. Pick it all back up instead of stranding it (this once
    lost 1,000+ auctions of a shipping-analysis run to a frontend deploy).
    Imported here, not top-level: workers.enrich instantiates the Anthropic
    client at import time, and module import order shouldn't depend on it."""
    from .workers.resume import resume_interrupted_work
    resume_interrupted_work()
    # Phone alerts for watched lots closing soon (no-op without NTFY_TOPIC).
    from .workers.notify import start_notifier
    start_notifier()


@app.get("/health")
def health():
    return {"status": "ok"}
