# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

CoWork is a multi-tenant coworking-space booking REST API (FastAPI + SQLAlchemy +
SQLite). But the primary task is **not building features — it's a black-box,
contract-preserving bug-fix challenge** (IUT ICT Fest hackathon). The shipped code
**intentionally contains bugs**; a grader builds the container and asserts behavior
only through the HTTP API against the 16 business rules and API contract.

**Do not assume the current code is correct.** `REQUIREMENTS.md` (Sections 3–4) and
the `README.md` contract are the **oracle** — wherever observable API behavior
deviates from a rule, that's a bug to fix. Read `skills/cowork-bughunt/SKILL.md` for
the bug-hunting workflow before proposing a fix, and see `MODULE_DIVISION.md` for how
files map to rules/owners.

### Hard constraints (from the grader)
- **Never change the contract:** paths, HTTP status codes, error `code` strings, and
  JSON field names are frozen. A change that alters any of these is wrong even if it
  "fixes" behavior.
- **Fix, don't refactor or add features.** Make the smallest change per bug that
  restores the named rule. Every fix must map to a specific rule number.
- Errors are `{"detail": <str>, "code": <CODE>}` via `AppError`; framework validation
  errors (422) use FastAPI's default shape.

## Commands

```bash
# Run the API (Docker — same path the grader uses)
docker compose up --build          # serves http://localhost:8000, /docs for Swagger

# Run locally without Docker (Python 3.11)
pip install -r requirements.txt
uvicorn app.main:app --reload

# Smoke test (single happy-path golden flow; not full coverage)
pytest
pytest tests/test_smoke.py::test_core_flow   # a single test
```

There are no lint/format/type-check configs. The DB (`cowork.db`, SQLite file) and its
schema are created automatically at startup — no migrations or seed scripts. Delete
`cowork.db` to reset state.

## Architecture

Request flow: `routers/*` (HTTP, auth dependency, tenant filtering) → `services/*`
(business logic) → `models.py` (SQLAlchemy) → SQLite, serialized back through
`serializers.py`. `app/main.py` wires routers and registers the `AppError` handler.

Cross-cutting pieces that create the trickiest (highest-value) bugs:

- **Auth dependency** — `get_current_user` in `app/auth.py` is imported by every
  router and enforces JWT validation + the logout blacklist. It is the shared choke
  point for tenant identity.
- **In-memory global state, separate from the DB** — `app/cache.py`
  (report/availability caches), `services/stats.py` (per-room count/revenue),
  `services/ratelimit.py` (per-user rolling window), and `services/reference.py`
  (reference-code counter) all hold process-global dicts. These are written by
  booking create/cancel but read by rooms/admin endpoints — so a booking mutation
  that fails to invalidate/update the right key produces stale reports, stats, or
  availability across module boundaries.
- **Concurrency surfaces** — the booking create/cancel paths and the in-memory
  services perform read-check-then-write sequences (several contain deliberate
  `time.sleep(...)` "pause" helpers that widen the race window). Rules 3–7 and 16
  require these to hold under simultaneous requests; verify concurrency bugs by firing
  N parallel requests, not one sequential one.
- **Datetime handling** — `app/timeutils.py` normalizes input to naive-UTC for storage
  and renders responses as UTC with an explicit designator. Storage is naive UTC;
  comparisons use `datetime.utcnow()`. Mismatches here cascade into pricing, quota,
  overlap, and refund-tier logic.

Tenancy is enforced per-query: every data access filters by the caller's `org_id`
(often via a join to `Room`), and cross-org IDs must resolve as 404, not 403.

## Working notes
- `bug_report.md` in the repo root (file/line, rule violated, why, fix, difficulty) is
  the scoring tie-breaker — keep it current as you fix.
- When touching a shared file (`auth.py`, `cache.py`, `models.py`, `schemas.py`),
  remember other modules depend on its exact shape — route schema changes through the
  Module 3 owner per `MODULE_DIVISION.md`.
