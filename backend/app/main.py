import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from . import models  # noqa: F401 — import registers models on Base before create_all
from .routers import lots, enrichment, auctions

# Dev convenience only — creates tables from models if they don't exist.
# Once this is a real app with data you care about, replace this with Alembic
# migrations instead of letting SQLAlchemy auto-create/alter tables.
# Retried because managed Postgres (Railway etc.) can take a few seconds to
# accept connections at deploy time — crashing on the first refusal means an
# endless crash-loop that looks like a broken deploy.
for attempt in range(10):
    try:
        Base.metadata.create_all(bind=engine)
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
