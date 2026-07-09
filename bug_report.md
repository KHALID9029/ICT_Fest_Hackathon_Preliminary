# Bug Report — CoWork (ICT Fest Preliminary)

Line numbers refer to the **original (buggy) code** as shipped. Every fix was
located and verified **black-box through the HTTP API** (probe scripts assert the
business rule before and after the fix, including concurrency hammers), and the
API contract (paths, status codes, error codes, JSON field names) is unchanged.

---

## Module 2 — Booking Engine (rules 1–7, 10–13)

### Bug 1 — Rule 1: input offsets dropped instead of converted to UTC
- **File/line:** `app/timeutils.py:13`
- **What/why:** `parse_input_datetime` did `dt.replace(tzinfo=None)` on offset-carrying
  inputs — it *stripped* the offset without converting, so `2026-07-12T10:00:00+06:00`
  was stored/compared as `10:00 UTC` instead of `04:00 UTC`. Every downstream rule
  (price window, overlap, quota, refund notice) then ran on the wrong instant.
  Observed: booking with `+06:00` start echoed back `10:00+00:00`.
- **Fix:** `dt.astimezone(timezone.utc).replace(tzinfo=None)` — normalize to UTC, then
  store naive.
- **Difficulty:** Easy

### Bug 2 — Rule 2: 300-second grace window on `start_time`
- **File/line:** `app/routers/bookings.py:86`
- **What/why:** `if start <= now - timedelta(seconds=300)` allowed bookings starting up
  to 5 minutes in the past. Rule 2: strictly in the future, **no grace window**.
  Observed: `start = now − 60s` → `201`.
- **Fix:** `if start <= now:`
- **Difficulty:** Easy

### Bug 3 — Rule 2: no minimum-duration check (`end <= start` accepted)
- **File/line:** `app/routers/bookings.py:93-94`
- **What/why:** only `duration_hours > MAX_DURATION_HOURS` was checked. Zero and
  negative whole-hour durations passed validation, so `end == start` and `end < start`
  produced `201` with `price_cents` of `0` or even negative. Rule 2: min 1h and
  `end_time` strictly after `start_time`.
- **Fix:** `if duration_hours < MIN_DURATION_HOURS or duration_hours > MAX_DURATION_HOURS:`
  (covers 0/negative → also enforces `end > start`; still `400 INVALID_BOOKING_WINDOW`).
- **Difficulty:** Easy

### Bug 4 — Rule 3: overlap comparison rejects back-to-back bookings
- **File/line:** `app/routers/bookings.py:50`
- **What/why:** `b.start_time <= end and start <= b.end_time` — the rule defines overlap
  with **strict** inequalities (`existing.start < new.end AND new.start < existing.end`);
  with `<=`, a booking starting exactly when another ends was rejected `409 ROOM_CONFLICT`.
  Observed: `[10:00,12:00)` then `[12:00,14:00)` → `409`.
- **Fix:** `if b.start_time < end and start < b.end_time:`
- **Difficulty:** Easy

### Bug 5 — Rules 3, 4 (+7, 12): booking creation check-then-insert race
- **File/line:** `app/routers/bookings.py:100-117` (conflict check at 100, quota at 103,
  insert/commit at 116-117; `_pricing_warmup`/`_quota_audit` sleeps at 48/69 widen the window)
- **What/why:** conflict and quota were read-checked and then the row inserted with no
  synchronization, so N simultaneous requests all passed the checks before any committed.
  Observed: 6 concurrent same-slot requests → six `201`s (rule requires exactly one);
  6 concurrent in-window requests by one user → six `201`s (quota 3).
- **Fix:** module-level `threading.Lock()` (`_booking_lock`) held across
  conflict-check → quota-check → insert → commit → stats/cache updates. The app is a
  single uvicorn worker (sync endpoints on a threadpool), so a process lock is the
  correct serialization point — same model the in-memory caches/stats already assume.
  Notification side effects stay **outside** the lock. Verified: 20 concurrent same-slot
  → exactly one `201` + 19 `409`; 10 concurrent in-window → exactly 3 `201`.
- **Difficulty:** Hard

### Bug 6 — Rule 5: rate-limiter lost-update race
- **File/line:** `app/services/ratelimit.py:18-26` (sleep at 21-23 widens the window)
- **What/why:** `record_and_check` read the user's bucket, slept 0.1s, then wrote it
  back — concurrent requests each read the same old bucket and overwrote each other's
  appends, so the counter never reached the limit. Observed: 25 concurrent POSTs →
  zero `429` (rule: max 20 per rolling 60s, all requests count).
- **Fix:** `threading.Lock()` around the read-trim-append-check sequence.
  Verified: 25 concurrent → exactly 20 `201` + 5 `429 RATE_LIMITED`; a request after
  61s idle succeeds again (rolling window intact).
- **Difficulty:** Hard

### Bug 7 — Rule 7: reference-code counter race → duplicate codes
- **File/line:** `app/services/reference.py:17-21` (sleep at 19 widens the window)
- **What/why:** `next_reference_code` read the counter, slept 0.12s, then incremented —
  concurrent creations all read the same value. Observed: 6 concurrent bookings all got
  `CW-001025`.
- **Fix:** `threading.Lock()` around read-increment-format. Verified: 15 concurrent
  creations → 15 unique codes.
- **Difficulty:** Medium

### Bug 8 — Rule 6: refund tier ladder wrong at both boundaries
- **File/line:** `app/routers/bookings.py:200-206`
- **What/why:** two defects in the tier selection:
  (a) `notice_hours = int(notice.total_seconds() // 3600)` **floors** the notice and then
  uses `> 48`, so notice in `[48h, 49h)` fell to the 50% tier (rule: `>= 48h` → 100%);
  (b) the `< 24h` branch was `refund_percent = 50` — the 0% tier never existed.
  Observed: notice 48h+40s → 50%; notice 10h → 50%.
- **Fix:** compare the timedelta directly:
  `notice >= timedelta(hours=48)` → 100, `elif notice >= timedelta(hours=24)` → 50,
  `else` → 0.
- **Difficulty:** Medium

### Bug 9 — Rule 6: banker's rounding on the refund amount
- **File/line:** `app/routers/bookings.py:208`
- **What/why:** `round(price * pct)` uses Python's round-half-to-even: 50% of 1001 →
  `round(500.5)` → **500**; the rule (and README example) require half-cents to round
  **up** → 501.
- **Fix:** response now returns the RefundLog amount computed with exact integer
  half-up arithmetic (see Bug 10), which also guarantees response == ledger.
- **Difficulty:** Medium

### Bug 10 — Rule 6: RefundLog amount computed differently from the response
- **File/line:** `app/services/refunds.py:15-17`
- **What/why:** `log_refund` went through float dollars and `int(...)` **truncation**, so
  the stored amount disagreed with the cancel response (rule: they must be equal, and
  half-cents round up). Observed: price 103, 50% → response `52`, RefundLog `51`.
- **Fix:** `amount_cents = (booking.price_cents * percent + 50) // 100` — exact integer
  half-cent-up rounding; `cancel_booking` returns `entry.amount_cents` so the two can
  never diverge.
- **Difficulty:** Medium

### Bug 11 — Rule 6: concurrent cancels → multiple refunds
- **File/line:** `app/routers/bookings.py:195-214` (status check at 195, refund write at
  210, status commit at 213-214, with `_settlement_pause` at 212 widening the window)
- **What/why:** status was read-checked (`confirmed`), then the refund logged and the
  status committed 0.12s later — N concurrent cancels all passed the check. Observed:
  6 concurrent cancels → six `200`s and **six RefundLog rows** (rule: exactly one
  RefundLog, concurrent cancels must yield `409 ALREADY_CANCELLED`).
- **Fix:** the whole fetch → status check → refund → commit sequence runs under
  `_booking_lock`; `booking.status = "cancelled"` is set **before** `log_refund` commits
  so the status flip and the ledger row land in one transaction. Verified: 12 concurrent
  cancels → exactly one `200` + 11 `409`, exactly one RefundLog equal to the response.
- **Difficulty:** Hard

### Bug 12 — Rule 10: members can read other members' bookings
- **File/line:** `app/routers/bookings.py:156-163`
- **What/why:** `GET /bookings/{id}` filtered only by org, missing the owner check that
  cancel has — any member could read another member's booking in the same org (rule:
  `404 BOOKING_NOT_FOUND`).
- **Fix:** added the same guard as cancel:
  `if user.role != "admin" and booking.user_id != user.id: 404 BOOKING_NOT_FOUND`.
- **Difficulty:** Medium

### Bug 13 — API contract: booking detail `start_time` overwritten with `created_at`
- **File/line:** `app/routers/bookings.py:166`
- **What/why:** after serializing, the handler did
  `response["start_time"] = iso_utc(booking.created_at)` — the detail endpoint returned
  the creation timestamp as the start time.
- **Fix:** removed the line (`serialize_booking` already sets `start_time` correctly).
- **Difficulty:** Easy

### Bug 14 — Rule 11: pagination broken three ways
- **File/line:** `app/routers/bookings.py:137-139`
- **What/why:** (a) `order_by(Booking.start_time.desc(), ...)` — rule requires
  **ascending** start_time; (b) `.offset(page * limit)` — page 1 skipped the first
  `limit` items (default request returned `items: []` while `total` was non-zero);
  (c) `.limit(10)` hardcoded — the `limit` parameter was ignored.
- **Fix:** `order_by(start_time.asc(), id.asc())`, `.offset((page - 1) * limit)`,
  `.limit(limit)`. Verified: pages `[(N−1)·L, N·L)`, tie on equal start_time ordered by
  id, no skip/repeat, `total` correct.
- **Difficulty:** Easy

### Bug 15 — Rule 12: creating a booking leaves the usage-report cache stale
- **File/line:** `app/routers/bookings.py:120-121`
- **What/why:** create invalidated only the availability cache; a cached
  `/admin/usage-report` kept serving pre-create numbers (rule: report reflects current
  state immediately). Observed: report cached with 1 booking, second booking created,
  report still showed 1.
- **Fix:** create also calls `cache.invalidate_report(user.org_id)`.
- **Difficulty:** Medium

### Bug 16 — Rule 13: cancelling a booking leaves the availability cache stale
- **File/line:** `app/routers/bookings.py:216-217`
- **What/why:** cancel invalidated only the report cache; a cached
  `/rooms/{id}/availability` kept showing the cancelled slot as busy. Observed:
  availability cached with 2 busy slots, one cancelled, still 2 busy.
- **Fix:** cancel also calls
  `cache.invalidate_availability(booking.room_id, booking.start_time.date().isoformat())`.
- **Difficulty:** Medium

### Verification (Module 2)
- 58-check black-box probe suite covering rules 1–7, 10–13 incl. boundary cases
  (back-to-back slots, exact 24h/48h notice, exactly-8h duration, half-cent rounding,
  window edge `(now, now+24h]`): **29/51 pre-fix → 58/58 post-fix** from a clean boot.
- Concurrency hammers: 20× same-slot create, 10× in-window quota burst, 25× rate-limit
  burst, 12× double-cancel, 15× reference codes — all invariants hold post-fix.
- `pytest` smoke test green; run inside the Docker image (grader path).

---

## Cross-module findings (observed while testing Module 2 — owned by other modules)

These were **not** fixed here (module ownership); reported to the owners with repros.

1. **`app/services/notifications.py:24-35` — Rule 16 (Module 3): lock-order deadlock.**
   `notify_created` acquires `_email_lock` → `_audit_lock`; `notify_cancelled` acquires
   `_audit_lock` → `_email_lock`. A concurrent create + cancel can each grab their first
   lock and wait forever on the other → the two worker threads hang **holding both
   locks**, after which every subsequent booking create/cancel blocks forever.
   Deterministic repro: call both functions concurrently from two threads — both still
   blocked after 5s. Fix suggestion: acquire the locks in the same order in both
   functions (or don't nest them).
2. **`app/services/stats.py:15-26` — Rule 14 (Module 3): read-modify-write race.**
   `record_create`/`record_cancel` read the counters, sleep 0.1s, then write back —
   concurrent bursts lose updates. Currently masked because `bookings.py` (the only
   writer) serializes these calls under its booking lock, but the race is latent in the
   file; a lock there (or recomputing from the DB) makes Rule 14 robust on its own.
3. **`app/routers/auth.py:32-43` — Rule 15 (Module 1): duplicate username returns the
   existing user (201) instead of `409 USERNAME_TAKEN`** — even when the request carries
   a different password (acts as an auth-less account probe).
   Repro: `POST /auth/register` twice with the same org+username → second returns 201.
4. **`app/routers/auth.py:81-93` — Rule 8 (Module 1): refresh tokens are not single-use.**
   `POST /auth/refresh` never invalidates the presented refresh token; reusing the same
   token returns 200 with fresh tokens (rule: reuse → 401).
   Repro: refresh twice with the same token → both 200.
