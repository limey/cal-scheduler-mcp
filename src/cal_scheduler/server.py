"""The MCP server: a strict, deterministic CalDAV boundary.

The LLM does English→params; this server validates, rejects loudly, and persists
zoned .ics. It is not an NLP layer. Every tool returns a small dict; on bad input
it raises with a caller-actionable message (FastMCP surfaces it as a tool error).

The only clock touched is DTSTAMP/LAST-MODIFIED on write — everything else is pure,
so the same input yields the same bytes (handy if the .ics store is kept in git).
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from . import ical
from .config import Config
from .store import Store
from .timezones import Resolved, get_zone, resolve

mcp = FastMCP("cal-scheduler")


@lru_cache(maxsize=1)
def _config() -> Config:
    return Config.from_env()


@lru_cache(maxsize=1)
def _store() -> Store:
    cfg = _config()
    return Store(cfg.base_url, cfg.username, cfg.password)


def _zone() -> ZoneInfo:
    return get_zone(_config().default_tz)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pick_calendar(calendar: str | None) -> str:
    """Resolve which calendar a call targets, with friendly fallbacks."""
    if calendar:
        return calendar
    cfg = _config()
    if cfg.default_calendar:
        return cfg.default_calendar
    names = _store().calendar_names()
    if len(names) == 1:
        return names[0]
    raise ValueError(
        "no calendar given and no default; specify one of: "
        + (", ".join(names) or "(none — create one first)")
    )


def _resolve_dt(value: str) -> Resolved:
    return resolve(value, _zone())


def _nonpositive_interval(start_dt, end_dt) -> bool:
    """True if both are timed and end is not strictly after start (RFC 5545 forbids
    a zero/negative-length timed event). The iCal-layer check in ical.py is the
    domain-vocab fallback; tools translate this into MCP-parameter vocabulary."""
    return (
        end_dt is not None
        and isinstance(start_dt, datetime)
        and isinstance(end_dt, datetime)
        and end_dt <= start_dt
    )


# ── calendars ─────────────────────────────────────────────────────────────────


@mcp.tool()
def list_calendars() -> dict:
    """List the calendars available on the account."""
    return {"calendars": _store().calendar_names()}


@mcp.tool()
def create_calendar(name: str) -> dict:
    """Create a new calendar by display name."""
    _store().create_calendar(name)
    return {"ok": True, "created": name}


@mcp.tool()
def delete_calendar(name: str) -> dict:
    """Delete a calendar and all of its events. Irreversible."""
    _store().delete_calendar(name)
    return {"ok": True, "deleted": name}


# ── reads ───────────────────────────────────────────────────────────────────--


@mcp.tool()
def list_events(start: str, end: str, calendar: str | None = None) -> dict:
    """List event occurrences in [start, end], expanding recurring series.

    Dates are interpreted in the calendar's configured zone. Returns one entry per
    occurrence (recurring instances are expanded), sorted by start.
    """
    cal_name = _pick_calendar(calendar)
    zone = _zone()
    lo = _resolve_dt(start).value
    hi = _resolve_dt(end).value
    # recurring_ical_events.between wants datetimes; widen all-day bounds.
    lo_dt = lo if isinstance(lo, datetime) else datetime(lo.year, lo.month, lo.day, tzinfo=zone)
    hi_dt = hi if isinstance(hi, datetime) else datetime(hi.year, hi.month, hi.day, tzinfo=zone)

    import recurring_ical_events

    occs = []
    for raw in _store().search_raw(cal_name, lo_dt, hi_dt):
        cal = ical.parse(raw)
        for occ in recurring_ical_events.of(cal).between(lo_dt, hi_dt):
            occs.append(ical.occurrence_dict(occ))
    occs.sort(key=lambda e: e["start"])
    return {"calendar": cal_name, "count": len(occs), "events": occs}


@mcp.tool()
def resolve_datetime(value: str) -> dict:
    """Show how a datetime string will be interpreted, without writing anything.

    Use this to confirm a zone before committing an event.
    """
    r = _resolve_dt(value)
    return {"input": value, "resolved": r.value.isoformat(), "note": r.note}


# ── writes ──────────────────────────────────────────────────────────────────--


@mcp.tool()
def create_event(
    summary: str,
    start: str,
    end: str | None = None,
    calendar: str | None = None,
    description: str | None = None,
    location: str | None = None,
    rrule: str | None = None,
) -> dict:
    """Create an event (single, or recurring if `rrule` is given).

    `start`/`end` are ISO 8601. A bare local time is assumed to be wall time in the
    calendar's zone; an offset-qualified time is honoured and stored in that zone.
    With no `end`, the event defaults to 1 hour (all-day if `start` is date-only).
    `rrule` is a raw RRULE body, e.g. "FREQ=WEEKLY;COUNT=12".
    """
    cal_name = _pick_calendar(calendar)
    rs = _resolve_dt(start)
    notes = [rs.note]
    dtend = None
    if end is not None:
        re_ = _resolve_dt(end)
        dtend = re_.value
        if isinstance(rs.value, datetime) != isinstance(dtend, datetime):
            raise ValueError("start and end must both be timed or both be all-day dates")
        if _nonpositive_interval(rs.value, dtend):
            raise ValueError("`end` must be after `start` — omit `end` for a 1-hour default")

    recur = None
    if rrule:
        recur = ical.validate_and_normalize_rrule(ical.parse_rrule(rrule), rs.value, _zone())

    uid = ical.new_uid()
    ev = ical.build_event(
        uid=uid,
        summary=summary,
        dtstart=rs.value,
        dtend=dtend,
        now=_now(),
        description=description,
        location=location,
        recur=recur,
    )
    _store().save_new_event(cal_name, ical.serialize(ical.event_calendar(ev)))
    return {"ok": True, "uid": uid, "calendar": cal_name, "note": "; ".join(notes)}


@mcp.tool()
def update_event(
    uid: str,
    calendar: str | None = None,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    rrule: str | None = None,
) -> dict:
    """Edit a whole event/series. Only the fields you pass change.

    Preserves the UID and any single-occurrence exclusions/overrides. If you move
    `start` without giving `end`, the duration is kept. Moving `start` re-anchors
    the whole series — occurrences before the new start stop being generated (this
    retimes an entire series; it does not split one at a date).
    """
    cal_name = _pick_calendar(calendar)
    store = _store()
    event = store.fetch_event(cal_name, uid)
    cal = ical.parse(event.data)
    ev = ical.master(cal)
    zone = _zone()
    notes: list[str] = []

    old_start = ev.decoded("dtstart")
    old_end = ev.decoded("dtend") if "dtend" in ev else None
    duration = (old_end - old_start) if old_end is not None else None

    new_start = old_start
    if start is not None:
        rs = _resolve_dt(start)
        new_start = rs.value
        notes.append(rs.note)

    if end is not None:
        new_end = _resolve_dt(end).value
    elif start is not None and duration is not None:
        new_end = new_start + duration
    else:
        new_end = old_end

    if start is not None:
        # Moving the anchor later silently drops the rule occurrences in the gap;
        # report the count (computed BEFORE we mutate DTSTART) so it isn't silent.
        dropped = ical.dropped_on_reanchor(cal, old_start, new_start, zone)
        if dropped:
            cutoff = (new_start.date() if isinstance(new_start, datetime) else new_start).isoformat()
            notes.append(
                f"dropped {dropped} earlier occurrence{'' if dropped == 1 else 's'} "
                f"(before {cutoff}); series now starts then"
            )

    if start is not None or end is not None:
        if isinstance(new_start, datetime) != isinstance(new_end, datetime):
            raise ValueError("start and end must both be timed or both be all-day dates")
        if _nonpositive_interval(new_start, new_end):
            raise ValueError("`end` must be after `start` — omit `end` to keep the existing duration")
        for key in ("dtstart", "dtend"):
            if key in ev:
                del ev[key]
        ev.add("dtstart", new_start)
        if new_end is not None:
            ev.add("dtend", new_end)

    if summary is not None:
        _set(ev, "summary", summary)
    if description is not None:
        _set(ev, "description", description)
    if location is not None:
        _set(ev, "location", location)

    if rrule is not None:
        recur = ical.validate_and_normalize_rrule(ical.parse_rrule(rrule), new_start, zone)
        if "rrule" in ev:
            del ev["rrule"]
        ev.add("rrule", recur)
    elif "rrule" in ev and start is not None:
        # anchor moved under an existing rule — re-validate it against the new start
        ical.validate_and_normalize_rrule(ev["rrule"], new_start, zone)

    ical.touch(ev, _now())
    store.write_back(event, ical.serialize(cal))
    return {"ok": True, "uid": uid, "calendar": cal_name, "note": "; ".join(notes)}


def _set(ev, key: str, value: str) -> None:
    if key in ev:
        del ev[key]
    ev.add(key, value)


@mcp.tool()
def delete_event(uid: str, calendar: str | None = None) -> dict:
    """Delete a whole event/series (and any of its overrides). Irreversible."""
    cal_name = _pick_calendar(calendar)
    _store().delete_event(cal_name, uid)
    return {"ok": True, "deleted": uid, "calendar": cal_name}


@mcp.tool()
def exclude_occurrence(uid: str, occurrence: str, calendar: str | None = None) -> dict:
    """Drop a single occurrence of a recurring series (EXDATE).

    `occurrence` is the start of the instance to remove, as listed by list_events.
    """
    cal_name = _pick_calendar(calendar)
    store = _store()
    event = store.fetch_event(cal_name, uid)
    cal = ical.parse(event.data)
    occ = _resolve_dt(occurrence).value
    ical.add_exdate(cal, occ, _now())
    store.write_back(event, ical.serialize(cal))
    return {"ok": True, "uid": uid, "excluded": occ.isoformat()}


@mcp.tool()
def move_occurrence(
    uid: str,
    occurrence: str,
    new_start: str,
    new_end: str | None = None,
    calendar: str | None = None,
) -> dict:
    """Reschedule a single occurrence of a series (RECURRENCE-ID override).

    `occurrence` is the instance's current start; `new_start`/`new_end` are where
    it moves to. Omit `new_end` to keep the occurrence's existing duration. The
    rest of the series is unchanged.
    """
    cal_name = _pick_calendar(calendar)
    store = _store()
    event = store.fetch_event(cal_name, uid)
    cal = ical.parse(event.data)
    occ = _resolve_dt(occurrence).value
    ns = _resolve_dt(new_start)
    ne = _resolve_dt(new_end).value if new_end is not None else None
    if _nonpositive_interval(ns.value, ne):
        raise ValueError(
            "`new_end` must be after `new_start` — "
            "omit `new_end` to keep the occurrence's existing duration"
        )
    ical.add_override(cal, occurrence=occ, new_start=ns.value, new_end=ne, now=_now())
    store.write_back(event, ical.serialize(cal))
    return {"ok": True, "uid": uid, "moved_from": occ.isoformat(), "note": ns.note}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
