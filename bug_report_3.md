# Bug Report

Each entry: file/line (original buggy code), rule violated, what was wrong and why it
caused incorrect behavior, how it was fixed, difficulty. All fixes verified black-box
against the running Docker container (same path the grader uses), including concurrent
probes for the concurrency rules.

---

## Module 3 — Rooms, Reporting & Platform

### Bug M3-1 — Lock-order deadlock hangs the service (Rule 16: Liveness) — **Hard**

- **File/line:** `app/services/notifications.py:24-35`
- **What was wrong:** `notify_created` acquires `_email_lock` then `_audit_lock`
  (nested); `notify_cancelled` acquired them in the **opposite** order
  (`_audit_lock` then `_email_lock`). With the 0.12s/0.1s simulated I/O inside the
  critical sections, a booking create running concurrently with a cancel routinely
  reached the state "create holds email lock, wants audit lock; cancel holds audit
  lock, wants email lock" — a classic ABBA deadlock. Both worker threads block
  forever, and every subsequent create/cancel queues up behind the dead locks, so
  the whole booking pipeline hangs permanently.
- **Proof (before fix):** firing 6 creates + 6 cancels concurrently left 11/12
  requests hung past a 25s timeout; the container had to be restarted.
- **Fix:** `notify_cancelled` no longer holds one lock while acquiring the other:
  it takes `_audit_lock`, writes the audit entry, releases it, then takes
  `_email_lock` and sends. No thread ever holds one of the two locks while waiting
  for the other, so circular wait is impossible. Operation order (audit first,
  then email) is unchanged.
- **Proof (after fix):** same 6+6 concurrent burst: all 12 requests complete
  (201/200); repeated bursts of 8 creates + 8 cancels also complete; `/health`
  stays responsive.

### Bug M3-2 — Lost updates in live room stats (Rule 14: Room stats) — **Hard**

- **File/line:** `app/services/stats.py:15-26`
- **What was wrong:** `record_create`/`record_cancel` did read → 0.1s pause →
  write on the shared `_stats` dict with no synchronization. Concurrent bookings
  read the same starting count/revenue, then each wrote back "old value + 1", so
  all but one of the overlapping updates were silently lost and
  `GET /rooms/{id}/stats` disagreed with the bookings table.
- **Proof (before fix):** 6 concurrent creates on one room (all 201) →
  `total_confirmed_bookings` was 2 instead of 6, revenue 1000 instead of 3000.
- **Fix:** added a module-level `threading.Lock` and made the whole
  read-modify-write in both functions atomic under it.
- **Proof (after fix):** 6 concurrent creates → count 6 / revenue 3000; a mixed
  burst (8 concurrent creates racing 8 concurrent cancels across 8 rooms, two
  users) leaves every room's stats exactly equal to its confirmed bookings
  (count 2, revenue 1400 per room); cancel correctly decrements.

### Bug M3-3 — Usage report stale after booking create (Rule 12: Usage report) — **Medium**

- **File/line:** `app/cache.py:20-22` / `app/cache.py:33-34` (root trigger:
  `app/routers/bookings.py:121` only calls `cache.invalidate_availability(...)`
  on create — Module 2's file, not edited)
- **What was wrong:** creating a booking never dropped cached usage reports. Once
  an admin had requested `/admin/usage-report` for a range, any booking created
  afterwards was invisible in that range's report forever — the cache served the
  old result, violating "must reflect current state immediately".
- **Proof (before fix):** GET report (0 bookings, cached) → create booking in
  range → GET report again → still 0 bookings / 0 revenue.
- **Fix (kept inside Module 3 files):** the booking-create path's only cache call
  is `invalidate_availability`, so that function now also drops the report cache.
  Also made all cache operations take a lock — the previous unsynchronized
  iterate-and-pop over dicts shared across request threads could raise
  "dictionary changed size during iteration" mid-request.
- **Proof (after fix):** report reflects a new booking immediately; `[from, to]`
  inclusivity re-verified (booking on the `to` date is counted); zero-booking
  rooms still listed; cancelled bookings still excluded.
- **Note for Module 2 owner:** the canonical fix is adding
  `cache.invalidate_report(user.org_id)` after commit in the create path
  (`app/routers/bookings.py:121`); the cache-side fix is equivalent and
  composes safely with it.

### Bug M3-4 — Availability stale after cancel (Rule 13: Availability) — **Medium**

- **File/line:** `app/cache.py:20-22` (root trigger: `app/routers/bookings.py:217`
  only calls `cache.invalidate_report(...)` on cancel — Module 2's file, not edited)
- **What was wrong:** cancelling a booking never dropped the cached availability
  for its date, so `GET /rooms/{id}/availability` kept returning the cancelled
  booking as a busy interval indefinitely.
- **Proof (before fix):** book → GET availability (1 busy interval, cached) →
  cancel → GET availability → still 1 busy interval.
- **Fix (kept inside Module 3 files):** the cancel path's only cache call is
  `invalidate_report`, so that function now also drops the availability cache.
- **Proof (after fix):** availability empties immediately after cancel; intervals
  remain sorted ascending with UTC-designated datetimes; after the mixed
  concurrent burst, availability for each date exactly matches the surviving
  confirmed bookings.
- **Note for Module 2 owner:** the canonical fix is adding
  `cache.invalidate_availability(booking.room_id, booking.start_time.date().isoformat())`
  after commit in the cancel path (`app/routers/bookings.py:217`).

### Bug M3-5 — Cross-org data leak in CSV export (Rule 9: Multi-tenancy) — **Medium**

- **File/line:** `app/services/export.py:48-52` (helper at 22-29)
- **What was wrong:** with `include_all=true&room_id=<id>`, the export used
  `fetch_bookings_raw`, which filters only by `room_id` with **no org filter**.
  An admin of org A could pass a room id belonging to org B and download all of
  B's bookings for that room (ids, user ids, times, prices) — cross-org resource
  ids must behave as non-existent.
- **Proof (before fix):** org A admin exporting with org B's room id received
  B's booking rows in the CSV.
- **Fix:** `include_all` now only widens the export from "caller's own bookings"
  to "all users' bookings", always through the org-scoped query
  (`_fetch_scoped(db, org_id, None, room_id)`). A cross-org room id yields a
  header-only CSV.
- **Proof (after fix):** same probe returns only the header row; same-org
  exports (with/without `room_id`, with/without `include_all`) unchanged; exact
  header `id,reference_code,room_id,user_id,start_time,end_time,status,price_cents`
  preserved; non-admin still gets `403 FORBIDDEN`.

### Bug M3-6 — New room missing from cached usage report (Rule 12) — **Easy**

- **File/line:** `app/routers/rooms.py:42-57`
- **What was wrong:** Rule 12 requires the report to list every room in the org
  *including rooms with zero bookings*, immediately. `create_room` never
  invalidated cached reports, so a range requested before the room was created
  kept serving a report without it.
- **Proof (before fix):** GET report (cached) → POST /rooms → GET report → new
  room absent.
- **Fix:** `create_room` calls `cache.invalidate_report(admin.org_id)` after
  commit.
- **Proof (after fix):** the new room appears with `confirmed_bookings: 0,
  revenue_cents: 0` immediately.

### Module 3 regression evidence

- Full probe suite (Rules 9, 12, 13, 14 + response/error shapes): 29/29 checks pass.
- Liveness burst probes: no hangs, `/health` responsive throughout.
- `pytest` smoke test: passing.
- Contract untouched: no path, status code, error `code`, or JSON field name changed.

---

## Cross-module findings (observed while tracing Module 3 data flow — NOT fixed here; owners: Modules 1 & 2)

**Module 2 (`app/routers/bookings.py`, `app/services/refunds.py`, `app/timeutils.py`, `app/services/ratelimit.py`, `app/services/reference.py`):**

- `timeutils.py:13` — offset-carrying datetimes are stripped, not converted to UTC (Rule 1).
- `bookings.py:86` — 300s grace window; start must be strictly future (Rule 2).
- `bookings.py:89-94` — no minimum-duration check: 0-hour and end≤start bookings accepted (Rule 2).
- `bookings.py:50` — overlap uses `<=`, rejecting back-to-back bookings (Rule 3); conflict check also races under concurrency (no guard around check-then-insert).
- `bookings.py:166` — `GET /bookings/{id}` overwrites `start_time` with `created_at` (contract).
- `bookings.py:137-139` — list: `desc` ordering, `offset(page * limit)` (skips page 1), hardcoded `limit(10)` (Rule 11).
- `bookings.py:201-206` — refund tiers: `> 48` should be `>= 48`; the `< 24h` branch returns 50 instead of 0 (Rule 6).
- `bookings.py:208` + `refunds.py:17` — response uses banker's `round()` while the RefundLog stores `int(...)` truncation: half-cents must round up and the two amounts must match (Rule 6).
- `bookings.py:195-214` — cancel is check-then-write with `_settlement_pause()` in between: concurrent cancels double-refund (Rule 6) and double-decrement room stats (breaks Rule 14 from outside Module 3).
- `ratelimit.py:18-26` / `reference.py:17-21` — unguarded read-modify-write: rate-limit undercounts and reference codes duplicate under concurrency (Rules 5, 7). (`models.py` has no unique constraint on `reference_code`; left unchanged — the fix belongs in `reference.py` generation.)

**Module 1 (`app/auth.py`):**

- `auth.py:50` — `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)` yields 900 *minutes*; Rule 8 requires exactly 900 seconds.
- `auth.py:85-98` — logout stores the token's `jti` in the blacklist but the check compares `payload.get("sub")` against it, so revocation never matches and logged-out tokens keep working (Rule 8).
