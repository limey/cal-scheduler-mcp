"""The MCP server: a strict, deterministic CalDAV boundary.

The LLM does English→params; this server validates, rejects loudly, and persists
zoned .ics. It is not an NLP layer. Every tool returns a small dict; on bad input
it raises with a caller-actionable message (FastMCP surfaces it as a tool error).

The only clock touched is DTSTAMP/LAST-MODIFIED on write — everything else is pure,
so the same input yields the same bytes (handy if the .ics store is kept in git).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from pydantic import Field

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


def _require_calendar(calendar: str | None) -> str:
    """Return the calendar name; raise a PCD-style error if omitted."""
    if calendar:
        return calendar
    names = _store().calendar_names()
    raise ValueError(
        "calendar is required; call `list_calendars` to discover the "
        "calendars on this account (available: "
        f"{', '.join(names) if names else '(none — create one first)'})"
    )


def _require_known_calendar(calendar: str | None) -> str:
    """Return a calendar name that exists on the account, or raise.

    Like `_require_calendar` but also rejects unknown names — writes
    must fail loudly before any `.ics` mutation rather than land on
    the wrong calendar.
    """
    names = _store().calendar_names()
    available = ", ".join(names) if names else "(none — create one first)"
    if not calendar:
        raise ValueError(
            "calendar is required; call `list_calendars` to discover the "
            f"calendars on this account (available: {available})"
        )
    if calendar not in names:
        raise ValueError(
            f"calendar {calendar!r} not found; call `list_calendars` to "
            f"discover the calendars on this account (available: {available})"
        )
    return calendar


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


def _humanize_timedelta(td: timedelta) -> str:
    """Render a `timedelta` as a human-readable English phrase.

    The self-teaching response builds the message from the actual
    defaulted duration the .ics layer applied, never from a hard-coded
    phrase, so the two cannot drift if the default changes.
    """
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts) if parts else "0 seconds"


def _end_default_message(start_value, end: str | None) -> str | None:
    """Self-teaching helper: returns the disclosure message naming the
    default duration `ical.default_dtend` will apply when `end` is
    omitted, or `None` when `end` was given.
    """
    if end is not None:
        return None
    if start_value is None:
        return None
    defaulted_end = ical.default_dtend(start_value)
    duration = defaulted_end - start_value
    is_all_day = type(start_value) is not datetime
    suffix = " (all-day)" if is_all_day else ""
    return f"no `end` given; defaulted to {_humanize_timedelta(duration)} after `start`{suffix}"


# Module-level zone + default-duration strings baked into parameter
# descriptions at import time. `CAL_DEFAULT_TZ` is read here, not via
# `Config.from_env`, so the server can still start before CalDAV is wired.
_ZONE = os.environ.get("CAL_DEFAULT_TZ", "Pacific/Auckland").strip() or "Pacific/Auckland"
_TIMED_DEFAULT, _ALL_DAY_DEFAULT = ical.default_durations()
_TIMED_DEFAULT_PHRASE = _humanize_timedelta(_TIMED_DEFAULT)
_ALL_DAY_DEFAULT_PHRASE = _humanize_timedelta(_ALL_DAY_DEFAULT)

_START_DESC = (
    f"ISO 8601 datetime. A bare local time is interpreted as wall "
    f"time in the configured zone (`{_ZONE}`); an offset-qualified "
    f"time is honoured and stored in that zone. Use "
    f"`resolve_datetime` to confirm before writing. With no `end`, "
    f"the event defaults to {_TIMED_DEFAULT_PHRASE} after this "
    f"`start` ({_ALL_DAY_DEFAULT_PHRASE} for all-day)."
)
_END_DESC = (
    f"ISO 8601 datetime. Omit for the default duration: "
    f"{_TIMED_DEFAULT_PHRASE} after `start` for timed events, "
    f"{_ALL_DAY_DEFAULT_PHRASE} after `start` for all-day. Must be "
    f"after `start`."
)
_UPDATE_START_DESC = (
    f"ISO 8601 datetime. A bare local time is interpreted as wall "
    f"time in the configured zone (`{_ZONE}`); an offset-qualified "
    f"time is honoured and stored in that zone. Use "
    f"`resolve_datetime` to confirm before writing. If `end` is "
    f"omitted, the existing duration is kept."
)
_MOVE_NEW_START_DESC = (
    f"ISO 8601 datetime. A bare local time is interpreted as wall "
    f"time in the configured zone (`{_ZONE}`); an offset-qualified "
    f"time is honoured and stored in that zone. Use "
    f"`resolve_datetime` to confirm before writing."
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
    cal_name = _require_calendar(calendar)
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
        # Derive `recurring` from the source master VEVENT, not the
        # expanded occurrence. `recurring_ical_events` adds a RECURRENCE-ID
        # to every expansion (including one-off events) — see
        # `ical.occurrence_dict` for why. The master is the
        # VEVENT without a RECURRENCE-ID; it has RRULE iff the source is
        # a series.
        master_ev = ical.master(cal)
        is_recurring = "RRULE" in master_ev
        expanded = list(recurring_ical_events.of(cal).between(lo_dt, hi_dt))
        done_map = ical.done_at_for_occurrences(cal, expanded)
        for i, occ in enumerate(expanded):
            occs.append(ical.occurrence_dict(
                occ, recurring=is_recurring, done_at=done_map.get(i),
            ))
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
    start: Annotated[str, Field(description=_START_DESC)],
    end: Annotated[str | None, Field(description=_END_DESC)] = None,
    calendar: str | None = None,
    description: str | None = None,
    location: str | None = None,
    rrule: str | None = None,
) -> dict:
    """Create an event (single, or recurring if `rrule` is given).

    `start`/`end` are ISO 8601 (see parameter docs for zone + default-duration
    details — read those before writing). `rrule` is a raw RRULE body,
    e.g. "FREQ=WEEKLY;COUNT=12". `calendar` is required in practice — there
    is no default calendar. Pick deliberately; events are not validated
    against calendar type.
    """
    cal_name = _require_known_calendar(calendar)
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
    elif (default_msg := _end_default_message(rs.value, end)) is not None:
        notes.append(default_msg)

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
    start: Annotated[str | None, Field(description=_UPDATE_START_DESC)] = None,
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
    cal_name = _require_calendar(calendar)
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
    cal_name = _require_calendar(calendar)
    _store().delete_event(cal_name, uid)
    return {"ok": True, "deleted": uid, "calendar": cal_name}


@mcp.tool()
def mark_done(
    uid: str,
    calendar: str | None = None,
    occurrence: str | None = None,
    done: bool = True,
) -> dict:
    """Tag an event (or one occurrence) done via a custom X- property.

    Without `occurrence`: marks the whole series done (X- on master). With
    `occurrence`: marks that one instance done (X- on a RECURRENCE-ID override).
    `done=False` removes the marker — if the override existed only to carry the
    marker, it is deleted entirely. `occurrence` is the instance's current start
    exactly as returned by `list_events` (UTC offset included). Bare local times
    may not match. CalDAV `If-Match` rejects concurrent edits; a conflict
    surfaces as a retryable error rather than a silent clobber.
    """
    cal_name = _require_calendar(calendar)
    store = _store()
    event = store.fetch_event(cal_name, uid)
    cal = ical.parse(event.data)
    master_ev = ical.master(cal)
    now = _now()

    if occurrence is None:
        if done:
            ical.mark_done(master_ev, now)
        else:
            ical.clear_done(master_ev)
        ical.touch(master_ev, now)
    else:
        occ = _resolve_dt(occurrence).value
        existing = ical.override_for_occurrence(cal, occ)
        if existing is not None:
            if done:
                ical.mark_done(existing, now)
            elif ical.override_is_done_only(existing, master_ev):
                cal.subcomponents.remove(existing)
            else:
                ical.clear_done(existing)
            ical.touch(master_ev, now)
        elif done:
            override = ical.build_done_override(
                master_ev=master_ev, occurrence=occ, done_at=now,
            )
            cal.add_component(override)
            ical.touch(master_ev, now)

    try:
        store.write_back(event, ical.serialize(cal))
    except Exception as exc:
        msg = str(exc).lower()
        if "412" in msg or "precondition" in msg:
            raise ValueError(
                "event was modified concurrently; re-fetch and retry"
            ) from exc
        raise

    # Compute the response's done_at from the post-write calendar state:
    # master when no occurrence, the matching override (if any) when occurrence.
    if occurrence is None:
        final = ical.done_at(ical.master(cal))
    else:
        occ = _resolve_dt(occurrence).value
        ov = ical.override_for_occurrence(cal, occ)
        final = ical.done_at(ov) if ov is not None else None
    return {
        "ok": True,
        "uid": uid,
        "calendar": cal_name,
        "occurrence": occurrence,
        "done": done,
        "done_at": final.isoformat() if final is not None else None,
    }


@mcp.tool()
def exclude_occurrence(uid: str, occurrence: str, calendar: str | None = None) -> dict:
    """Drop a single occurrence of a recurring series (EXDATE).

    `occurrence` is the instance's current start exactly as returned by
    `list_events`, including the UTC offset (e.g. `2026-06-18T09:00:00+12:00`).
    Bare local times may not match. The response includes `series_remaining`
    (occurrences left in the series) and `overrides` (RECURRENCE-ID overrides
    on the series) so the rest-of-series-unchanged claim is observable.
    """
    cal_name = _require_calendar(calendar)
    store = _store()
    event = store.fetch_event(cal_name, uid)
    cal = ical.parse(event.data)
    occ = _resolve_dt(occurrence).value
    ical.add_exdate(cal, occ, _now())
    series_remaining, overrides = ical.count_series(cal)
    store.write_back(event, ical.serialize(cal))
    return {
        "ok": True,
        "uid": uid,
        "excluded": occ.isoformat(),
        "series_remaining": series_remaining,
        "overrides": overrides,
    }


@mcp.tool()
def move_occurrence(
    uid: str,
    occurrence: str,
    new_start: Annotated[str, Field(description=_MOVE_NEW_START_DESC)],
    new_end: str | None = None,
    calendar: str | None = None,
) -> dict:
    """Reschedule a single occurrence of a series (RECURRENCE-ID override).

    `occurrence` is the instance's current start exactly as returned by
    `list_events`, including the UTC offset (e.g. `2026-06-18T09:00:00+12:00`).
    Bare local times may not match. `new_start`/`new_end` are where it moves to.
    Omit `new_end` to keep the occurrence's existing duration. The rest of the
    series is unchanged. The response includes `series_remaining` (occurrences
    left in the series) and `overrides` (RECURRENCE-ID overrides on the series)
    so the rest-of-series-unchanged claim is observable.
    """
    cal_name = _require_calendar(calendar)
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
    series_remaining, overrides = ical.count_series(cal)
    store.write_back(event, ical.serialize(cal))
    return {
        "ok": True,
        "uid": uid,
        "moved_from": occ.isoformat(),
        "note": ns.note,
        "series_remaining": series_remaining,
        "overrides": overrides,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
