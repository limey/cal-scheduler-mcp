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

# Single source of truth for the custom X- property name. Both the write
# path (mark_done / clear_done) and the read path (done_at) route through
# this so the property cannot drift between stored and parsed values.
_DONE_PROPERTY = "X-CAL-SCHEDULER-DONE"

_DONE_TIMESTAMP = "%Y%m%dT%H%M%SZ"


def mark_done(ev: Event, done_at: datetime) -> None:
    """Stamp the done marker on a VEVENT (idempotent; replaces any prior stamp)."""
    clear_done(ev)
    ev.add(_DONE_PROPERTY, done_at.astimezone(ZoneInfo("UTC")).strftime(_DONE_TIMESTAMP))


def clear_done(ev: Event) -> None:
    """Strip the done marker from a VEVENT (idempotent; no-op when absent)."""
    if _DONE_PROPERTY in ev:
        del ev[_DONE_PROPERTY]


def done_at(ev: Event) -> datetime | None:
    """Parsed UTC timestamp on the done marker, or None when not marked."""
    if _DONE_PROPERTY not in ev:
        return None
    raw = str(ev[_DONE_PROPERTY])
    parsed = datetime.strptime(raw, _DONE_TIMESTAMP).replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def override_for_occurrence(
    cal: Calendar, occurrence: datetime | date
) -> Event | None:
    """The override VEVENT whose RECURRENCE-ID matches `occurrence`, else None."""
    target = occurrence
    ev = master(cal)
    if isinstance(ev.decoded("dtstart"), datetime) and isinstance(target, datetime):
        target = target.astimezone(ZoneInfo("UTC"))
    for comp in vevents(cal):
        if "RECURRENCE-ID" not in comp:
            continue
        rid = comp.decoded("recurrence-id")
        if isinstance(rid, datetime) and isinstance(target, datetime):
            if rid.astimezone(ZoneInfo("UTC")) == target:
                return comp
        elif rid == target:
            return comp
    return None


def override_is_done_only(comp: Event, master_ev: Event) -> bool:
    """True when the override carries only the done marker (no real edits).

    A minimal override exists solely to tag one occurrence done — same time,
    same summary, same description/location as the master, and only the X-
    added. Removing the X- leaves nothing meaningful, so the whole component
    can be deleted instead of leaving an empty shell.
    """
    if _DONE_PROPERTY not in comp:
        return False
    ds = master_ev.decoded("dtstart")
    if "DTEND" in comp and "DTEND" in master_ev:
        if comp.decoded("dtend") != master_ev.decoded("dtend"):
            return False
    elif "DTEND" in comp and "DTEND" not in master_ev:
        return False
    if comp.decoded("dtstart") != ds:
        return False
    if str(comp.get("summary", "")) != str(master_ev.get("summary", "")):
        return False
    for opt_key in ("description", "location"):
        if str(comp.get(opt_key, "")) != str(master_ev.get(opt_key, "")):
            return False
    return True


def build_done_override(
    *,
    master_ev: Event,
    occurrence: datetime | date,
    done_at: datetime,
) -> Event:
    """Build a minimal override that stamps the done marker for one occurrence.

    Mirrors `add_override` (same UID, RECURRENCE-ID = occurrence, master copy)
    but skips the time move — the override's only purpose is to carry X-.
    """
    ds = master_ev.decoded("dtstart")
    de = master_ev.decoded("dtend") if "DTEND" in master_ev else None
    if isinstance(ds, datetime) and isinstance(occurrence, datetime):
        occurrence = occurrence.astimezone(ds.tzinfo)
    override = build_event(
        uid=str(master_ev["uid"]),
        summary=str(master_ev.get("summary", "")),
        dtstart=ds,
        dtend=de,
        now=done_at,
        description=_opt(master_ev, "description"),
        location=_opt(master_ev, "location"),
        sequence=int(master_ev.get("sequence", 0)) + 1,
    )
    override.add("recurrence-id", occurrence)
    mark_done(override, done_at)
    return override


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
    import recurring_ical_events
    from copy import deepcopy

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


def done_at_for_occurrences(
    cal: Calendar, expanded: list
) -> dict[int, datetime | None]:
    """Map each expanded occurrence index to its done marker (or None).

    `expanded` is the list returned by `recurring_ical_events.of(cal).between(...)`.
    The map is keyed by the occurrence's position in that list — keys are stable
    even when the same start time appears multiple times in a series (e.g. after
    a move).

    Done marker lookup priority per occurrence:
    1. Override VEVENT whose RECURRENCE-ID matches the occurrence's original
       (pre-move) start.
    2. Else the master VEVENT's done marker (whole-series mark).
    """
    import recurring_ical_events
    from copy import deepcopy

    master_ev = master(cal)
    master_done = done_at(master_ev)

    # Re-expand the master alone (no overrides) to recover each occurrence's
    # original start. An expanded occurrence from the full calendar carries
    # the post-move start; pairing by index gives the original.
    master_only = new_calendar()
    master_only.add_component(deepcopy(master_ev))
    if "rrule" in master_ev:
        originals = [o.start for o in recurring_ical_events.of(master_only).between(
            master_ev.decoded("dtstart"),
            master_ev.decoded("dtstart") + _SERIES_HORIZON * 10,
        )]
    else:
        originals = [master_ev.decoded("dtstart")]

    overrides = {
        comp.decoded("recurrence-id"): done_at(comp)
        for comp in vevents(cal)
        if "RECURRENCE-ID" in comp
    }

    out: dict[int, datetime | None] = {}
    for i, occ in enumerate(expanded):
        original = originals[i] if i < len(originals) else occ.start
        # Normalise the override map's key to UTC for tz-aware comparison.
        match = None
        for rid, value in overrides.items():
            rid_cmp = rid
            if isinstance(rid, datetime) and isinstance(original, datetime):
                rid_cmp = rid.astimezone(ZoneInfo("UTC"))
                original_cmp = original.astimezone(ZoneInfo("UTC"))
            else:
                original_cmp = original
            if rid_cmp == original_cmp:
                match = value
                break
        out[i] = match if match is not None else master_done
    return out


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


def occurrence_dict(
    occ: Event, *, recurring: bool = False, done_at: datetime | None = None
) -> dict:
    """Serialise one expanded occurrence into the agent-facing dict.

    `recurring` is the source of truth for the `recurring` flag — the
    caller derives it from the source calendar's master VEVENT (RRULE
    iff series) and passes it in.

    Do NOT derive `recurring` from the occurrence itself: the expansion
    library stamps a `RECURRENCE-ID` on every expansion including
    one-off events, which would leak `"recurring": true` and break the
    agent's single- vs series-edit decision.

    `done_at` is the parsed UTC timestamp of the done marker on this
    occurrence (override X- takes priority over master X-); None when
    not marked. The caller computes it from the master + override pair,
    not from the expanded occurrence.
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
        "done_at": done_at.isoformat() if done_at is not None else None,
    }
    if "location" in occ:
        out["location"] = str(occ["location"])
    if "description" in occ:
        out["description"] = str(occ["description"])
    if recurring:
        out["recurring"] = True
    return out
