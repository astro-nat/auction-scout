from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from . import models  # noqa: F401 — import registers models on Base before create_all
from .routers import lots, enrichment, auctions

# Dev convenience only — creates tables from models if they don't exist.
# Once this is a real app with data you care about, replace this with Alembic
# migrations instead of letting SQLAlchemy auto-create/alter tables.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AuctionScout")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
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
