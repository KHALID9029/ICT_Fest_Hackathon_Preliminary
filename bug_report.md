# Bug Report — CoWork (ICT Fest Preliminary)


## Rule 1 — Datetimes

### Bug 1 — Input UTC offsets were dropped instead of converted
- **File/line:** `app/timeutils.py:13`
- **What/why:** `parse_input_datetime` called `dt.replace(tzinfo=None)` on
  offset-carrying input — it stripped the offset without converting, so
  `2026-07-12T10:00:00+06:00` was stored/compared as `10:00 UTC` instead of
  `04:00 UTC`. Every downstream rule (price window, overlap, quota, refund notice)
  then ran on the wrong instant.
- **Fix:** `dt.astimezone(timezone.utc).replace(tzinfo=None)` — convert to UTC first,
  then strip tzinfo for naive storage.

---

## Rule 2 — Booking price & window

### Bug 2 — 300-second grace window let bookings start in the past
- **File/line:** `app/routers/bookings.py:91`
- **What/why:** the check was `if start <= now - timedelta(seconds=300)`, allowing
  bookings starting up to 5 minutes in the past. The rule requires `start_time` to be
  strictly in the future with no grace window.
- **Fix:** `if start <= now:`

### Bug 3 — No minimum-duration check
- **File/line:** `app/routers/bookings.py:98`
- **What/why:** only `duration_hours > MAX_DURATION_HOURS` was checked, so zero and
  negative whole-hour durations (`end_time <= start_time`) passed validation and
  produced a booking with `price_cents` of `0` or negative.
- **Fix:** `if duration_hours < MIN_DURATION_HOURS or duration_hours > MAX_DURATION_HOURS:`
  — rejects non-positive durations too, still as `400 INVALID_BOOKING_WINDOW`.

---

## Rules 3 & 4 — No double-booking / booking quota

### Bug 4 — Overlap check rejected back-to-back bookings
- **File/line:** `app/routers/bookings.py:55` (in `_has_conflict`)
- **What/why:** the comparison was `b.start_time <= end and start <= b.end_time`. The
  rule defines overlap with strict inequalities, so a booking starting exactly when
  another ends was wrongly rejected with `409 ROOM_CONFLICT`.
- **Fix:** `if b.start_time < end and start < b.end_time:`

### Bug 5 — Booking creation was an unsynchronized check-then-insert
- **File/line:** `app/routers/bookings.py:105-129` (conflict check, quota check,
  insert and commit; lock defined at line 29)
- **What/why:** the conflict check, quota check, and row insert ran with no
  synchronization, so N simultaneous requests for the same slot (or the same user's
  quota window) could all pass their checks before any of them committed. Observed:
  6 concurrent requests for one slot produced six `201`s instead of one; 6 concurrent
  in-window requests by one user (quota 3) produced six `201`s instead of three.
- **Fix:** a module-level `threading.Lock` (`_booking_lock`) now wraps
  conflict-check → quota-check → insert → commit → stats/cache updates, serializing
  the whole read-check-write sequence. Notification side effects stay outside the
  lock.

---

## Rule 5 — Rate limit

### Bug 6 — Rate limiter lost updates under concurrent requests
- **File/line:** `app/services/ratelimit.py:22-31`
- **What/why:** `record_and_check` read the user's request bucket, then wrote it back
  with no synchronization, so concurrent requests read the same starting bucket and
  overwrote each other's appends — the counter never reached the limit. Observed: 25
  concurrent `POST /bookings` produced zero `429`s (limit is 20 per rolling 60s).
- **Fix:** wrapped the whole read-trim-append-check sequence in a `threading.Lock`.

---

## Rule 6 — Cancellation refund policy

### Bug 7 — Refund tier boundaries wrong at both ends
- **File/line:** `app/routers/bookings.py:208-215`
- **What/why:** two defects: (a) notice was floored to whole hours before comparing
  with `> 48`, so notice in `[48h, 49h)` fell into the 50% tier instead of 100%; (b)
  the `< 24h` branch returned 50% instead of 0% — the 0% tier never existed.
- **Fix:** compare the `timedelta` directly against the boundaries:
  `notice >= timedelta(hours=48)` → 100%, `elif notice >= timedelta(hours=24)` → 50%,
  `else` → 0%.

### Bug 8 — Refund amount rounded differently in the response vs. the ledger
- **File/line:** `app/services/refunds.py:17`, used by `app/routers/bookings.py:219`
- **What/why:** the response used Python's `round()` (round-half-to-even), while the
  `RefundLog` was written from a separate float-based `int(...)` truncation. Half-cent
  amounts rounded differently in each place, and the two could disagree (rule
  requires half-cents to round up and the response to equal the ledger).
- **Fix:** `amount_cents = (booking.price_cents * percent + 50) // 100` — exact
  integer half-cent-up rounding done once in `log_refund`; the cancel response now
  returns `entry.amount_cents` directly, so the two values can never diverge.

### Bug 9 — Concurrent cancels produced multiple refunds
- **File/line:** `app/routers/bookings.py:193-227`
- **What/why:** the booking's status was read-checked, then the refund logged and the
  status committed afterward with no synchronization — N concurrent cancels of the
  same booking could all pass the "not already cancelled" check. Observed: 6
  concurrent cancels produced six `200`s and six `RefundLog` rows (rule: exactly one
  `RefundLog`, extra attempts must get `409 ALREADY_CANCELLED`).
- **Fix:** the fetch → status check → refund → commit sequence now runs under the same
  `_booking_lock` used for creation, and `booking.status` is set to `"cancelled"`
  before the refund is logged and committed, so the status flip and the ledger row are
  atomic with respect to other cancel attempts.

---

## Rule 7 — Reference codes

### Bug 10 — Reference-code counter race produced duplicate codes
- **File/line:** `app/services/reference.py:21-26`
- **What/why:** `next_reference_code` read the counter, then incremented it with no
  synchronization, so concurrent booking creations could all read the same value.
  Observed: 6 concurrent bookings all received the same reference code.
- **Fix:** wrapped the read-increment-format sequence in a `threading.Lock`.

---

## Rule 8 — Auth

### Bug 11 — Access tokens lived 900 minutes instead of 900 seconds
- **File/line:** `app/auth.py:52`
- **What/why:** `lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)`.
  `ACCESS_TOKEN_EXPIRE_MINUTES` is already in minutes (15), so multiplying by 60
  produced a lifetime of 900 minutes (54,000 seconds) instead of 900 seconds.
- **Fix:** `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`, giving `exp - iat == 900`
  exactly.

### Bug 12 — Logout never actually invalidated the access token
- **File/line:** `app/auth.py:107` (compared against `app/auth.py:88`, where
  `revoke_access_token` stores the token's `jti`)
- **What/why:** the revocation set was keyed by the token's `jti`, but the
  request-path check tested `payload.get("sub")` — the user id — against that set, so
  it could never match. After `POST /auth/logout`, the same access token still worked
  on protected routes.
- **Fix:** compare the value actually stored: `if payload.get("jti") in
  _revoked_tokens:`. A different token belonging to the same user is unaffected.

### Bug 13 — Refresh tokens were reusable (no single-use rotation)
- **File/line:** `app/auth.py:91-96` (`consume_refresh_token`), called from
  `app/routers/auth.py:94`
- **What/why:** `POST /auth/refresh` decoded the presented refresh token and issued a
  new pair but never invalidated the presented token, so the same refresh token could
  be replayed indefinitely (rule: reuse must return 401).
- **Fix:** added `consume_refresh_token`, which — under a lock, since the
  check-then-add is itself a race window under concurrent requests — rejects an
  already-used `jti` with `401` and otherwise records it as used; called before
  issuing new tokens. Verified 10 parallel refreshes of one token → exactly one `200`
  and nine `401`s.

---

## Rule 9 — Multi-tenancy

### Bug 14 — Cross-org data leak in CSV export
- **File/line:** `app/services/export.py:32-53`
- **What/why:** with `include_all=true&room_id=<id>`, the export path used a helper
  that filtered bookings only by `room_id`, with no organization filter. An admin of
  one org could pass a room id belonging to another org and download that org's
  bookings (ids, user ids, times, prices) — cross-org resource ids must behave as
  non-existent.
- **Fix:** `include_all` now only widens the export from "caller's own bookings" to
  "all users' bookings" within an org-scoped query (`_fetch_scoped`, which always
  joins through `Room` and filters on the caller's `org_id`). A cross-org room id now
  yields a header-only CSV.

---

## Rule 10 — Booking visibility

### Bug 15 — Members could read other members' bookings
- **File/line:** `app/routers/bookings.py:172-173`
- **What/why:** `GET /bookings/{id}` filtered only by organization, missing the owner
  check that cancel already had — any member could read another member's booking in
  the same org.
- **Fix:** added the same guard used by cancel:
  `if user.role != "admin" and booking.user_id != user.id: raise 404 BOOKING_NOT_FOUND`.

---

## Rule 11 — Pagination & ordering

### Bug 16 — Pagination broken three ways
- **File/line:** `app/routers/bookings.py:145-147`
- **What/why:** (a) results were ordered `start_time` **descending** instead of
  ascending; (b) `.offset(page * limit)` skipped the first page's worth of items on
  `page=1`; (c) `.limit(10)` was hardcoded, ignoring the caller's `limit` parameter.
- **Fix:** `order_by(Booking.start_time.asc(), Booking.id.asc())`,
  `.offset((page - 1) * limit)`, `.limit(limit)`.

---

## Rule 12 — Usage report

### Bug 17 — Creating a booking left the usage-report cache stale
- **File/line:** `app/routers/bookings.py:126-128`, `app/cache.py:44-50`
- **What/why:** creating a booking only invalidated the availability cache, so a
  previously-cached `/admin/usage-report` kept serving pre-create numbers indefinitely
  (rule: the report must reflect current state immediately).
- **Fix:** the create path now calls both `cache.invalidate_availability(...)` and
  `cache.invalidate_report(...)`; `invalidate_availability` additionally clears the
  report cache as a backstop so either call path keeps both caches consistent.

### Bug 18 — New room missing from a cached usage report
- **File/line:** `app/routers/rooms.py:55-59`
- **What/why:** the rule requires the report to list every room in the org, including
  ones with zero bookings, immediately. `create_room` never invalidated cached
  reports, so a range requested before the room existed kept serving a report without
  it.
- **Fix:** `create_room` calls `cache.invalidate_report(admin.org_id)` after commit.

---

## Rule 13 — Availability

### Bug 19 — Cancelling a booking left the availability cache stale
- **File/line:** `app/routers/bookings.py:224-226`, `app/cache.py:24-31`
- **What/why:** cancelling a booking only invalidated the report cache, so a
  previously-cached `/rooms/{id}/availability` kept showing the cancelled slot as busy
  indefinitely.
- **Fix:** the cancel path now calls both `cache.invalidate_report(...)` and
  `cache.invalidate_availability(...)`; `invalidate_report` additionally clears the
  availability cache as a backstop.

---

## Rule 14 — Room stats

### Bug 20 — Lost updates in live room stats under concurrent bookings
- **File/line:** `app/services/stats.py:20-33`
- **What/why:** `record_create`/`record_cancel` read the counters, then wrote back
  with no synchronization, so concurrent bookings on the same room read the same
  starting count/revenue and each wrote back "old value + 1" — all but one of the
  overlapping updates were silently lost. Observed: 6 concurrent creates on one room
  left `total_confirmed_bookings` at 2 instead of 6.
- **Fix:** added a `threading.Lock` and made the read-modify-write in both functions
  atomic under it.

---

## Rule 15 — Registration

### Bug 21 — Duplicate username returned 201 with the existing account
- **File/line:** `app/routers/auth.py:40-46`
- **What/why:** when the username already existed in the org, the handler returned
  the existing user's record with `201` instead of an error, leaking account
  existence/role and making registration look successful without creating anything.
- **Fix:** raise `AppError(409, "USERNAME_TAKEN", ...)` on the duplicate path. A
  duplicate in a different org is still allowed (uniqueness is per-org).

### Bug 22 — Concurrent registration returned 500s
- **File/line:** `app/routers/auth.py:28-38` and `app/routers/auth.py:54-61`
- **What/why:** `register` did read-check-then-insert with no guard for either
  uniqueness invariant (org name, org+username). Under parallel requests the
  database's unique constraints fired as unhandled `IntegrityError` → `500`.
  Measured: 10 identical registrations produced 9×`500` (should be 9×`409`); 10
  different users registering into one brand-new org produced 4×`500` (should all be
  `201`, one admin plus nine members).
- **Fix:** both commits are now wrapped in `try/except IntegrityError`: losing the
  org-creation race rolls back, re-reads the now-existing org, and joins it as
  member; losing the username race rolls back and raises `409 USERNAME_TAKEN`.
  Verified: 10 identical → exactly 1×`201` + 9×`409`; 10 distinct users into a new
  org → 10×`201` with exactly one admin; no duplicate rows, no 5xx.

---

## Rule 16 — Liveness

### Bug 23 — Lock-order deadlock hung the service
- **File/line:** `app/services/notifications.py:24-39`
- **What/why:** `notify_created` acquired `_email_lock` then, nested inside it,
  `_audit_lock`; `notify_cancelled` acquired them in the opposite order (`_audit_lock`
  then `_email_lock`). A booking create running concurrently with a cancel could reach
  the state "create holds the email lock and wants the audit lock; cancel holds the
  audit lock and wants the email lock" — a classic ABBA deadlock. Both worker threads
  blocked forever, and every subsequent create/cancel queued up behind the dead locks,
  hanging the whole booking pipeline. Observed: firing 6 creates + 6 cancels
  concurrently left 11/12 requests hung past a 25s timeout.
- **Fix:** `notify_cancelled` no longer holds one lock while acquiring the other — it
  takes `_audit_lock`, writes the audit entry, releases it, then separately takes
  `_email_lock` and sends. No thread can hold one of the two locks while waiting for
  the other, so circular wait is impossible. Verified: repeated bursts of 8 creates +
  8 cancels all complete; `/health` stays responsive throughout.

---

## API contract

### Bug 24 — Booking detail response overwrote `start_time` with `created_at`
- **File/line:** `app/routers/bookings.py` (booking detail serialization)
- **What/why:** after serializing the booking, the handler reassigned
  `response["start_time"] = iso_utc(booking.created_at)`, so `GET /bookings/{id}`
  returned the creation timestamp in place of the actual start time.
- **Fix:** removed the overwrite; `serialize_booking` already sets `start_time`
  correctly from the booking row.

### Bug 25 — Malformed datetime string caused a 500 instead of a 400
- **File/line:** `app/routers/bookings.py:87-88` (the two `parse_input_datetime`
  calls), root cause spanning `app/schemas.py:29-30` (fields typed `str`) and
  `app/timeutils.py:11` (`datetime.fromisoformat`)
- **What/why:** `start_time`/`end_time` are plain `str` in `BookingCreateRequest`, so
  Pydantic accepts any string. `parse_input_datetime("not-a-datetime")` then raised a
  raw `ValueError` from `datetime.fromisoformat`, which escaped as an unhandled `500`
  in FastAPI's default shape — neither a documented `{"detail","code"}` 4xx nor a
  framework 422.
- **Fix:** wrapped the two parse calls in `try/except ValueError:` and re-raise
  `AppError(400, "INVALID_BOOKING_WINDOW", "start_time and end_time must be valid ISO 8601 datetimes")`.
  Valid-input behavior is unchanged; only malformed strings are affected.
