"""iCalendar construction, parsing, expansion, and single-occurrence ops.

We compose libraries rather than implement a calendar engine:
- `icalendar` builds/serialises components and injects `VTIMEZONE`.
- `recurring_ical_events` expands RRULE/EXDATE/RDATE/overrides over a range.

The contract this module enforces (the *what*, not the *how*):
- store zoned (TZID/VTIMEZONE) so wall time is DST-stable;
- reject an RRULE whose anchor contradicts it (the "June 30 + every 1st" bug);
- `UNTIL` is UTC when `DTSTART` is a zoned datetime (RFC 5545);
- stable UID; advance SEQUENCE / LAST-MODIFIED / DTSTAMP on every edit;
- single-occurrence exclude (EXDATE) / move (RECURRENCE-ID) that actually take.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vRecur

PRODID = "-//limey//cal-scheduler//EN"

# IANA weekday tokens used by BYDAY, in Python weekday() order (Mon=0).
_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class ValidationError(ValueError):
    """Input violates the calendar contract. Message is agent-facing."""


# ── calendar/component helpers ────────────────────────────────────────────────


def new_calendar() -> Calendar:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    return cal


def parse(ics: str | bytes) -> Calendar:
    return Calendar.from_ical(ics)


def serialize(cal: Calendar) -> str:
    # Inject VTIMEZONE for every TZID used, then emit. This is what makes the
    # stored .ics self-contained and DST-correct in any client.
    cal.add_missing_timezones()
    return cal.to_ical().decode("utf-8")


def vevents(cal: Calendar) -> list[Event]:
    return [c for c in cal.walk("VEVENT")]


def master(cal: Calendar) -> Event:
    """The master VEVENT (the one without a RECURRENCE-ID)."""
    for ev in vevents(cal):
        if "RECURRENCE-ID" not in ev:
            return ev
    raise ValidationError("no master VEVENT found in calendar object")


def new_uid() -> str:
    return f"{uuid.uuid4()}@cal-scheduler"


# Single source of truth for the default end-time applied when `end` is
# omitted. Both `build_event` (which persists) and the tool-layer disclosure
# go through this, so the persisted value and the disclosure message cannot
# drift if the default ever changes.
_DEFAULT_TIMED_DTEND = timedelta(hours=1)
_DEFAULT_ALL_DAY_DTEND = timedelta(days=1)  # DTEND is exclusive for all-day


def default_dtend(dtstart: datetime | date) -> datetime | date:
    """`dtend` applied when the caller omits it: timed → +1h, all-day → +1d."""
    if isinstance(dtstart, datetime):
        return dtstart + _DEFAULT_TIMED_DTEND
    return dtstart + _DEFAULT_ALL_DAY_DTEND


def default_durations() -> tuple[timedelta, timedelta]:
    """`(timed_default, all_day_default)` from the same single source of truth."""
    return (_DEFAULT_TIMED_DTEND, _DEFAULT_ALL_DAY_DTEND)


# ── recurrence validation ─────────────────────────────────────────────────────


def parse_rrule(rule: str) -> vRecur:
    s = rule.strip()
    if s.upper().startswith("RRULE:"):
        s = s[len("RRULE:") :]
    try:
        return vRecur.from_ical(s)
    except (ValueError, KeyError) as exc:
        raise ValidationError(f"could not parse RRULE {rule!r}: {exc}") from exc


def validate_and_normalize_rrule(
    recur: vRecur, dtstart: datetime | date, zone: ZoneInfo
) -> vRecur:
    """Reject an anchor that contradicts the rule; normalise UNTIL to UTC.

    Catches the malformed self-inconsistent series (e.g. DTSTART on the 30th with
    BYMONTHDAY=1) that many calendar tools persist verbatim. Validates the common
    BY* parts against DTSTART; positional/negative selectors pass through unchecked.
    """
    if "FREQ" not in recur:
        raise ValidationError("RRULE must include FREQ")

    if "COUNT" in recur and "UNTIL" in recur:
        raise ValidationError("RRULE cannot set both COUNT and UNTIL (RFC 5545)")

    def _vals(key: str) -> list:
        v = recur.get(key)
        if v is None:
            return []
        return list(v) if isinstance(v, (list, tuple)) else [v]

    bymonth = _vals("BYMONTH")
    if bymonth and dtstart.month not in bymonth:
        raise ValidationError(
            f"DTSTART month ({dtstart.month}) is not in RRULE BYMONTH={bymonth}; "
            "the anchor contradicts the rule"
        )

    bymonthday = [d for d in _vals("BYMONTHDAY") if isinstance(d, int) and d > 0]
    if bymonthday and dtstart.day not in bymonthday:
        raise ValidationError(
            f"DTSTART day ({dtstart.day}) is not in RRULE BYMONTHDAY={bymonthday}; "
            "the anchor contradicts the rule (e.g. start on the 30th but repeat on the 1st)"
        )

    byday_plain = [d for d in _vals("BYDAY") if isinstance(d, str) and d in _WEEKDAYS]
    if byday_plain and _WEEKDAYS[dtstart.weekday()] not in byday_plain:
        raise ValidationError(
            f"DTSTART weekday ({_WEEKDAYS[dtstart.weekday()]}) is not in "
            f"RRULE BYDAY={byday_plain}; the anchor contradicts the rule"
        )

    # UNTIL must be UTC when DTSTART is a zoned datetime (RFC 5545 3.3.10).
    until = recur.get("UNTIL")
    if until is not None:
        u = until[0] if isinstance(until, (list, tuple)) else until
        if isinstance(dtstart, datetime):
            if not isinstance(u, datetime):
                # date UNTIL against a timed series — promote to end of that day, UTC.
                u = datetime(u.year, u.month, u.day, 23, 59, 59, tzinfo=zone)
            if u.tzinfo is None:
                u = u.replace(tzinfo=zone)
            recur["UNTIL"] = [u.astimezone(ZoneInfo("UTC"))]
        else:
            # all-day series: UNTIL should be a date
            if isinstance(u, datetime):
                recur["UNTIL"] = [u.date()]
    return recur


# ── build ─────────────────────────────────────────────────────────────────────


def build_event(
    *,
    uid: str,
    summary: str,
    dtstart: datetime | date,
    dtend: datetime | date | None,
    now: datetime,
    description: str | None = None,
    location: str | None = None,
    recur: vRecur | None = None,
    sequence: int = 0,
) -> Event:
    all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    if dtend is None:
        dtend = default_dtend(dtstart)
    if not all_day and dtend <= dtstart:
        raise ValidationError("DTEND must be after DTSTART")
    if all_day and dtend <= dtstart:
        raise ValidationError("all-day end date must be after start date")

    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstamp", now)
    ev.add("created", now)
    ev.add("last-modified", now)
    ev.add("sequence", sequence)
    ev.add("summary", summary)
    ev.add("dtstart", dtstart)
    ev.add("dtend", dtend)
    if description:
        ev.add("description", description)
    if location:
        ev.add("location", location)
    if recur is not None:
        ev.add("rrule", recur)
    return ev


def event_calendar(ev: Event) -> Calendar:
    cal = new_calendar()
    cal.add_component(ev)
    return cal


def touch(ev: Event, now: datetime) -> None:
    """Advance the sync-relevant fields clients watch on edit."""
    seq = int(ev.get("sequence", 0))
    ev["sequence"] = seq + 1
    ev["last-modified"] = _wrap(now, "last-modified")
    ev["dtstamp"] = _wrap(now, "dtstamp")


def _wrap(dt: datetime, name: str):
    # icalendar wants a vDatetime; add() does the wrapping, so route through a
    # throwaway component to reuse its coercion.
    tmp = Event()
    tmp.add(name, dt)
    return tmp[name]


# ── single-occurrence operations ──────────────────────────────────────────────


def add_exdate(cal: Calendar, occurrence: datetime | date, now: datetime) -> None:
    """Exclude one occurrence by appending an EXDATE that matches its identity.

    The EXDATE value type + tz must match the generated occurrence (zoned datetime
    vs date), or the exclusion silently does nothing — the exact failure mode the
    spec flags as the hardest spot.
    """
    ev = master(cal)
    ds = ev.decoded("dtstart")
    ds_is_dt = isinstance(ds, datetime)
    occ_is_dt = isinstance(occurrence, datetime)
    if ds_is_dt != occ_is_dt:
        raise ValidationError(
            "occurrence value type does not match the series "
            f"({'timed' if ds_is_dt else 'all-day'} series, "
            f"{'timed' if occ_is_dt else 'all-day'} occurrence given)"
        )
    if ds_is_dt and isinstance(ds.tzinfo, ZoneInfo):
        occurrence = occurrence.astimezone(ds.tzinfo)

    # Append to existing EXDATEs rather than clobbering them.
    existing: list = []
    if "EXDATE" in ev:
        ex = ev["EXDATE"]
        for item in ex if isinstance(ex, list) else [ex]:
            existing.extend(d.dt for d in item.dts)
        del ev["EXDATE"]
    existing.append(occurrence)
    for d in existing:
        ev.add("exdate", d)
    touch(ev, now)


def add_override(
    cal: Calendar,
    *,
    occurrence: datetime | date,
    new_start: datetime | date,
    new_end: datetime | date | None,
    now: datetime,
    summary: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> None:
    """Reschedule one occurrence via a RECURRENCE-ID override component.

    The override shares the master UID and carries RECURRENCE-ID = the *original*
    occurrence's start. It lives in the same .ics resource as the master.
    """
    ev = master(cal)
    ds = ev.decoded("dtstart")
    if isinstance(ds, datetime) and isinstance(ds.tzinfo, ZoneInfo) and isinstance(
        occurrence, datetime
    ):
        occurrence = occurrence.astimezone(ds.tzinfo)

    uid = str(ev["uid"])
    if new_end is None:
        if isinstance(ds, datetime) and "DTEND" in ev:
            duration = ev.decoded("dtend") - ds
            new_end = new_start + duration
    override = build_event(
        uid=uid,
        summary=summary or str(ev.get("summary", "")),
        dtstart=new_start,
        dtend=new_end,
        now=now,
        description=description if description is not None else _opt(ev, "description"),
        location=location if location is not None else _opt(ev, "location"),
        sequence=int(ev.get("sequence", 0)) + 1,
    )
    override.add("recurrence-id", occurrence)
    cal.add_component(override)
    touch(ev, now)


def _opt(ev: Event, key: str) -> str | None:
    return str(ev[key]) if key in ev else None


# ── done marker ───────────────────────────────────────────────────────────────

DONE_PROPERTY = "X-CAL-SCHEDULER-DONE"
"""Custom X- property carrying a UTC datetime when the event is marked done.

RFC 5545 reserves X- for vendor/experimental use; foreign consumers
must ignore it. Absence means not done; presence = done-at in UTC.
"""


def mark_event_done(cal: Calendar, now: datetime) -> None:
    """Stamp the master VEVENT done (idempotent — replaces any prior stamp)."""
    ev = master(cal)
    if DONE_PROPERTY in ev:
        del ev[DONE_PROPERTY]
    ev.add(DONE_PROPERTY, now.astimezone(ZoneInfo("UTC")))
    touch(ev, now)


def add_done_override(
    cal: Calendar, *, occurrence: datetime | date, now: datetime
) -> None:
    """Stamp one occurrence of a recurring series done via a RECURRENCE-ID override.

    Mirrors `add_override` in shape, but the override carries only the done
    marker — the occurrence's time is unchanged. If an override already
    exists at this RECURRENCE-ID (e.g. from a prior `move_occurrence`), the
    marker is stamped on the existing override. Idempotent — re-marking
    replaces the prior timestamp.
    """
    ev = master(cal)
    ds = ev.decoded("dtstart")
    # Zone-normalise `occurrence` to the master's zone (same as add_override).
    if isinstance(ds, datetime) and isinstance(ds.tzinfo, ZoneInfo) and isinstance(
        occurrence, datetime
    ):
        occurrence = occurrence.astimezone(ds.tzinfo)

    # Find an existing override for this RECURRENCE-ID, or build a new one.
    target = occurrence
    for comp in vevents(cal):
        if "RECURRENCE-ID" not in comp:
            continue
        rid = comp.decoded("recurrence-id")
        if isinstance(rid, datetime) and isinstance(target, datetime):
            if rid.astimezone(ds.tzinfo if isinstance(ds.tzinfo, ZoneInfo) else ZoneInfo("UTC")) == target:
                # Existing override found — stamp the marker on it.
                if DONE_PROPERTY in comp:
                    del comp[DONE_PROPERTY]
                comp.add(DONE_PROPERTY, now.astimezone(ZoneInfo("UTC")))
                touch(ev, now)
                return
        elif rid == target:
            if DONE_PROPERTY in comp:
                del comp[DONE_PROPERTY]
            comp.add(DONE_PROPERTY, now.astimezone(ZoneInfo("UTC")))
            touch(ev, now)
            return

    # No existing override — build a minimal one carrying only the done marker.
    de = ev.decoded("dtend") if "DTEND" in ev else None
    duration = (de - ds) if de is not None and ds is not None else None
    override = build_event(
        uid=str(ev["uid"]),
        summary=str(ev.get("summary", "")),
        dtstart=occurrence,
        dtend=(occurrence + duration) if duration is not None else de,
        now=now,
        description=_opt(ev, "description"),
        location=_opt(ev, "location"),
        sequence=int(ev.get("sequence", 0)) + 1,
    )
    override.add("recurrence-id", occurrence)
    override.add(DONE_PROPERTY, now.astimezone(ZoneInfo("UTC")))
    cal.add_component(override)
    touch(ev, now)


# ── expansion / serialisation for reads ───────────────────────────────────────


def _as_dt(value: datetime | date, zone: ZoneInfo) -> datetime:
    """Normalise a date/datetime to a tz-aware datetime for range comparison."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=zone)
    return datetime(value.year, value.month, value.day, tzinfo=zone)


def dropped_on_reanchor(
    cal: Calendar, old_start: datetime | date, new_start: datetime | date, zone: ZoneInfo
) -> int:
    """How many occurrences vanish when a recurring master's anchor moves later.

    Counts the master's rule occurrences in [old_start, new_start) that are neither
    EXDATE-excluded nor preserved as a RECURRENCE-ID override (a moved occurrence
    survives the re-anchor as its own component). Returns 0 when the master has no
    RRULE or the anchor didn't move forward. Call BEFORE mutating DTSTART.
    """
    from copy import deepcopy

    import recurring_ical_events

    ev = master(cal)
    if "rrule" not in ev:
        return 0
    lo = _as_dt(old_start, zone)
    hi = _as_dt(new_start, zone)
    if hi <= lo:
        return 0

    # Expand the master rule ALONE (no override components) so the count is the
    # plain series; EXDATEs on the master are still honoured (so excluded dates
    # don't count as "dropped" — they weren't happening anyway).
    master_only = new_calendar()
    master_only.add_component(deepcopy(ev))
    count = 0
    for occ in recurring_ical_events.of(master_only).between(lo, hi):
        if lo <= _as_dt(occ.start, zone) < hi:
            count += 1

    # A moved occurrence whose ORIGINAL time falls in the window survives the
    # re-anchor as its own RECURRENCE-ID component, so it wasn't dropped.
    for comp in vevents(cal):
        if "RECURRENCE-ID" in comp and lo <= _as_dt(comp.decoded("recurrence-id"), zone) < hi:
            count -= 1
    return max(count, 0)


# Horizon for unbounded series in `count_series`. Long enough to cover any
# realistic finite series; the response field is documented as "over the
# horizon" for the infinite case so a cold agent reading a count cannot
# mistake it for a lifetime value.
_SERIES_HORIZON = timedelta(days=730)


def count_series(
    cal: Calendar, *, horizon: timedelta = _SERIES_HORIZON
) -> tuple[int, int]:
    """Return (instances, overrides) for the series over `horizon` from DTSTART.

    A finite series (RRULE with COUNT or UNTIL) returns the full lifetime count;
    an infinite one returns the bounded horizon count. Instances are the
    rule-generated occurrences minus EXDATE, plus RECURRENCE-ID overrides — the
    distinct instances the series still produces.
    """
    import recurring_ical_events

    ev = master(cal)
    overrides = sum(1 for c in vevents(cal) if "RECURRENCE-ID" in c)
    if "rrule" not in ev:
        return 1, overrides

    ds = ev.decoded("dtstart")
    if isinstance(ds, datetime):
        lo, hi = ds, ds + horizon
    else:
        lo = datetime(ds.year, ds.month, ds.day, tzinfo=ZoneInfo("UTC"))
        hi = lo + horizon

    n = sum(1 for _ in recurring_ical_events.of(cal).between(lo, hi))
    return n, overrides


def occurrence_dict(occ: Event, *, recurring: bool = False) -> dict:
    """Serialise one expanded occurrence into the agent-facing dict.

    `recurring` is the source of truth for the `recurring` flag — the
    caller derives it from the source calendar's master VEVENT (RRULE
    iff series) and passes it in.

    Do NOT derive `recurring` from the occurrence itself: the expansion
    library stamps a `RECURRENCE-ID` on every expansion including
    one-off events, which would leak `"recurring": true` and break the
    agent's single- vs series-edit decision.
    """
    start = occ.start
    end = occ.end
    all_day = isinstance(start, date) and not isinstance(start, datetime)
    out = {
        "uid": str(occ.get("uid", "")),
        "summary": str(occ.get("summary", "")),
        "start": start.isoformat(),
        "end": end.isoformat() if end is not None else None,
        "all_day": all_day,
        "done": False,
        "done_at": None,
    }
    if "location" in occ:
        out["location"] = str(occ["location"])
    if "description" in occ:
        out["description"] = str(occ["description"])
    if recurring:
        out["recurring"] = True
    if DONE_PROPERTY in occ:
        done_dt = occ.decoded(DONE_PROPERTY)
        out["done"] = True
        out["done_at"] = done_dt.isoformat() if isinstance(done_dt, datetime) else str(done_dt)
    return out
