---
name: cowork-bughunt
description: Use when hunting and fixing bugs in the CoWork booking API for the ICT Fest hackathon. Black-box graded against 16 business rules; the contract must not change. Use before proposing any fix.
---

# CoWork Bug Hunt (Hackathon Edition)

## Overview

This is a **black-box, contract-preserving bug-fix challenge**. A grader builds your repo
and probes it only through the HTTP API, asserting behavior against the 16 business rules in
`REQUIREMENTS.md`. You score points per bug (Easy 3 / Medium 5 / Hard 10). You do NOT add
features or refactor.

**Core principle:** the business rules are the ORACLE. A bug exists wherever observable API
behavior deviates from `REQUIREMENTS.md` Sections 3-4. Find the deviation, trace it to root
cause, fix the smallest thing, prove it against the API.

## The Iron Laws

```
1. NO FIX WITHOUT A RULE IT RESTORES.  Name the rule number the bug violates first.
2. NEVER CHANGE THE CONTRACT.  Paths, status codes, error codes, JSON field names are frozen.
3. FIX THE SYMPTOM'S SOURCE, NOT THE SYMPTOM.  One minimal change per bug.
4. PROVE IT THROUGH THE API.  Black-box in, black-box out — same as the grader.
```

If a change alters a path, status code, error `code`, or JSON field name, it is WRONG even
if it "fixes" the behavior. Stop and find the real bug.

## Before You Touch Code (setup)

- Fork the repo and **Leave fork network** (Settings -> Danger Zone) BEFORE editing.
- `docker compose up --build` (or `uvicorn app.main:app --reload`) — confirm it boots.
- Keep the app running and a scratch `curl`/httpx script open. You test against `:8000`.
- Work only in your assigned module (see `MODULE_DIVISION.md`) to avoid collisions.
- Start `bug_report.md` in the repo root NOW — fill it as you go (it's the tie-breaker).

## The Four Phases (do them in order)

### Phase 1 — Locate the Deviation (rule-driven)

For each business rule in your module, drive the API and compare actual vs. required:

1. **Pick a rule.** State it: "Rule N says X."
2. **Construct the probe.** The smallest request(s) that exercise it. Include the boundary
   cases the rule spells out (e.g. back-to-back bookings allowed, notice exactly 48h,
   duration exactly 8h, start_time exactly now).
3. **Observe the real response.** Status code, `code`, every JSON field, datetime format.
4. **Diff against the rule.** Any mismatch = a bug's fingerprint. Write down WHAT is wrong
   before asking WHY.

Read error messages and stack traces completely — FastAPI/SQLAlchemy tracebacks usually
name the file and line.

### Phase 2 — Trace to Root Cause

1. **Follow the data flow** from request -> router -> service -> model -> DB and back through
   the serializer. Where does the wrong value first appear?
2. **Compare working vs broken paths.** Another endpoint often does the same thing correctly
   (e.g. one place normalizes datetimes to UTC, another forgets). List every difference.
3. **Suspect the usual bug classes for this codebase:**
   - **Off-by-one / wrong operator** in comparisons (`<` vs `<=`) — overlap (Rule 3), quota
     window `(now, now+24h]` (Rule 4), refund tiers `>=48h`, `24-48h`, `<24h` (Rule 6).
   - **Datetime/timezone** — naive vs aware, missing UTC conversion, missing `Z` designator
     (Rule 1). Compare stored vs compared vs serialized.
   - **Rounding** — half-cent must round UP (Rule 6). Watch banker's rounding / `round()`.
   - **Auth/JWT** — exp = 900s exactly, blacklist on logout, single-use refresh rotation,
     claim types (`sub` is a string) (Rule 8).
   - **Tenant leakage** — a query missing `org_id` filter lets cross-org IDs resolve; must
     behave as 404 (Rule 9, 10).
   - **Pagination** — offset math, ordering ties by id, `total` count (Rule 11).
   - **Cache staleness** — `cache.py` not invalidated on create/cancel, so usage-report /
     availability / stats go stale (Rules 12-14). Trace who writes vs who invalidates.
   - **Concurrency** — race windows in booking create (conflict/quota/reference code) and
     cancel (double refund), missing locking/atomicity (Rules 3,4,5,6,7,16). A read-check-
     then-write without a guard is the tell.

### Phase 3 — One Hypothesis, Minimal Test

1. State it precisely: "The bug is on `<file>:<line>`; `<code>` should be `<code>` because
   Rule N requires Y."
2. Make the SMALLEST change that could fix it. One variable at a time. No bundled cleanup.
3. Re-run the exact probe from Phase 1. Fixed? -> Phase 4. Not fixed? -> new hypothesis,
   revert the change first (don't stack fixes).

### Phase 4 — Prove and Lock In

1. **Reproduce via the API** that the rule now holds, including the boundary cases.
2. **For concurrency rules, hammer it.** Fire N simultaneous requests and assert the
   invariant (e.g. only one of two overlapping bookings succeeds; exactly one RefundLog;
   reference codes all unique; quota never exceeded). Example pattern:
   ```bash
   # fire 10 identical bookings at once, expect exactly 1x 201 and 9x 409
   seq 10 | xargs -P10 -I{} curl -s -o /dev/null -w "%{http_code}\n" \
     -X POST :8000/bookings -H "Authorization: Bearer $TOK" \
     -H "Content-Type: application/json" -d "$BODY" | sort | uniq -c
   ```
3. **Regression check.** Run `pytest` (smoke test) and re-probe neighboring endpoints — did
   you break the contract anywhere?
4. **Confirm the contract is untouched:** same path, status, `code`, field names.
5. **Log it in `bug_report.md`:** file/line, the rule it violated, why it was wrong, the fix,
   and difficulty (Easy/Medium/Hard). This is your final tie-breaker.

## Red Flags — STOP and return to Phase 1

- Proposing a fix before naming which rule number is violated.
- Changing a field name, status code, or error `code` to "make it pass."
- "It's probably the datetime thing" — verify by probing, don't guess.
- Editing a file outside your module without telling the owner.
- Stacking a second fix on top of a first that didn't work (revert, re-hypothesize).
- Fixing a concurrency rule but only testing it with one sequential request.
- 3+ fixes on the same spot failing -> the bug is elsewhere (often shared state: `cache.py`,
  `get_current_user`, a missing DB constraint/lock). Re-trace the data flow.

## Scoring Strategy

- Sweep every rule in your module once for the cheap Easy wins (wrong operator, wrong field,
  missing UTC) before sinking time into a single Hard concurrency bug.
- Hard bugs (10 pts) cluster in booking create/cancel concurrency and cache invalidation —
  worth it once the easy ones are banked.
- Keep `bug_report.md` current: ties are broken first by difficulty solved, then by a clean
  bug report under manual review.

## Quick Reference

| Phase | Do | Done when |
|---|---|---|
| 1 Locate | Probe API per rule, diff actual vs required | You can state the rule + the observable deviation |
| 2 Trace | Follow data flow to first wrong value | You know the file/line and why |
| 3 Hypothesis | One minimal change | Probe passes, or new hypothesis |
| 4 Prove | Re-probe incl. boundaries + concurrency; regression; log | Rule holds, contract intact, report updated |

## Submission checklist
- [ ] Fork left the network before editing
- [ ] Only broken code changed; no refactors; contract identical
- [ ] `pytest` still green
- [ ] `bug_report.md` complete (file/line, rule, why, fix, difficulty)
- [ ] Repo made PUBLIC within 1 hour of the deadline
- [ ] URL submitted via the Google Form
