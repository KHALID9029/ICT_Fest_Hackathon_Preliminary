# Bug Report

Each entry: file/line (post-fix), the business rule violated, what was wrong and why it
caused incorrect behavior, how it was fixed, and difficulty. All bugs were confirmed
black-box through the HTTP API before fixing and re-verified after.

---

## Module 1 — Identity & Access

### Bug 1 — Access tokens live 900 minutes instead of 900 seconds

- **File/line:** `app/auth.py:52` (in `create_access_token`)
- **Rule violated:** Rule 8 — "Access tokens expire in exactly 900 seconds."
- **What was wrong:** `lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)`.
  `ACCESS_TOKEN_EXPIRE_MINUTES` is already in minutes (15), so multiplying by 60 produced
  a `timedelta` of 900 **minutes** (54 000 s). Observable: decoded token had
  `exp - iat = 54000`.
- **Fix:** removed the `* 60` → `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)` = 900 s.
  Verified: `exp - iat == 900` exactly; refresh token unchanged at 604 800 s (7 days).
- **Difficulty:** Easy

### Bug 2 — Logout never actually invalidates the access token

- **File/line:** `app/auth.py:107` (in `get_token_payload`)
- **Rule violated:** Rule 8 — "Logout immediately invalidates the presented access token
  (subsequent use → 401)."
- **What was wrong:** `revoke_access_token` stores the token's **`jti`** in
  `_revoked_tokens`, but the request-path check tested `payload.get("sub") in
  _revoked_tokens` — the user id, which is never in the set. So the blacklist could never
  match: after `POST /auth/logout`, the same token still returned 200 on protected routes.
- **Fix:** compare the token's `jti` (the value actually stored):
  `if payload.get("jti") in _revoked_tokens`. Verified: post-logout use of the token →
  401 `Token has been revoked`, while a *different* token for the same user keeps working
  (revoking `sub` instead would have wrongly killed every token of the user).
- **Difficulty:** Medium

### Bug 3 — Duplicate username returned 201 with the existing account instead of 409

- **File/line:** `app/routers/auth.py:45-46` (in `register`)
- **Rule violated:** Rule 15 — "A duplicate username within the org → `409 USERNAME_TAKEN`."
- **What was wrong:** when the username already existed in the org, the handler **returned
  the existing user's record with HTTP 201** instead of an error. Besides violating the
  rule, this leaked account existence/role to anyone and made registration look successful
  without creating anything.
- **Fix:** raise `AppError(409, "USERNAME_TAKEN", ...)` on the duplicate path. Verified:
  duplicate in same org → 409 `USERNAME_TAKEN`; same username in another org still allowed
  (per-org uniqueness); existing user's password not affected by the failed attempt.
- **Difficulty:** Easy

### Bug 4 — Refresh tokens were reusable (no single-use rotation)

- **File/line:** `app/routers/auth.py:94` + new helper `app/auth.py:91-96`
  (`consume_refresh_token`)
- **Rule violated:** Rule 8 — "Refresh tokens are single-use: refreshing returns a new
  access and refresh token and invalidates the presented refresh token (reuse → 401)."
- **What was wrong:** `POST /auth/refresh` decoded the presented refresh token and issued a
  new pair but **never invalidated the presented token** — the same refresh token could be
  replayed forever (stolen-token replay is exactly what rotation exists to stop).
- **Fix:** added `consume_refresh_token(payload)` in `app/auth.py`, which atomically
  (under a `threading.Lock`, since the check-then-add is a race window under concurrent
  requests) rejects an already-used `jti` with 401 and records it in the revocation set;
  called from the refresh endpoint before issuing new tokens. Verified: reuse → 401;
  rotated token chain works; **10 parallel refreshes of one token → exactly 1×200 and
  9×401**.
- **Difficulty:** Medium

### Bug 5 — Concurrent registration returned 500s (unguarded check-then-insert)

- **File/line:** `app/routers/auth.py:31-38` and `app/routers/auth.py:55-61` (in `register`)
- **Rules violated:** Rule 15 (registration semantics: duplicate → `409 USERNAME_TAKEN`;
  known org → join as member) — broken under concurrent requests.
- **What was wrong:** `register` did read-check-then-insert with no guard for either
  uniqueness invariant. Under parallel requests the DB unique constraints
  (`organizations.name`, `uq_user_org_username`) fired as unhandled
  `sqlalchemy.exc.IntegrityError` → **HTTP 500**. Measured black-box: 10 identical
  registrations → 9×500 (should be 9×409), and 10 different users registering into one
  brand-new org → 4×500 (should all be 201, one admin + nine members).
- **Fix:** wrapped both commits in `try/except IntegrityError` — losing the org-creation
  race rolls back, re-reads the now-existing org and joins it as **member**; losing the
  username race rolls back and raises the contractual `409 USERNAME_TAKEN`. Verified by
  re-hammering: 10 identical → exactly 1×201 + 9×409; 10 distinct users into a new org →
  10×201 with exactly one admin; no duplicate org/user rows; no 5xx.
- **Difficulty:** Hard
