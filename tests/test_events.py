"""Event construction + single-occurrence ops (EXDATE / RECURRENCE-ID)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cal_scheduler import ical

NZ = ZoneInfo("Pacific/Auckland")
NOW = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))


def _master(**kw):
    """Build a calendar with a single recurring master VEVENT."""
    recur = ical.validate_and_normalize_rrule(
        ical.parse_rrule(kw.pop("rrule", "FREQ=WEEKLY;COUNT=10")),
        kw["dtstart"],
        NZ,
    )
    ev = ical.build_event(
        uid="evt-1@cal-scheduler",
        summary="Standup",
        dtend=None,
        now=NOW,
        recur=recur,
        **kw,
    )
    return ical.event_calendar(ev)


def test_timed_event_defaults_to_one_hour():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=None, now=NOW,
    )
    assert ev.decoded("dtend") - ev.decoded("dtstart") == timedelta(hours=1)


def test_all_day_event_defaults_to_one_day():
    ev = ical.build_event(
        uid="u", summary="s", dtstart=date(2026, 6, 30), dtend=None, now=NOW,
    )
    assert ev.decoded("dtend") - ev.decoded("dtstart") == timedelta(days=1)


def test_nonpositive_timed_interval_rejected():
    start = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    with pytest.raises(ical.ValidationError, match="DTEND must be after"):
        ical.build_event(uid="u", summary="s", dtstart=start, dtend=start, now=NOW)


def test_uid_is_namespaced_to_cal_scheduler():
    assert ical.new_uid().endswith("@cal-scheduler")


def test_prodid_is_cal_scheduler():
    cal = ical.new_calendar()
    assert "cal-scheduler" in str(cal.get("prodid"))


def test_exclude_occurrence_appends_exdate():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    # second occurrence, one week later
    ical.add_exdate(cal, dtstart + timedelta(weeks=1), NOW)
    ev = ical.master(cal)
    assert "EXDATE" in ev


def test_exclude_occurrence_type_mismatch_rejected():
    # all-day series, but a timed occurrence is given
    cal = _master(dtstart=date(2026, 6, 30), rrule="FREQ=DAILY;COUNT=5")
    with pytest.raises(ical.ValidationError, match="value type"):
        ical.add_exdate(cal, datetime(2026, 7, 1, 21, 0, tzinfo=NZ), NOW)


def test_move_occurrence_adds_recurrence_id_override():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ, new_start=occ + timedelta(hours=2), new_end=None, now=NOW,
    )
    overrides = [c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1
    # master + override share the UID
    assert {str(c["uid"]) for c in ical.vevents(cal)} == {"evt-1@cal-scheduler"}


# ── create_event response (self-teaching default) ──────────────────────────
#
# The disclosure's *value* is derived from the live `ical.default_dtend` —
# never a hard-coded phrase. If the default changes, the message changes
# automatically; the test stays green without anyone touching the message
# template. Issue #7.


def test_humanize_timedelta():
    from cal_scheduler.server import _humanize_timedelta
    assert _humanize_timedelta(timedelta(hours=1)) == "1 hour"
    assert _humanize_timedelta(timedelta(hours=2)) == "2 hours"
    assert _humanize_timedelta(timedelta(minutes=15)) == "15 minutes"
    assert _humanize_timedelta(timedelta(days=1)) == "1 day"
    assert _humanize_timedelta(timedelta(days=2)) == "2 days"
    assert _humanize_timedelta(timedelta(days=1, hours=2)) == "1 day 2 hours"
    assert _humanize_timedelta(timedelta(seconds=0)) == "0 seconds"


def test_end_default_message_for_timed_names_live_default():
    """The message must name the duration the live default produces —
    derived from `ical.default_dtend`, not a hard-coded '1 hour' phrase."""
    from cal_scheduler import ical
    from cal_scheduler.server import _end_default_message, _humanize_timedelta

    start = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    end = ical.default_dtend(start)
    msg = _end_default_message(start, None)
    assert _humanize_timedelta(end - start) in msg


def test_end_default_message_for_all_day_names_live_default():
    from cal_scheduler import ical
    from cal_scheduler.server import _end_default_message, _humanize_timedelta

    start = date(2026, 6, 30)
    end = ical.default_dtend(start)
    msg = _end_default_message(start, None)
    assert _humanize_timedelta(end - start) in msg
    assert "all-day" in msg


def test_end_default_message_tracks_a_changed_default_for_timed(monkeypatch):
    """Anti-drift: if `default_dtend` returns 15 minutes instead of 1 hour,
    the message must say '15 minutes' automatically — proving the message
    is built from the value, not from a hard-coded phrase.
    """
    from cal_scheduler import ical
    from cal_scheduler.server import _end_default_message

    monkeypatch.setattr(
        ical, "default_dtend",
        lambda dt: dt + timedelta(minutes=15),
    )
    msg = _end_default_message(datetime(2026, 6, 30, 21, 0, tzinfo=NZ), None)
    assert "15 minutes" in msg
    assert "1 hour" not in msg


def test_end_default_message_tracks_a_changed_default_for_all_day(monkeypatch):
    from cal_scheduler import ical
    from cal_scheduler.server import _end_default_message

    monkeypatch.setattr(
        ical, "default_dtend",
        lambda dt: dt + timedelta(days=2),
    )
    msg = _end_default_message(date(2026, 6, 30), None)
    assert "2 days" in msg
    assert "1 day" not in msg


def test_end_default_message_none_when_end_given():
    from cal_scheduler.server import _end_default_message
    assert _end_default_message(
        datetime(2026, 6, 30, 21, 0, tzinfo=NZ), "2026-06-30T22:00"
    ) is None


def test_create_event_response_discloses_default_for_timed(monkeypatch):
    """Self-teaching response: when the agent omits `end`, the tool's
    response must include the default-end message so the agent can
    learn from the call. The message names the *live* default — the
    same value the server applies — not a hard-coded phrase.
    Exercises the full path (helper + integration), not just the helper."""
    from unittest.mock import MagicMock
    from cal_scheduler import ical, server
    from cal_scheduler.server import _humanize_timedelta

    # _resolve_dt -> _zone -> _config reads the env. Set a placeholder;
    # we never actually connect (the Store is mocked).
    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")

    fake_store = MagicMock()
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    # `calendar="personal"` exercises the explicit path.
    result = server.create_event(
        summary="test", start="2026-06-30T21:00", calendar="personal",
    )
    start = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    end = ical.default_dtend(start)
    assert _humanize_timedelta(end - start) in result["note"]


# ── `recurring` flag honesty (regression for eval §5 / issue #8) ───────────
#
# `list_events` previously returned `"recurring": true` for one-off events
# because `occurrence_dict` derived the flag from the *expanded occurrence*
# and the `recurring_ical_events` library adds a `RECURRENCE-ID` to every
# expansion, including one-offs. The flag is the contract an agent uses to
# decide single- vs series-edits; a wrong value silently breaks the edit
# path. Regression: a one-off must come back with no `recurring` key, a
# series must come back with `"recurring": true`.

def test_occurrence_dict_default_is_not_recurring():
    """Default keyword flag is False: helper does NOT emit `recurring`."""
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    out = ical.occurrence_dict(ev)
    assert "recurring" not in out


def test_occurrence_dict_recurring_true_when_flag_set():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    out = ical.occurrence_dict(ev, recurring=True)
    assert out["recurring"] is True


def test_occurrence_dict_recurring_false_when_flag_false():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    out = ical.occurrence_dict(ev, recurring=False)
    assert "recurring" not in out


def test_list_events_one_off_is_not_recurring(monkeypatch):
    """End-to-end: a one-off stored and listed comes back without a
    `recurring` key, even though `recurring_ical_events` stamps a
    RECURRENCE-ID on every expansion. This is the eval §5 bug."""
    from unittest.mock import MagicMock
    from cal_scheduler import server

    # Avoid touching the live CalDAV server / reading real env. The store
    # is fully mocked; we still need CALDAV_BASE_URL to satisfy the
    # `_config()` required-fields check, and CAL_DEFAULT_TZ for `_zone()`.
    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 7, 15, 10, 0, tzinfo=NZ)
    ev = ical.build_event(
        uid="dentist@cal-scheduler", summary="Dentist",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
    )
    cal = ical.event_calendar(ev)
    raw = ical.serialize(cal)

    fake_store = MagicMock()
    fake_store.search_raw.return_value = [raw]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    out = server.list_events(
        start="2026-07-01T00:00",
        end="2026-07-31T00:00",
        calendar="personal",
    )

    assert out["count"] == 1
    event = out["events"][0]
    assert event["uid"] == "dentist@cal-scheduler"
    assert event.get("recurring", False) is False, (
        f"one-off came back recurring=True — issue #8 regression: {event!r}"
    )


def test_list_events_recurring_series_is_recurring(monkeypatch):
    """End-to-end: a series comes back with `recurring=True`."""
    from unittest.mock import MagicMock
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 7, 1, 9, 0, tzinfo=NZ)
    recur = ical.validate_and_normalize_rrule(
        ical.parse_rrule("FREQ=WEEKLY;COUNT=4"), dtstart, NZ,
    )
    ev = ical.build_event(
        uid="standup@cal-scheduler", summary="Standup",
        dtstart=dtstart, dtend=dtstart + timedelta(minutes=30), now=NOW,
        recur=recur,
    )
    cal = ical.event_calendar(ev)
    raw = ical.serialize(cal)

    fake_store = MagicMock()
    fake_store.search_raw.return_value = [raw]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    out = server.list_events(
        start="2026-07-01T00:00",
        end="2026-07-31T00:00",
        calendar="personal",
    )

    assert out["count"] == 4
    for event in out["events"]:
        assert event.get("recurring") is True, (
            f"series occurrence came back without recurring=True: {event!r}"
        )


# ── `calendar` parameter is required (PCD contract) ───────────────────────
#
# Every event tool's `calendar` is required; omitting it is a hard error
# whose response names `list_calendars` (caller-actionable, points at
# the discovery tool). The implicit-default-by-else-name heuristic is
# gone — even a single-calendar account must be explicit. The error
# fires *before* any store call: the MCP does not perform discovery on
# the agent's behalf (PCD contract — the harness owns *how* to apply
# the hint; the MCP owns the hint itself).


def test_require_calendar_returns_calendar_when_given():
    """Pure unit: a non-empty `calendar` is returned unchanged. No
    store call — the contract is "if you said it, we use it"."""
    from cal_scheduler import server

    assert server._require_calendar("personal") == "personal"
    assert server._require_calendar("work") == "work"


def test_require_calendar_rejects_none():
    """Pure unit: `None` raises a PCD-style error that names
    `list_calendars` — no store call, no implicit fallback."""
    from cal_scheduler import server

    with pytest.raises(ValueError, match="list_calendars"):
        server._require_calendar(None)


def test_require_calendar_rejects_empty_string():
    """Pure unit: empty string is treated the same as `None` — the
    contract is "you must say which calendar, and say it with a name",
    not "you must provide a parameter that is technically non-None"."""
    from cal_scheduler import server

    with pytest.raises(ValueError, match="list_calendars"):
        server._require_calendar("")


def test_require_calendar_does_not_consult_store(monkeypatch):
    """Pure unit: the omitted-calendar error fires before any store
    call. The MCP names the discovery tool; it does not perform
    discovery on the agent's behalf (PCD contract)."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError):
        server._require_calendar(None)

    fake_store.calendar_names.assert_not_called()
    fake_store.fetch_event.assert_not_called()
    fake_store.save_new_event.assert_not_called()
    fake_store.delete_event.assert_not_called()


def test_require_calendar_does_not_silently_fall_back_to_single_calendar(monkeypatch):
    """Single-calendar invariant: the contract is not "use the only
    calendar on the account when one is given"; it is "the caller
    must name the calendar". A single-calendar account still has to
    say so explicitly. (The pure-unit version — no store call —
    proves the *fallback* path is gone, not just hidden behind a flag.)"""
    from cal_scheduler import server

    with pytest.raises(ValueError, match="list_calendars"):
        server._require_calendar(None)


def test_create_event_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`create_event` with no `calendar` raises *before* the store is
    touched. The PCD error is the agent's only signal — there is no
    half-written event, no half-opened connection."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.create_event(summary="x", start="2026-06-30T21:00")

    fake_store.save_new_event.assert_not_called()
    fake_store.calendar_names.assert_not_called()


def test_list_events_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`list_events` with no `calendar` raises before any store call.
    Reads had a friendly fallback in the old design (per PR #24) — the
    PCD contract is now uniform across reads and writes."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.list_events(
            start="2026-06-30T00:00", end="2026-07-01T00:00",
        )

    fake_store.search_raw.assert_not_called()


def test_update_event_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`update_event` with no `calendar` raises before the store is
    touched. A misrouted update would silently change the wrong
    calendar — the PCD error is the only thing that prevents it."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.update_event(uid="x@cal-scheduler", summary="new")

    fake_store.fetch_event.assert_not_called()
    fake_store.write_back.assert_not_called()


def test_delete_event_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`delete_event` with no `calendar` raises before the store is
    touched. Irreversible writes get the strictest possible guard."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.delete_event(uid="x@cal-scheduler")

    fake_store.delete_event.assert_not_called()


def test_exclude_occurrence_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`exclude_occurrence` with no `calendar` raises before the store
    is touched. A misrouted EXDATE would silently edit the wrong
    series — the PCD error is the only thing that prevents it."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.exclude_occurrence(
            uid="x@cal-scheduler", occurrence="2026-07-07T09:00:00+12:00",
        )

    fake_store.fetch_event.assert_not_called()
    fake_store.write_back.assert_not_called()


def test_move_occurrence_rejects_null_calendar_before_any_store_call(monkeypatch):
    """`move_occurrence` with no `calendar` raises before the store is
    touched. A misrouted RECURRENCE-ID override would silently edit
    the wrong series — the PCD error is the only thing that prevents it."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.move_occurrence(
            uid="x@cal-scheduler",
            occurrence="2026-07-07T09:00:00+12:00",
            new_start="2026-07-08T10:00:00+12:00",
        )

    fake_store.fetch_event.assert_not_called()
    fake_store.write_back.assert_not_called()
