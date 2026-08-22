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
]

for attempt in range(10):
    try:
        Base.metadata.create_all(bind=engine)
        # Each ALTER needs an exclusive table lock; if another connection
        # holds the table (e.g. the previous deploy's pool), waiting forever
        # here silently blocks the server from ever starting. Give each
        # statement 5s, then skip — it'll succeed on a later boot.
        for stmt in _MIGRATIONS:
            try:
                with engine.begin() as conn:
                    conn.execute(text("SET lock_timeout = '5000ms'"))
                    conn.execute(text(stmt))
            except Exception as mig_exc:  # noqa: BLE001
                print(f"Migration skipped (will retry next boot): {stmt[:60]}… — {mig_exc}")
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
    """Reset lots stuck in 'queued' back to 'pending'. BackgroundTasks don't
    survive a restart/reload, so anything still queued at startup was
    interrupted mid-batch — make it re-runnable instead of stranded."""
    from sqlalchemy import update
    from .database import SessionLocal
    db = SessionLocal()
    try:
        result = db.execute(
            update(models.Enrichment)
            .where(models.Enrichment.status == "queued")
            .values(status="pending")
        )
        db.commit()
        if result.rowcount:
            print(f"Recovered {result.rowcount} enrichments orphaned by restart")
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
