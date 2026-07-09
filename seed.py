"""Seed the CoWork database with known test data for manual testing.

Usage
-----
Local (venv, Python 3.11):        python seed.py
Docker (compose service `api`):   docker compose cp seed.py api:/app/seed.py
                                  docker compose exec api python seed.py

The script uses the app's own models and DATABASE_URL, so it seeds whichever
database the server actually reads (./cowork.db locally, the compose volume in
Docker). It refuses to run twice; delete the DB (or `docker compose down -v`)
to reseed.

What you get
------------
Two orgs with password "password123" for every user:

  acme   : alice (admin), bob (member), carol (member)
  globex : dave (admin),  erin (member)

acme rooms: Focus Room (rate 1000c), Board Room (2500c), Phone Booth (500c),
Empty Room (750c, deliberately zero bookings for usage-report checks).
globex room: Globex HQ (2000c).

Bookings (times are computed at seed time, whole-hour aligned, printed on exit):
  - Focus Room, day-after-tomorrow: bob 09:00-11:00 and carol 11:00-12:00
    (back-to-back pair), plus a CANCELLED bob 14:00-16:00 with one RefundLog.
  - Board Room, day-after-tomorrow: carol 10:00-12:00.
  - Phone Booth: bob at now+2h and now+5h (1h each) -> bob already holds 2
    confirmed bookings inside the (now, now+24h] quota window.
  - Focus Room, 3 days out: bob 10:00-11:00 (cancel it to see the 100% tier).
  - Globex HQ, day-after-tomorrow: erin 09:00-10:00 (cross-org 404 material).

Caveat: /rooms/{id}/stats is served from in-memory counters that only update
on API create/cancel, so seeded bookings show up in usage-report, availability
and export (DB-backed) but NOT in room stats.
"""
from datetime import datetime, time as dtime, timedelta

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Booking, Organization, RefundLog, Room, User

PASSWORD = "password123"


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Organization).filter(Organization.name == "acme").first():
            print("Database already contains the seed org 'acme'.")
            print("Delete the DB file (or `docker compose down -v`) and rerun to reseed.")
            return

        hashed = hash_password(PASSWORD)  # one hash reused; same password for all users

        acme = Organization(name="acme")
        globex = Organization(name="globex")
        db.add_all([acme, globex])
        db.flush()

        alice = User(org_id=acme.id, username="alice", hashed_password=hashed, role="admin")
        bob = User(org_id=acme.id, username="bob", hashed_password=hashed, role="member")
        carol = User(org_id=acme.id, username="carol", hashed_password=hashed, role="member")
        dave = User(org_id=globex.id, username="dave", hashed_password=hashed, role="admin")
        erin = User(org_id=globex.id, username="erin", hashed_password=hashed, role="member")
        db.add_all([alice, bob, carol, dave, erin])
        db.flush()

        focus = Room(org_id=acme.id, name="Focus Room", capacity=4, hourly_rate_cents=1000)
        board = Room(org_id=acme.id, name="Board Room", capacity=12, hourly_rate_cents=2500)
        booth = Room(org_id=acme.id, name="Phone Booth", capacity=1, hourly_rate_cents=500)
        empty = Room(org_id=acme.id, name="Empty Room", capacity=6, hourly_rate_cents=750)
        hq = Room(org_id=globex.id, name="Globex HQ", capacity=10, hourly_rate_cents=2000)
        db.add_all([focus, board, booth, empty, hq])
        db.flush()

        now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        d2 = (now + timedelta(hours=48)).date()  # always > 24h out: quota-neutral
        d3 = (now + timedelta(hours=72)).date()  # >= 48h out: 100% refund tier

        def at(day, hour):
            return datetime.combine(day, dtime(hour=hour))

        def booking(room, user, start, end, code, status="confirmed"):
            hours = int((end - start).total_seconds() // 3600)
            return Booking(
                room_id=room.id,
                user_id=user.id,
                start_time=start,
                end_time=end,
                status=status,
                reference_code=code,
                price_cents=room.hourly_rate_cents * hours,
                created_at=now,
            )

        bookings = [
            booking(focus, bob, at(d2, 9), at(d2, 11), "CW-SEED01"),
            booking(focus, carol, at(d2, 11), at(d2, 12), "CW-SEED02"),  # back-to-back with SEED01
            booking(focus, bob, at(d2, 14), at(d2, 16), "CW-SEED03", status="cancelled"),
            booking(board, carol, at(d2, 10), at(d2, 12), "CW-SEED04"),
            booking(booth, bob, now + timedelta(hours=2), now + timedelta(hours=3), "CW-SEED05"),
            booking(booth, bob, now + timedelta(hours=5), now + timedelta(hours=6), "CW-SEED06"),
            booking(focus, bob, at(d3, 10), at(d3, 11), "CW-SEED07"),
            booking(hq, erin, at(d2, 9), at(d2, 10), "CW-SEED08"),
        ]
        db.add_all(bookings)
        db.flush()

        cancelled = bookings[2]
        db.add(RefundLog(
            booking_id=cancelled.id,
            amount_cents=cancelled.price_cents,  # cancelled with >= 48h notice: 100%
            status="processed",
            processed_at=now,
        ))

        db.commit()

        print("Seeded. All passwords:", PASSWORD)
        print()
        print("Users:   acme: alice(admin) bob(member) carol(member) | globex: dave(admin) erin(member)")
        print(f"Rooms:   acme: Focus={focus.id} Board={board.id} Booth={booth.id} Empty={empty.id} | globex: HQ={hq.id}")
        print()
        print("Bookings:")
        for b in bookings:
            print(f"  id={b.id:<3} {b.reference_code}  room={b.room_id}  user={b.user_id}  "
                  f"{b.start_time.isoformat()} -> {b.end_time.isoformat()}  {b.status}  {b.price_cents}c")
        print()
        print(f"Availability date with data: {d2.isoformat()} (Focus busy 09-11, 11-12; cancelled 14-16 must NOT appear)")
        print(f"Usage-report range with data: from={d2.isoformat()} to={d3.isoformat()}")
        print(f"Quota: bob already holds 2 confirmed bookings in (now, now+24h] "
              f"(ids {bookings[4].id}, {bookings[5].id}) -> 3rd in-window create OK, 4th must be 409 QUOTA_EXCEEDED")
        print(f"Refund tiers: cancel id={bookings[6].id} (>=48h notice) -> 100%; "
              f"cancel id={bookings[4].id} (<24h notice) -> 0%")
        print(f"Cross-org 404s from an acme token: room {hq.id}, booking {bookings[7].id}")
        print()
        print('Login: curl -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" '
              '-d \'{"org_name": "acme", "username": "alice", "password": "password123"}\'')
        print()
        print("NOTE: /rooms/{id}/stats uses in-memory counters fed only by API create/cancel;")
        print("seeded bookings appear in usage-report/availability/export but not in stats.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
