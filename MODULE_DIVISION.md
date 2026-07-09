# CoWork Hackathon — 3-Module Work Split

Bug-fix challenge. Each member OWNS a set of files (no overlap = no merge conflicts),
and is responsible for finding/fixing bugs and testing the business rules mapped to it.

---

## Module 1 — Identity & Access (Auth · Registration · Multi-Tenancy)

**Owns (edit these):**
- `app/auth.py` — JWT create/verify, password hashing, `get_current_user`, token blacklist
- `app/routers/auth.py` — register, login, refresh, logout
- `app/config.py`

**Business rules to verify & test:**
- **8. Auth** — HS256 claims (`sub, org, role, jti, iat, exp, type`); access exp = 900s exactly; refresh = 7 days; logout invalidates access token (->401); refresh single-use / rotates and invalidates old (reuse ->401)
- **15. Registration** — unknown org -> creates org + admin; known org -> joins as member; dup username -> 409 `USERNAME_TAKEN`
- **9. Multi-tenancy** — tenant isolation on the auth path; cross-org IDs behave as 404 (coordinate: also touches Modules 2 & 3)

**Endpoints:** `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`

---

## Module 2 — Booking Engine (Create -> Cancel core)

**Owns (edit these):**
- `app/routers/bookings.py` — all `/bookings` endpoints (densest file)
- `app/services/refunds.py`
- `app/services/ratelimit.py`
- `app/services/reference.py`
- `app/timeutils.py`

**Business rules to verify & test:**
- **1. Datetimes** — ISO 8601; offsets -> UTC; naive treated as UTC; responses UTC with designator
- **2. Booking price / window** — `price = rate x hours`; whole hours 1-8; end > start; start strictly future
- **3. No double-booking** — overlap rule, back-to-back allowed -> 409 `ROOM_CONFLICT` (concurrency)
- **4. Booking quota** — <=3 confirmed in (now, now+24h] per member -> 409 `QUOTA_EXCEEDED` (concurrency)
- **5. Rate limit** — 20 req / rolling 60s per user on POST /bookings -> 429 `RATE_LIMITED` (concurrency)
- **6. Cancellation refund** — owner/admin only; 48h->100%, 24-48h->50%, <24h->0%; half-cent rounds up; one RefundLog; 409 `ALREADY_CANCELLED` (concurrency)
- **7. Reference codes** — unique under concurrent creation
- **10. Booking visibility** — members see/cancel only own (else 404 `BOOKING_NOT_FOUND`); admins any in org
- **11. Pagination & ordering** — page/limit, sort by start_time then id, no skip/repeat, includes `total`

**Endpoints:** `POST /bookings`, `GET /bookings`, `GET /bookings/{id}`, `POST /bookings/{id}/cancel`

---

## Module 3 — Rooms, Reporting & Platform

**Owns (edit these):**
- `app/routers/rooms.py` — list/create rooms, availability, stats
- `app/routers/admin.py` — usage-report, export
- `app/services/stats.py`, `app/services/export.py`, `app/services/notifications.py`
- `app/cache.py`, `app/serializers.py`, `app/routers/health.py`
- Shared foundation (maintainer): `app/models.py`, `app/schemas.py`, `app/database.py`, `app/main.py`, `app/errors.py`

**Business rules to verify & test:**
- **12. Usage report** — per-room (incl. zero-booking rooms) count + revenue for `[from,to]`; cancelled excluded; immediate
- **13. Availability** — confirmed bookings starting on UTC date as busy intervals, sorted, immediate
- **14. Room stats** — live confirmed count + revenue; cancel decrements; always consistent (incl. concurrency)
- **16. Liveness** — no concurrent requests may hang the service
- Room create/list, CSV export exact header, error/response shapes

**Endpoints:** `GET/POST /rooms`, `/rooms/{id}/availability`, `/rooms/{id}/stats`, `/admin/usage-report`, `/admin/export`, `/health`

---

## Shared touchpoints (coordinate before editing)
- `get_current_user` (Module 1) is imported by every router — changes affect everyone.
- `app/cache.py` (Module 3) is invalidated by booking create/cancel (Module 2). Stale reports/stats/availability after a booking = cross-module cache-invalidation bug.
- `app/models.py` / `app/schemas.py` (Module 3) define shared shapes — Modules 1 & 2 read them; route schema changes through the Module 3 owner.

## Workflow reminders (from the PDF)
- Fork + "leave fork network" BEFORE editing.
- Don't refactor or change the API contract — only fix broken behavior.
- Keep `bug_report.md` in repo root (file, line, what/why, how fixed) — final tie-breaker.
- Points: Easy 3 · Medium 5 · Hard 10.
