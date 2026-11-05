"""Schedules for the ``jobs`` and ``vault`` collectors: one short line of text.

Accepted forms (case does not matter; times are 24-hour clock times in the build's local zone):

| text | runs |
|---|---|
| ``daily 03:00`` | every day at 03:00 |
| ``weekdays 08:30`` | Monday to Friday at 08:30 |
| ``weekly tue 07:15`` | every Tuesday at 07:15 (``weekly mon,thu 07:15`` for several days) |
| ``monthly 1 06:00`` | on the 1st of each month (a day past the month's end means its last day) |
| ``hourly`` / ``hourly :20`` | every hour, on the hour / at 20 past |
| ``every 6h`` / ``every 30m`` / ``every 2d`` | a fixed period; no "next" until it has run |

:func:`parse` turns the text into a :class:`Schedule` (or raises ``ValueError`` saying what is
wrong). A schedule knows its next run after a moment, the slot before a moment, its period (the
longest gap between two runs) and the words the room shows. A job has **missed its run** when its
last run is older than its period plus 15 %.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_WORDS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
GRACE = 0.15  # a run is missed once it is this share of a period late
_TIME = r"([01]?\d|2[0-3]):([0-5]\d)"


# --- review-fix C6: this computer's zone, clock changes and all ---
class LocalZone(dt.tzinfo):
    """This computer's time zone with its daylight-saving changes (stdlib only: no zone name
    needed, so it works on Windows without tzdata).

    ``datetime.now().astimezone()`` gives a fixed offset, right only for today: a schedule slot
    reached by adding days across a clock change came out an hour off. A time in this zone
    keeps its wall-clock time under arithmetic and takes the offset of its own date."""

    @staticmethod
    def _fixed(d: dt.datetime) -> dt.datetime:
        try:
            return d.replace(tzinfo=None).astimezone()
        except (OverflowError, OSError, ValueError):  # past what the OS can convert
            return dt.datetime.now().astimezone()

    def utcoffset(self, d: dt.datetime | None) -> dt.timedelta | None:
        return None if d is None else self._fixed(d).utcoffset()

    def dst(self, d: dt.datetime | None) -> dt.timedelta | None:
        return None  # not known without the zone's rules; utcoffset() is what counts

    def tzname(self, d: dt.datetime | None) -> str | None:
        return None if d is None else self._fixed(d).tzname()

    def fromutc(self, d: dt.datetime) -> dt.datetime:
        try:
            fixed = d.replace(tzinfo=dt.UTC).astimezone()
        except (OverflowError, OSError, ValueError):  # past what the OS can convert
            return d + (dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0))
        out = fixed.replace(tzinfo=self)
        if out.utcoffset() != fixed.utcoffset():  # the second time round when clocks go back
            out = out.replace(fold=1)
        return out

    def __repr__(self) -> str:
        return "LocalZone()"

    def __reduce__(self):
        return (LocalZone, ())


LOCAL = LocalZone()  # the build's clock; a naive ``now`` given to a collector means this zone
# --- end review-fix C6 ---


@dataclass(frozen=True)
class Schedule:
    text: str
    kind: str  # daily | weekly | monthly | hourly | every
    days: tuple[int, ...] = ()  # weekly: Monday = 0
    day: int = 1  # monthly: day of the month
    hour: int = 0
    minute: int = 0
    every: dt.timedelta | None = None

    # ------------------------------------------------------------------ slots
    def period(self) -> dt.timedelta:
        """The longest gap between two runs."""
        if self.kind == "every":
            return self.every or dt.timedelta(days=1)
        if self.kind == "hourly":
            return dt.timedelta(hours=1)
        if self.kind == "daily":
            return dt.timedelta(days=1)
        if self.kind == "monthly":
            return dt.timedelta(days=31)
        ds = sorted(self.days)
        gaps = [(ds[(i + 1) % len(ds)] - d) % 7 or 7 for i, d in enumerate(ds)]
        return dt.timedelta(days=max(gaps))

    def next_after(self, now: dt.datetime, last: dt.datetime | None = None) -> dt.datetime | None:
        """The first run strictly after ``now`` (``every``: the last run plus the period)."""
        if self.kind == "every":
            if last is None:
                return None
            nxt = last + self.period()
            while nxt <= now:
                nxt += self.period()
            return nxt
        if self.kind == "hourly":
            cand = now.replace(minute=self.minute, second=0, microsecond=0)
            return cand if cand > now else cand + dt.timedelta(hours=1)
        if self.kind == "monthly":
            y, m = now.year, now.month
            for _ in range(3):
                cand = _month_slot(now, y, m, self)
                if cand > now:
                    return cand
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            return None  # unreachable
        base = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if self.kind == "daily":
            return base if base > now else base + dt.timedelta(days=1)
        cands = []
        for d in self.days:
            cand = base + dt.timedelta(days=(d - now.weekday()) % 7)
            cands.append(cand if cand > now else cand + dt.timedelta(days=7))
        return min(cands)

    def last_slot(self, now: dt.datetime) -> dt.datetime | None:
        """The most recent planned run at or before ``now`` (``every`` has none)."""
        if self.kind == "every":
            return None
        nxt = self.next_after(now)
        if nxt is None:
            return None
        if self.kind == "monthly":
            y, m = (nxt.year - 1, 12) if nxt.month == 1 else (nxt.year, nxt.month - 1)
            return _month_slot(nxt, y, m, self)
        step = dt.timedelta(hours=1) if self.kind == "hourly" else dt.timedelta(days=1)
        cand = nxt - step
        while not self._is_slot(cand):
            cand -= step
        return cand

    def _is_slot(self, t: dt.datetime) -> bool:
        return self.kind != "weekly" or t.weekday() in self.days

    def missed(self, last: dt.datetime | None, now: dt.datetime) -> bool:
        """The last run is older than the period plus 15 %."""
        return last is not None and now - last > self.period() * (1 + GRACE)

    # ------------------------------------------------------------------ words
    def words(self) -> str:
        """Plain words: 'Tuesdays 07:15', 'Every day 03:00', 'Every 6 h'."""
        hm = f"{self.hour:02d}:{self.minute:02d}"
        if self.kind == "daily":
            return f"Every day {hm}"
        if self.kind == "hourly":
            return "Every hour" + (f" at :{self.minute:02d}" if self.minute else "")
        if self.kind == "monthly":
            return f"Monthly on day {self.day} {hm}"
        if self.kind == "every":
            return "Every " + _span(self.period())
        if tuple(sorted(self.days)) == (0, 1, 2, 3, 4):
            return f"Weekdays {hm}"
        return ", ".join(DAY_WORDS[d] for d in sorted(self.days)) + " " + hm

    def short(self) -> str:
        """Upper-case words for a chalk slate: 'EVERY TUE 07:15'."""
        hm = f"{self.hour:02d}:{self.minute:02d}"
        if self.kind == "weekly" and len(self.days) == 1:
            return f"EVERY {DAYS[self.days[0]].upper()} {hm}"
        if self.kind == "weekly" and tuple(sorted(self.days)) != (0, 1, 2, 3, 4):
            return " ".join(DAYS[d].upper() for d in sorted(self.days)) + " " + hm
        return self.words().upper()


def _span(td: dt.timedelta) -> str:
    s = int(td.total_seconds())
    if s % 86400 == 0:
        return f"{s // 86400} d"
    if s % 3600 == 0:
        return f"{s // 3600} h"
    return f"{s // 60} min"


def _month_slot(ref: dt.datetime, y: int, m: int, s: Schedule) -> dt.datetime:
    """``s``'s day/hour/minute in month ``y-m``; a day past the month's end is its last day."""
    d = min(s.day, calendar.monthrange(y, m)[1])
    return ref.replace(year=y, month=m, day=d, hour=s.hour, minute=s.minute, second=0,
                       microsecond=0)


def parse(text: str) -> Schedule:
    """Read one schedule line; ``ValueError`` names what is wrong."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("schedule is empty")
    raw = text.strip()
    s = " ".join(raw.lower().split())
    if m := re.fullmatch(rf"daily {_TIME}", s):
        return Schedule(raw, "daily", hour=int(m[1]), minute=int(m[2]))
    if m := re.fullmatch(rf"weekdays {_TIME}", s):
        return Schedule(raw, "weekly", days=(0, 1, 2, 3, 4), hour=int(m[1]), minute=int(m[2]))
    if m := re.fullmatch(rf"weekly ([a-z, ]+?) {_TIME}", s):
        names = [d.strip()[:3] for d in re.split(r"[ ,]+", m[1]) if d.strip()]
        bad = [n for n in names if n not in DAYS]
        if bad or not names:
            raise ValueError(f"schedule {raw!r}: unknown day {bad[0] if bad else ''!r} "
                             "(use mon tue wed thu fri sat sun)")
        days = tuple(sorted({DAYS.index(n) for n in names}))
        return Schedule(raw, "weekly", days=days, hour=int(m[2]), minute=int(m[3]))
    if m := re.fullmatch(rf"monthly (\d{{1,2}}) {_TIME}", s):
        day = int(m[1])
        if not 1 <= day <= 31:
            raise ValueError(f"schedule {raw!r}: day of the month must be 1 to 31")
        return Schedule(raw, "monthly", day=day, hour=int(m[2]), minute=int(m[3]))
    if m := re.fullmatch(r"hourly(?: :([0-5]\d))?", s):
        return Schedule(raw, "hourly", minute=int(m[1] or 0))
    if m := re.fullmatch(r"every (\d{1,4}) ?(m|min|h|d)", s):
        n = int(m[1])
        unit = {"m": 60, "min": 60, "h": 3600, "d": 86400}[m[2]]
        if n <= 0:
            raise ValueError(f"schedule {raw!r}: the period must be more than 0")
        return Schedule(raw, "every", every=dt.timedelta(seconds=n * unit))
    raise ValueError(
        f"schedule {raw!r} is not understood (examples: 'daily 03:00', 'weekly tue 07:15', "
        "'weekdays 08:30', 'monthly 1 06:00', 'hourly :15', 'every 6h')"
    )
