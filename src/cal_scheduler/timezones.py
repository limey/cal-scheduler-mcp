"""Datetime parsing and timezone resolution — the heart of why this server exists.

The rule (single-locale calendar, deliberately simple):

- **Naive** input (no offset/zone) → assumed to be *wall time* in the configured
  default zone. This is the common agent path: models often emit a bare local
  time. We resolve it visibly and report what we assumed, rather than rejecting.
- **Offset-qualified** input (the `+12:00` / `+13:00` models append) → honoured as
  an instant, then re-expressed in the default zone. Same wall clock when the
  offset already matches the zone; a deliberate, correct conversion otherwise.

Either way the result is a tz-aware datetime *in the default zone*, which is then
stored with `TZID`/`VTIMEZONE`. That is what keeps a weekly 9am at 9am across a DST
boundary instead of drifting (the classic failure mode of storing bare UTC).

Date-only values (no `T`) are treated as all-day dates and carry no zone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeError(ValueError):
    """A datetime string or zone could not be resolved. Message is agent-facing."""


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise TimeError(f"Unknown time zone {name!r}") from exc


@dataclass
class Resolved:
    """A parsed temporal value.

    `value` is either a tz-aware `datetime` (in the default zone) or a `date`
    (all-day). `assumed` is True when a naive datetime was interpreted as default-
    zone wall time. `note` is a terse, agent-readable summary of what happened.
    """

    value: datetime | date
    assumed: bool
    note: str

    @property
    def is_all_day(self) -> bool:
        return isinstance(self.value, date) and not isinstance(self.value, datetime)


def resolve(value: str, zone: ZoneInfo) -> Resolved:
    s = (value or "").strip()
    if not s:
        raise TimeError("empty datetime")

    if _DATE_ONLY.match(s):
        return Resolved(date.fromisoformat(s), assumed=False, note="all-day date")

    # Normalise a trailing Z (fromisoformat handles it on 3.11+, but be explicit).
    iso = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise TimeError(
            f"could not parse datetime {value!r}; expected ISO 8601 "
            "(e.g. 2026-06-30T21:00 or 2026-06-30T21:00+12:00)"
        ) from exc

    if dt.tzinfo is None:
        return Resolved(
            dt.replace(tzinfo=zone),
            assumed=True,
            note=f"assumed {zone.key} wall time",
        )

    converted = dt.astimezone(zone)
    if converted.utcoffset() == dt.utcoffset():
        note = f"interpreted in {zone.key}"
    else:
        note = f"converted to {zone.key} ({converted:%H:%M})"
    return Resolved(converted, assumed=False, note=note)
