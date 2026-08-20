# Migrating Off Streamlit → FastAPI + React + Postgres + Nginx
### A build plan and a learning plan, run side by side

**Context:** current tool is a Streamlit app doing HiBid lot scraping + image/text enrichment (BOLO matching, ROI scoring) that fails silently on the published deployment. Goal: rebuild as a real service, and use the rebuild to get genuinely better at end-to-end development.

**How to use this document:** don't try to do all of Phase 0 before starting Phase 1. Learn SQL *while* building the schema, learn FastAPI *while* building the first endpoint. The project is the curriculum.

---

## Guiding principle

Migrate in **thin vertical slices**, not by layer. Don't build all of Postgres, then all of FastAPI, then all of React. Instead: get *one* piece of data (say, a single auction's lots) flowing all the way from Postgres → FastAPI → browser before adding the next feature. Every slice should leave you with something that runs, even if it's ugly.

---

## Phase 0 — Foundations (ongoing, in parallel with everything else)

These aren't a gate you pass before starting; they're skills you sharpen *as* you hit them in the phases below.

| Skill | Why it matters here | How to learn it |
|---|---|---|
| SQL (real SQL, not ORM) | Every phase touches Postgres | Write your `lots`/`enrichment` schema by hand first in raw SQL before touching an ORM. Use `EXPLAIN ANALYZE` once you have >1000 rows loaded. |
| HTTP fundamentals | FastAPI is just HTTP handlers | Read request/response cycle basics once; the rest clicks by using FastAPI's `/docs` and watching real requests in browser dev tools |
| Git branching | You'll break things mid-migration | Branch per phase (`feat/postgres-schema`, `feat/fastapi-lots-endpoint`), never commit directly to main once this is a real service |
| Reading others' code | You'll copy patterns from docs/repos | When you copy a FastAPI example, don't just paste it — trace what each line does before running it |

**No dedicated study block needed** — budget ~30 min before each new phase to skim the relevant concept, then learn the rest by doing.

---

## Phase 1 — Postgres schema (Week 1)

**Goal:** replace the CSV export with real tables. This alone fixes your biggest current pain — no way to know what's already enriched vs. what failed.

**Build:**
- Run Postgres locally via Docker: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=dev postgres:16`
- Design 3 tables by hand, in raw SQL, before any ORM:
  - `lots` — the raw scrape fields (lot_id, auction, title, category, current_bid, next_bid, description, etc.)
  - `enrichment` — foreign key to `lots.lot_id`, columns for bolo_brand/tier/target_buy, verdict, status (`pending` / `success` / `failed`), last_attempted_at, error_message
  - `auctions` — auction_id, name, closing_date
- Load your existing CSV into it once (`\copy` in `psql`, or pandas `.to_sql`), just to see real data in real tables
- Write 5 queries by hand: all pending enrichment, all failed enrichment with error reason, lots meeting a melt-value ROI threshold via `SUMPRODUCT`-style SQL math, etc.

**Learn alongside this:** `SELECT`/`JOIN`/`WHERE`/`GROUP BY`, primary/foreign keys, why `status` columns beat re-scanning everything every run (this is literally what silently broke in Streamlit — no persisted state of what succeeded).

**Done when:** you can answer "which lots still need enrichment?" with one SQL query instead of re-running the whole pipeline.

---

## Phase 2 — FastAPI backend, no frontend (Week 2)

**Goal:** a backend that reads/writes Postgres, provable entirely through `/docs` — no UI required yet.

**Build:**
```
app/
  main.py
  database.py       # SQLAlchemy engine/session
  models.py          # Lot, Enrichment, Auction ORM models
  schemas.py          # Pydantic request/response shapes
  routers/
    lots.py           # GET /lots, GET /lots/{id}
    enrichment.py      # POST /lots/{id}/enrich
```
- `GET /lots` — filterable by auction, category, enrichment status
- `GET /lots/{id}` — single lot detail
- `POST /lots/{id}/enrich` — *for now*, calls your image/text API synchronously and writes the result (you'll fix this in Phase 3)
- Test everything with the auto `/docs` UI (Swagger) — hit each endpoint, confirm it returns/writes what you expect

**Learn alongside this:** path/query params, Pydantic validation, dependency injection (`Depends()` for DB sessions) — don't skip understanding *why* `Depends` exists, it's the part that feels like magic until it doesn't.

**Done when:** you can enrich a single lot via `/docs` and see the result land correctly in Postgres.

---

## Phase 3 — Background jobs + AI enrichment (Week 3)

**Goal:** fix the actual root cause of your Streamlit failures — long, flaky AI API calls blocking the response cycle — and this is where the AI/vision integration itself lives.

**Where the AI call physically sits:**
```
React → POST /api/lots/{id}/enrich → FastAPI (enqueues job, returns instantly)
                                            │
                                            ▼
                              Worker process (Arq/Celery) ← Claude/GPT-4o call happens HERE
                                            │
                                            ▼
                                     Postgres (result + status written)
```
FastAPI never talks to the AI API directly — it just drops a job on the queue. The worker is the only thing that ever imports `anthropic`/`openai`.

**Build:**
- Start simple: `BackgroundTasks` built into FastAPI — `POST /lots/{id}/enrich` returns immediately (`status: queued`), the actual API call happens after the response is sent
- Once that works, graduate to a real queue if you want retries/concurrency control/visibility: **Arq** (lightweight, async-native, pairs well with FastAPI) or **Celery + Redis** (heavier, more mature, more tutorials)
- Write the enrichment worker function itself:
  - Text pass first (cheap, fast) — brand/tier/target-price from the description alone
  - Image/vision pass only for lots the text pass couldn't resolve confidently — send the photo as base64 to Claude/GPT-4o, prompt it to return JSON only, parse and store
  - Retry with backoff (2-3 attempts) before marking `status=failed`, with a real error message — this replaces your silent `*_api_failed` rows
  - Cache by image URL hash so a re-pull of the same auction doesn't re-pay for the same photo
  - Log tokens/cost per call in a column — vision calls aren't free at 800 lots × 2 auctions
- Add a `POST /auctions/{id}/enrich-all` endpoint that queues every pending lot, worker pool sized to your API rate limit so you're not firing 800 calls at once

**Learn alongside this:** the difference between sync and async in Python (`async def`), what a task queue actually is conceptually, idempotency (why it's safe to re-run enrichment on a lot that's already `status=failed`), structured-output prompting (asking a model to return only JSON matching a schema).

**Done when:** you can kick off enrichment for a whole 800-lot auction, close the browser tab, come back later, and see accurate per-lot status — including real error messages for anything that failed, and BOLO/tier results actually populated.

---

## Phase 4 — React frontend (Weeks 4–5, budget more time here — it's the least familiar)

**Goal:** something that replaces the Streamlit table/filter UI, calling your own API.

**Build:**
- `npm create vite@latest` → React + JS (skip TypeScript for v1, add it later once React itself feels normal)
- One page first: table of lots, `fetch('/api/lots')`, render rows — no styling, no filters, just prove data flows
- Add filtering/sorting client-side once the basic table works
- Add an "Enrich" button per row that calls `POST /api/lots/{id}/enrich`, then polls `GET /api/lots/{id}` every few seconds until status changes — this is where the background-job architecture actually pays off visibly
- Recreate your ROI/target-price view as a React table, sourced from the API instead of a static export

**Learn alongside this:** `useState`/`useEffect`, why React re-renders instead of re-running everything (the direct fix for Streamlit's full-script-rerun problem), fetch/promises/async in JS.

**Done when:** you can browse lots, trigger enrichment, and watch status update — without touching Postgres or FastAPI directly.

---

## Phase 5 — Nginx + Docker Compose (Week 6)

**Goal:** one command runs the whole stack.

**Build:**
- `docker-compose.yml` with 4 services: `postgres`, `api` (FastAPI), `web` (React build), `nginx`
- Nginx config: route `/api/*` → FastAPI container, everything else → static React build
- `docker compose up` should bring up the entire app from a clean checkout

**Learn alongside this:** what a reverse proxy actually does (you already understand this conceptually from our earlier conversation — now do it), Docker networking basics (why containers can reach each other by service name).

**Done when:** a fresh clone of the repo + `docker compose up` gives you the full working app, no manual setup steps.

---

## Phase 6 — Deploy (Week 7)

**Goal:** it's live somewhere, reliably.

**Recommended path, in order:**
1. **Railway or Render first** — point at your repo, let them run the Compose setup, use their managed Postgres. Fast feedback, low ops overhead — validates the whole stack works outside your laptop.
2. **A DigitalOcean/Linode VPS second**, once step 1 works — install Docker yourself, `docker compose up -d`, point a domain, get HTTPS via Certbot. This is where you actually learn the ops layer instead of having a platform hide it.

**Learn alongside this:** environment variables/secrets management in production (not the same as your local `.env`), basic logging (so a failure shows up somewhere you can read it — this is the direct fix for "I had no idea it was failing until I looked at the CSV").

**AWS deliberately deferred:** AWS (ECS/EC2, RDS, IAM, VPC) is a real option here but a much bigger surface area, easy to burn a weekend on IAM permissions or surprise costs (idle NAT gateways, etc.), and it stacks a second learning curve on top of FastAPI/React while you're still learning those. Ship on Railway/Render → VPS first; treat an AWS redeploy of this *same, already-working* app as its own standalone follow-up project once the app itself isn't the unknown anymore.

---

## Migration mechanics (how to cut over without losing your current tool)

1. Keep the Streamlit app running as-is while you build — don't touch it.
2. Build Phases 1–3 against a copy of your real CSV data, not live traffic.
3. Once Phase 4 gives you feature parity with what you actually use day-to-day (browse lots, see enrichment status, see ROI targets), run both in parallel for one real auction cycle — compare outputs.
4. Only retire Streamlit once the new stack has handled one full real auction end-to-end without you needing to fall back.

---

## Realistic pacing

Given DASC 5133, two ventures, and Reboost scouting already on your plate — this is written as ~7 weeks at a few hours a week, not a sprint. If a phase takes two weeks instead of one, that's normal; the ordering matters more than the speed. Each phase is independently useful even if you stop after it — Phase 1 alone (real Postgres tables) already fixes your worst current pain.

## Glossary — terms that came up

- **VPS (Virtual Private Server):** a rented slice of a physical server (DigitalOcean, Linode, Hetzner) that boots as a bare machine with root access — nothing pre-installed. You SSH in and set up Docker, Nginx, etc. yourself. Different from Railway/Render, which run your containers for you without ever handing you the underlying machine.
- **Docker:** packages an app + its exact environment (OS, language version, dependencies) into a portable, isolated unit (a container), so it runs identically on your laptop, a teammate's machine, or a VPS. A `Dockerfile` is the recipe; an image is the built package; a container is a running instance of it.
- **Docker Compose:** the tool that starts several containers together (Postgres, FastAPI, React build, Nginx) on one shared network with a single `docker compose up`.
- **Makefile vs. Dockerfile:** a Makefile automates *commands* you'd otherwise type by hand, run directly on whatever machine invokes it (no isolation). A Dockerfile defines a whole *isolated environment* from scratch. They're often used together — a `make build` target that's just a shortcut for the full `docker build` command.

## Quick reference — what to Google when stuck

- Postgres: "postgres explain analyze slow query"
- FastAPI: official docs at `fastapi.tiangolo.com` — unusually good, read before searching elsewhere
- Background jobs: "arq python fastapi tutorial" or "celery fastapi background tasks"
- React: `react.dev` — the new official docs are written for exactly this kind of self-taught path
- Docker Compose: "docker compose fastapi postgres nginx example" — many near-identical starter repos exist to read, not necessarily copy
