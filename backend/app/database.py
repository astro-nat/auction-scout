import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://htown:devpassword@localhost:5432/auction_scout",
)

# Managed Postgres providers (Railway, Heroku, Render) hand out postgres:// or
# postgresql:// URLs; SQLAlchemy needs the psycopg2 dialect spelled out.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Connection pool, sized on purpose rather than left at SQLAlchemy's default
# of 5 + 10 overflow.
#
# That default was what made the app fall over: three long jobs (reprice,
# ship-analysis, bid-refresh) each keep a transaction open across their slow
# HTTP calls, every lot opens two more short sessions for job bookkeeping,
# and the UI polls /lots and /status on top. Past 15 the next caller blocks
# for 30s and then throws — which killed the worker mid-loop and left a job
# row that showed progress forever without advancing.
#
# pool_recycle because managed Postgres drops idle connections; without it
# the first query after a quiet spell fails on a stale socket.
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "10"))

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    # Fail fast instead of tying up a request thread for half a minute —
    # a caller that can't get a connection should say so while the UI is
    # still listening.
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — one session per request, always closed after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
