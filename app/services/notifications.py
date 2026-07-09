"""Side effects that accompany booking lifecycle events.

Each booking change sends a (simulated) notification email and appends an
audit-log entry. Both resources are guarded by locks so their output stays
consistent when many requests are processed at once.
"""
import threading
import time

_email_lock = threading.Lock()
_audit_lock = threading.Lock()


def _send_email(kind: str, booking) -> None:
    # Simulated SMTP round-trip.
    time.sleep(0.12)


def _write_audit(kind: str, booking) -> None:
    # Simulated audit-log formatting/flush.
    time.sleep(0.1)


def notify_created(booking) -> None:
    with _email_lock:
        _send_email("created", booking)
        with _audit_lock:
            _write_audit("created", booking)


def notify_cancelled(booking) -> None:
    # Never hold _audit_lock while waiting for _email_lock: notify_created
    # nests the locks in the opposite (email -> audit) order, and holding one
    # while acquiring the other deadlocks the service under concurrent
    # create + cancel traffic.
    with _audit_lock:
        _write_audit("cancelled", booking)
    with _email_lock:
        _send_email("cancelled", booking)
