"""Calendar helpers shared by the rule core and providers.

All Demo cities are in China, so "local time" is a fixed UTC+8 offset. Provider
timestamps are UTC; departure-date and early-departure rules use local time.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
EARLY_DEPARTURE_CUTOFF_HOUR = 8  # D-10: earlier than 08:00 local, 08:00 itself is not early


def parse_iso_date(value: object) -> date:
    """Strict `YYYY-MM-DD`. Python 3.11 also accepts compact forms; the contract does not."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def to_local(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("timestamps must carry an explicit UTC offset")
    return moment.astimezone(LOCAL_TZ)


def local_date(moment: datetime) -> date:
    return to_local(moment).date()


def is_early_departure(moment: datetime) -> bool:
    return to_local(moment).hour < EARLY_DEPARTURE_CUTOFF_HOUR


def trip_nights(start: date, end: date) -> int:
    return (end - start).days


def trip_days(start: date, end: date) -> int:
    return trip_nights(start, end) + 1


def stay_nights(check_in: date, check_out: date) -> list[date]:
    """Nights occupied by a stay: every date in [check_in, check_out)."""
    return [check_in + timedelta(days=offset) for offset in range((check_out - check_in).days)]
