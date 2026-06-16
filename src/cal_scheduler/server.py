"""The MCP server: a strict, deterministic CalDAV boundary.

The LLM does English→params; this server validates, rejects loudly, and persists
zoned .ics. It is not an NLP layer. Every tool returns a small dict; on bad input
it raises with a caller-actionable message (FastMCP surfaces it as a tool error).

The only clock touched is DTSTAMP/LAST-MODIFIED on write — everything else is pure,
so the same input yields the same bytes (handy if the .ics store is kept in git).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import caldav
from mcp.server.fastmcp import FastMCP

from . import ical
from .config import Config, SCHEMA, ConfigError
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

    Used by the self-teaching disclosure on `create_event`'s default
    path (PHILOSOPHY §5). The message the agent sees is built from the
    *actual* defaulted duration the .ics layer applied — never from a
    hard-coded "1 hour" / "1 day" string — so the two cannot drift if
    the default ever changes (issue #7).
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
    """Self-teaching response helper (PHILOSOPHY §5).

    When the agent calls `create_event` with only a `start` and no
    `end`, the .ics layer applies a default duration (see
    `ical.default_dtend` — the single source of truth for both the
    persisted value and this disclosure). The tool response names that
    duration so the agent can learn from the call and remember for
    next time. Returns `None` when `end` was given — no default to
    disclose. The message is built from the value the helper produced,
    not from a hard-coded phrase, so the response and the persisted
    value can never disagree (issue #7).
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


# ── preflight (PCD runtime check) ───────────────────────────────────────────
#
# `doctor` is the runtime companion to AGENTS.md's *Configuration* spec.
# The spec lives in the doc (so a scraping agent can self-teach without
# invoking a tool); `doctor` is what the agent calls when something is
# wrong or during the install-validate round-trip. It runs a live
# preflight: URL reachability, auth-header send, principal discovery,
# calendar enumeration, and a one-shot write round-trip in a throwaway
# calendar. It never persists — apply fixes via the harness's per-server
# `env` block, restart, call `doctor` again to re-validate.


def _password_presence_hint(cfg: Config) -> str:
    """The Radicale `auth=none` gotcha, surfaced as the first blocker hint.

    Radicale under `auth=none` rejects a request when one credential is
    set without the other (it interprets a half-set pair as a malformed
    basic-auth header and returns 401 / AuthorizationError). The rule:
    set both `CALDAV_USERNAME` and `CALDAV_PASSWORD` together, or set
    neither. Anything else triggers this hint as the first thing the
    agent sees on a failed `doctor` call.
    """
    has_user = bool(cfg.username)
    has_pass = bool(cfg.password)
    if has_user and not has_pass:
        return (
            "Radicale under `auth=none` requires CALDAV_USERNAME and "
            "CALDAV_PASSWORD to be set together, or both left empty. "
            "A username is set but no password — set CALDAV_PASSWORD, "
            "or clear CALDAV_USERNAME for truly anonymous access."
        )
    if has_pass and not has_user:
        return (
            "Radicale under `auth=none` requires CALDAV_USERNAME and "
            "CALDAV_PASSWORD to be set together, or both left empty. "
            "A password is set but no username — set CALDAV_USERNAME, "
            "or clear CALDAV_PASSWORD for truly anonymous access."
        )
    return ""


def _blank_password_hint(cfg: Config) -> str:
    """A 401 may also mean the password was left unset.

    Some servers (the value is ignored) require a non-empty password
    so the client emits a Basic auth header at all. The eval hit this
    with both `CALDAV_USERNAME` and `CALDAV_PASSWORD` blank — the half-set
    hint above does not cover that case, so this is the second-pass
    correlation: when the password is unset AND auth still failed, suggest
    setting a placeholder.
    """
    if cfg.password:
        return ""
    return (
        "CALDAV_PASSWORD is unset; some servers need a non-empty value "
        "as a placeholder (the value is ignored, but it triggers the "
        "Basic auth header). Set CALDAV_PASSWORD to any non-empty string "
        "and call `doctor` again."
    )


def _display_name(cal: caldav.Calendar) -> str:
    """Best-effort display name for a calendar (matches `Store._display_name`)."""
    try:
        return str(cal.get_display_name())
    except Exception:
        return str(cal.name)


def _redacted_config(cfg: Config, calendars: list[str]) -> dict:
    """Echo the *resolved* config in the `ready` response, password-redacted.

    The field set is driven by `SCHEMA` so the test for "config echo
    matches SCHEMA" stays true when fields are added. The password is
    surfaced as `"<set>"` (truthy) or `None` (absent) — never the value.
    """
    echo: dict[str, object] = {}
    for f in SCHEMA:
        if f.name == "CALDAV_BASE_URL":
            echo[f.name] = cfg.base_url
        elif f.name == "CALDAV_USERNAME":
            echo[f.name] = cfg.username or None
        elif f.name == "CALDAV_PASSWORD":
            # Never echo the password itself; the agent only needs to
            # know "is it set" so it can ask the user if not.
            echo[f.name] = "<set>" if cfg.password else None
        elif f.name == "CAL_DEFAULT_TZ":
            echo[f.name] = cfg.default_tz
    echo["calendars"] = calendars
    return echo


def _doctor_preflight(cfg: Config) -> dict:
    """Run the full preflight against `cfg`; return the doctor response dict.

    Extracted from the tool wrapper so tests can drive it with a stub
    config and a stubbed `caldav` layer (the cached `_store()` is not
    used here — the preflight opens a fresh connection per call, which
    is the right shape for a tool the agent invokes rarely).
    """
    # 1+2: URL reachability + auth-header send. `principal()` triggers
    # the PROPFIND that carries the Authorization header, so a single
    # call exercises both steps.
    try:
        client = caldav.DAVClient(
            url=cfg.base_url,
            username=cfg.username or None,
            password=cfg.password or None,
        )
        principal = client.principal()
    except caldav.lib.error.AuthorizationError:
        hints: list[str] = []
        pw_hint = _password_presence_hint(cfg)
        if pw_hint:
            hints.append(pw_hint)
        bp_hint = _blank_password_hint(cfg)
        if bp_hint:
            hints.append(bp_hint)
        hints.append(
            f"auth failed against {cfg.base_url} — check CALDAV_USERNAME "
            "and CALDAV_PASSWORD on the server"
        )
        return {
            "status": "blockers",
            "hints": hints,
            "note": (
                "doctor preflight could not authenticate; fix the listed "
                "hints and call `doctor` again."
            ),
        }
    except Exception as exc:  # network, DNS, TLS, refused, etc.
        return {
            "status": "blockers",
            "hints": [f"could not reach {cfg.base_url}: {type(exc).__name__}: {exc}"],
            "note": "doctor preflight could not reach the CalDAV server.",
        }

    # 3: calendar enumeration (already proved the principal is reachable).
    try:
        names = sorted(_display_name(c) for c in principal.calendars())
    except Exception as exc:
        return {
            "status": "blockers",
            "hints": [f"calendar enumeration failed: {type(exc).__name__}: {exc}"],
            "note": "doctor preflight reached the server but failed to list calendars.",
        }

    # 4: one-shot write round-trip in a throwaway calendar. The name is
    # timestamped so two doctor calls in the same second don't collide,
    # and so any leftover from a previous failed run is easy to spot and
    # clean up by hand.
    throwaway = f"_doctor_{int(time.time() * 1000)}"
    try:
        principal.make_calendar(name=throwaway)
    except Exception as exc:
        return {
            "status": "blockers",
            "hints": [
                f"write round-trip failed (could not create throwaway "
                f"calendar {throwaway!r}): {type(exc).__name__}: {exc}"
            ],
            "note": (
                "doctor preflight authenticated and enumerated calendars, "
                "but the write path is blocked. The account is read-only "
                "or quota-limited."
            ),
        }
    try:
        for c in principal.calendars():
            if _display_name(c) == throwaway:
                c.delete()
                break
    except Exception as exc:
        # Cleanup failed — leave the throwaway; the preflight still
        # passed every check the user cares about. Surface as a hint
        # so the agent can clean up.
        return {
            "status": "ready",
            "config": _redacted_config(cfg, names),
            "note": (
                f"preflight passed, but cleanup of the throwaway calendar "
                f"{throwaway!r} failed ({type(exc).__name__}: {exc}); "
                "you may want to delete it by hand"
            ),
        }

    return {
        "status": "ready",
        "config": _redacted_config(cfg, names),
        "note": (
            "preflight passed; the MCP is wired and the CalDAV account "
            "is usable. For the configuration field spec (names, "
            "defaults, required-ness), read AGENTS.md *Configuration*."
        ),
    }


@mcp.tool()
def doctor() -> dict:
    """Preflight: check the MCP is wired and the CalDAV account is usable.

    Runs a live round-trip in this order: URL reachability, auth-header
    send, principal discovery, calendar enumeration, and a one-shot
    write (create + delete a throwaway calendar so the write path is
    genuinely exercised, not just auth-checked).

    On success returns `{status: "ready", config: {...}, note: "..."}`
    where `config` is the resolved configuration (password redacted) plus
    the list of calendars on the account.

    On failure returns `{status: "blockers", hints: [...], note: "..."}`
    with actionable hints. The first hint names the Radicale `auth=none`
    password-presence rule when the configured credentials trip it; the
    second is the generic auth-failure hint. Apply fixes via your
    harness's per-server `env` block, restart the MCP, and call `doctor`
    again to re-validate.

    PCD contract: this tool runs a check, it does not persist. The
    configuration field spec lives in AGENTS.md *Configuration* (read it
    once, then use this tool to verify the live wiring).
    """
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        return {
            "status": "blockers",
            "hints": [str(exc)],
            "note": (
                "doctor preflight could not read the configuration; set "
                "the required env vars and call `doctor` again."
            ),
        }
    return _doctor_preflight(cfg)


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
        # Derive `recurring` from the *source* master VEVENT, not the
        # expanded occurrence. `recurring_ical_events` adds a RECURRENCE-ID
        # to every expansion (including one-off events) — see
        # ical.occurrence_dict's docstring / issue #8. The master is the
        # VEVENT without a RECURRENCE-ID; it has RRULE iff the source is
        # a series.
        is_recurring = "RRULE" in ical.master(cal)
        for occ in recurring_ical_events.of(cal).between(lo_dt, hi_dt):
            occs.append(ical.occurrence_dict(occ, recurring=is_recurring))
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

    `start`/`end` are ISO 8601. A bare local time is interpreted as wall time in
    the calendar's configured zone (see `resolve_datetime` to confirm before
    writing); an offset-qualified time is honoured and stored in that zone. With
    no `end`, the event defaults to 1 hour (all-day if `start` is date-only).
    `rrule` is a raw RRULE body, e.g. "FREQ=WEEKLY;COUNT=12".
    """
    cal_name = _require_calendar(calendar)
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
    new_start: str,
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
