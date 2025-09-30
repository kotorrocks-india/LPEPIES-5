# core/scheduler.py
from __future__ import annotations
from datetime import date, timedelta
from typing import Iterable, List

# ------------------------------------------------------------
# Small date helpers
# ------------------------------------------------------------

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def weekday_name(d: date) -> str:
    """Return short weekday name (Mon..Sun) for a date object."""
    try:
        return WEEKDAY_NAMES[d.weekday()]
    except Exception:
        # Fallback, never raises in normal use
        return d.strftime("%a")


def year_sem12_to_abs_sem(year: int, sem12: int) -> int:
    """
    Convert 'Year (1..N)' + 'Sem-in-year (1 or 2)' into absolute semester number.
    Example: Year 1 -> (1,2), Year 2 -> (3,4), etc.
    """
    year = int(year)
    sem12 = 1 if int(sem12) == 1 else 2
    return (year - 1) * 2 + sem12


# ------------------------------------------------------------
# Academic year window
# ------------------------------------------------------------

def academic_year_window(batch_year: int, year_in_program: int) -> tuple[date, date]:
    """
    For a given batch start year (e.g., 2021) and program year (1..duration),
    return the *inclusive* academic window for that program year as dates.

    Convention used (as discussed):
      - Year 1:  June <batch_year>        -> May <batch_year + 1>
      - Year 2:  June <batch_year + 1>    -> May <batch_year + 2>
      - ...
      - Year N:  June <batch_year + N - 1>-> May <batch_year + N>

    Returns:
      (start_date, end_date)
    """
    by = int(batch_year)
    y  = max(1, int(year_in_program))

    start = date(by + (y - 1), 6, 1)   # June 1
    end   = date(by + y, 5, 31)        # May 31 next year
    return (start, end)


# ------------------------------------------------------------
# Pattern generators (with holiday skipping)
# ------------------------------------------------------------

def _normalize_holidays(holidays: Iterable[date]) -> set[date]:
    """Convert an iterable of date-like objects into a set[date]."""
    out = set()
    for h in holidays or []:
        if isinstance(h, date):
            out.add(h)
    return out


def generate_simple_pattern(
    start: date,
    end: date,
    weekday_indices: Iterable[int],
    holidays: Iterable[date] | None = None,
) -> List[date]:
    """
    Generate a list of dates from start..end (inclusive) that fall on the given
    weekday indices (0=Mon..6=Sun), skipping any holidays.
    """
    if start > end:
        return []

    wd_set = {int(i) for i in weekday_indices or []}
    holi = _normalize_holidays(holidays)
    days = []
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d.weekday() in wd_set and d not in holi:
            days.append(d)
        d += one
    return days


def generate_alternating_pattern(
    start: date,
    end: date,
    weekA_weekdays: Iterable[int],
    weekB_weekdays: Iterable[int],
    holidays: Iterable[date] | None = None,
) -> List[date]:
    """
    Generate alternating weeks between 'A' and 'B' patterns.
    - Week 0 relative to 'start' is A, week 1 is B, then A, B, ...

    weekA_weekdays / weekB_weekdays: iterables of weekday indices (0=Mon..6=Sun)
    """
    if start > end:
        return []

    holi = _normalize_holidays(holidays)
    a_set = {int(i) for i in weekA_weekdays or []}
    b_set = {int(i) for i in weekB_weekdays or []}

    out = []
    d = start
    one = timedelta(days=1)
    while d <= end:
        # Determine week index (0-based) from start
        week_index = ((d - start).days) // 7
        use_A = (week_index % 2 == 0)
        wset = a_set if use_A else b_set
        if d.weekday() in wset and d not in holi:
            out.append(d)
        d += one
    return out


# ------------------------------------------------------------
# Backfill helper (optional)
# ------------------------------------------------------------

def backfill_last_weeks(
    start: date,
    end: date,
    weeks_tail: int,
    tail_weekdays: Iterable[int],
    holidays: Iterable[date] | None = None,
) -> List[date]:
    """
    Return candidate dates focused on the *last* `weeks_tail` weeks in the
    window [start..end], on the given weekdays, skipping holidays.

    Useful to quickly find open dates toward the end to meet targets.
    """
    if start > end or weeks_tail <= 0:
        return []

    holi = _normalize_holidays(holidays)
    wset = {int(i) for i in tail_weekdays or []}
    one = timedelta(days=1)

    # Compute tail window start
    tail_days = weeks_tail * 7
    tail_start = max(start, end - timedelta(days=tail_days - 1))

    d = tail_start
    out = []
    while d <= end:
        if d.weekday() in wset and d not in holi:
            out.append(d)
        d += one
    return out
