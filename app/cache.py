"""In-memory response caches for read-heavy reporting endpoints.

Usage reports and per-room availability are relatively expensive to compute and
are read far more often than the underlying data changes, so results are cached
and invalidated when the data they depend on is modified.
"""
import threading

_lock = threading.Lock()
_report_cache: dict[tuple, dict] = {}
_availability_cache: dict[tuple, dict] = {}


def get_report(org_id: int, frm: str, to: str):
    with _lock:
        return _report_cache.get((org_id, frm, to))


def set_report(org_id: int, frm: str, to: str, value: dict) -> None:
    with _lock:
        _report_cache[(org_id, frm, to)] = value


def invalidate_report(org_id: int) -> None:
    with _lock:
        for key in [k for k in _report_cache if k[0] == org_id]:
            _report_cache.pop(key, None)
        # Booking cancellation only calls invalidate_report, but it also
        # changes availability; drop cached availability so it reflects the
        # cancel immediately (Rule 13).
        _availability_cache.clear()


def get_availability(room_id: int, date: str):
    with _lock:
        return _availability_cache.get((room_id, date))


def set_availability(room_id: int, date: str, value: dict) -> None:
    with _lock:
        _availability_cache[(room_id, date)] = value


def invalidate_availability(room_id: int, date: str) -> None:
    with _lock:
        _availability_cache.pop((room_id, date), None)
        # Booking creation only calls invalidate_availability, but it also
        # changes usage reports; drop cached reports so they reflect the new
        # booking immediately (Rule 12).
        _report_cache.clear()
