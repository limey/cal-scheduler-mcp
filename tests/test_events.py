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


# ── count_series ─────────────────────────────────────────────────────────────


def test_count_series_counts_master_only():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    assert ical.count_series(cal) == (10, 0)


def test_count_series_move_keeps_count_and_adds_override():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ, new_start=occ + timedelta(hours=2), new_end=None, now=NOW,
    )
    # the override is its own instance — total stays 10
    assert ical.count_series(cal) == (10, 1)


def test_count_series_exclude_drops_one_instance():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    ical.add_exdate(cal, dtstart + timedelta(weeks=1), NOW)
    assert ical.count_series(cal) == (9, 0)


def test_count_series_no_rrule_returns_one():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    cal = ical.event_calendar(ev)
    assert ical.count_series(cal) == (1, 0)


def test_count_series_all_day_finite():
    cal = _master(dtstart=date(2026, 6, 30), rrule="FREQ=DAILY;COUNT=5")
    assert ical.count_series(cal) == (5, 0)


def test_count_series_infinite_returns_bounded_horizon_count():
    # No COUNT, no UNTIL — the function must still return a finite number.
    # 730-day horizon = ~104 weeks; exact value is library-defined, so check
    # a range that proves the rule was expanded and not just the master.
    cal = _master(
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        rrule="FREQ=WEEKLY",
    )
    instances, overrides = ical.count_series(cal)
    assert 100 < instances < 110
    assert overrides == 0


# ── move/exclude echo series state in the response payload ──────────────


def test_move_occurrence_response_includes_series_remaining_and_overrides(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.move_occurrence(
        uid="evt-1@cal-scheduler",
        occurrence=(dtstart + timedelta(weeks=1)).isoformat(),
        new_start=(dtstart + timedelta(weeks=1, hours=2)).isoformat(),
        calendar="personal",
    )

    assert result["ok"] is True
    assert result["overrides"] == 1
    assert result["series_remaining"] == 10
    # existing fields preserved
    assert "moved_from" in result
    assert "note" in result


def test_move_occurrence_response_with_prior_override(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    # first move: instance 2 (one week after DTSTART)
    occ1 = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ1, new_start=occ1 + timedelta(hours=2), new_end=None, now=NOW,
    )
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.move_occurrence(
        uid="evt-1@cal-scheduler",
        occurrence=(dtstart + timedelta(weeks=2)).isoformat(),
        new_start=(dtstart + timedelta(weeks=2, hours=2)).isoformat(),
        calendar="personal",
    )

    assert result["overrides"] == 2
    assert result["series_remaining"] == 10


def test_exclude_occurrence_response_includes_series_remaining_and_overrides(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    # one prior override
    occ1 = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ1, new_start=occ1 + timedelta(hours=2), new_end=None, now=NOW,
    )
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.exclude_occurrence(
        uid="evt-1@cal-scheduler",
        occurrence=(dtstart + timedelta(weeks=4)).isoformat(),
        calendar="personal",
    )

    assert result["ok"] is True
    assert result["overrides"] == 1  # exclude doesn't add an override
    assert result["series_remaining"] == 9  # 10 - 1
    assert "excluded" in result


def test_exclude_occurrence_response_no_prior_overrides(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.exclude_occurrence(
        uid="evt-1@cal-scheduler",
        occurrence=(dtstart + timedelta(weeks=1)).isoformat(),
        calendar="personal",
    )

    assert result["overrides"] == 0
    assert result["series_remaining"] == 9


# ── create_event response (self-teaching default) ──────────────────────────


def test_humanize_timedelta():
    from cal_scheduler.server import _humanize_timedelta
    assert _humanize_timedelta(timedelta(hours=1)) == "1 hour"
    assert _humanize_timedelta(timedelta(hours=2)) == "2 hours"
    assert _humanize_timedelta(timedelta(minutes=15)) == "15 minutes"
    assert _humanize_timedelta(timedelta(days=1)) == "1 day"
    assert _humanize_timedelta(timedelta(days=2)) == "2 days"
    assert _humanize_timedelta(timedelta(days=1, hours=2)) == "1 day 2 hours"
    assert _humanize_timedelta(timedelta(seconds=0)) == "0 seconds"



def test_create_event_response_discloses_default_for_timed(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import ical, server
    from cal_scheduler.server import _humanize_timedelta

    # _resolve_dt -> _zone -> _config reads the env. Set a placeholder;
    # we never actually connect (the Store is mocked).
    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")

    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    # `calendar="personal"` exercises the explicit path.
    result = server.create_event(
        summary="test", start="2026-06-30T21:00", calendar="personal",
    )
    start = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    end = ical.default_dtend(start)
    assert _humanize_timedelta(end - start) in result["note"]


# ── `recurring` flag honesty ─────────────────────────────────────────────────

def test_occurrence_dict_default_is_not_recurring():
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
        f"one-off came back recurring=True: {event!r}"
    )


def test_list_events_recurring_series_is_recurring(monkeypatch):
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


# ── `calendar` parameter is required ─────────────────────────────────────────


def test_calendar_omitted_raises_with_list_calendars_hint(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal", "work"]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.create_event(summary="x", start="2026-06-30T21:00")

    # The discovery tool was actually called — the error's "available"
    # list comes from a live enumeration, not a hard-coded name.
    fake_store.calendar_names.assert_called_once()


def test_calendar_required_even_with_single_calendar(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="list_calendars"):
        server.create_event(summary="x", start="2026-06-30T21:00")


def test_calendar_required_when_account_has_none(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = []
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError) as excinfo:
        server.create_event(summary="x", start="2026-06-30T21:00")
    assert "list_calendars" in str(excinfo.value)
    assert "create one first" in str(excinfo.value)


# ── write-side calendar disambiguation ─────────────────────────────────────


def test_create_event_unknown_calendar_raises_with_inline_valid_values(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal", "work"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError) as excinfo:
        server.create_event(
            summary="dentist", start="2026-06-30T21:00", calendar="Vacation",
        )

    msg = str(excinfo.value)
    # Same field names + same ordering as the list_events omit error.
    assert "Vacation" in msg, f"error must name the rejected calendar: {msg!r}"
    assert "list_calendars" in msg, f"error must hint at list_calendars: {msg!r}"
    assert "available:" in msg, f"error must include available: clause: {msg!r}"
    assert "personal" in msg and "work" in msg, (
        f"error must name the valid calendars inline: {msg!r}"
    )
    # The bad name comes first, the fix (list_calendars) follows — same
    # shape as list_events, plus the unknown-name prefix.
    assert msg.index("Vacation") < msg.index("list_calendars") < msg.index("available:")

    # Pre-validation, not post-hoc: no .ics was written.
    fake_store.save_new_event.assert_not_called()
    # The discovery tool was actually called — the error's "available"
    # list comes from a live enumeration, not a hard-coded name.
    fake_store.calendar_names.assert_called_once()


def test_create_event_unknown_calendar_error_matches_list_events_omit_shape(monkeypatch):
    import re
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal", "work"]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    # Omit case
    with pytest.raises(ValueError) as omit_exc:
        server.create_event(summary="x", start="2026-06-30T21:00")
    omit_msg = str(omit_exc.value)

    # Unknown case
    with pytest.raises(ValueError) as unknown_exc:
        server.create_event(
            summary="x", start="2026-06-30T21:00", calendar="Vacation",
        )
    unknown_msg = str(unknown_exc.value)

    # Strip the leading prefix (varies: "calendar is required;" vs.
    # "calendar 'Vacation' not found;") — what follows must be identical.
    omit_suffix = re.sub(r"^calendar[^;]+;", "", omit_msg)
    unknown_suffix = re.sub(r"^calendar[^;]+;", "", unknown_msg)
    assert omit_suffix == unknown_suffix, (
        f"shape mismatch: omit suffix {omit_suffix!r} != unknown suffix {unknown_suffix!r}"
    )


def test_create_event_known_calendar_succeeds(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal", "work"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="dentist", start="2026-06-30T21:00", calendar="personal",
    )

    assert result["ok"] is True
    assert result["calendar"] == "personal"
    fake_store.save_new_event.assert_called_once()
    # The discovery call happened (guard pre-validates) but the call
    # itself was successful.
    fake_store.calendar_names.assert_called_once()


# ── create_event resolved response fields ──────────────────────────────────


def test_create_event_start_resolved_bare_local(monkeypatch):
    """Bare local time → start_resolved carries the resolved offset."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="test", start="2026-07-16T15:00:00", calendar="personal",
    )
    assert result["start_resolved"] == "2026-07-16T15:00:00+12:00"
    assert result["end_resolved"] == "2026-07-16T16:00:00+12:00"


def test_create_event_start_resolved_offset_qualified(monkeypatch):
    """Offset-qualified start → honour and echo the resolved offset."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="test", start="2026-07-16T15:00:00+12:00", calendar="personal",
    )
    assert result["start_resolved"] == "2026-07-16T15:00:00+12:00"
    assert result["end_resolved"] == "2026-07-16T16:00:00+12:00"


def test_create_event_all_day_resolved(monkeypatch):
    """All-day event → start_resolved and end_resolved are dates (no offset)."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Holiday", start="2026-12-25", calendar="personal",
    )
    assert result["start_resolved"] == "2026-12-25"
    assert result["end_resolved"] == "2026-12-26"


def test_create_event_recurring_has_next_occurrence(monkeypatch):
    """Recurring event → next_occurrence is the first instance (DTSTART)."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Standup",
        start="2026-07-16T09:00:00",
        calendar="personal",
        rrule="FREQ=WEEKLY;COUNT=4",
    )
    assert result["next_occurrence"] == "2026-07-16T09:00:00+12:00"
    # non-recurring events do NOT get next_occurrence
    assert "start_resolved" in result
    assert "end_resolved" in result


def test_create_event_single_no_next_occurrence(monkeypatch):
    """Single (non-recurring) event → no next_occurrence field."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Dentist", start="2026-07-16T09:00:00", calendar="personal",
    )
    assert "next_occurrence" not in result


def test_create_event_explicit_end_resolved(monkeypatch):
    """Explicit end is resolved and echoed in end_resolved."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Meeting",
        start="2026-07-16T15:00:00",
        end="2026-07-16T16:30:00",
        calendar="personal",
    )
    assert result["start_resolved"] == "2026-07-16T15:00:00+12:00"
    assert result["end_resolved"] == "2026-07-16T16:30:00+12:00"


def test_create_event_note_shows_resolution_chain(monkeypatch):
    """The note field shows the resolution chain for a bare local start."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="test", start="2026-07-16T15:00:00", calendar="personal",
    )
    note = result["note"]
    assert "resolved 2026-07-16T15:00:00 →" in note
    assert "Pacific/Auckland" in note
    assert "end defaulted to 1 hour →" in note
    assert "2026-07-16T16:00:00+12:00" in note


def test_create_event_note_all_day(monkeypatch):
    """All-day note shows (all-day) and default duration."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Holiday", start="2026-12-25", calendar="personal",
    )
    note = result["note"]
    assert "(all-day)" in note
    assert "end defaulted to 1 day →" in note
    assert "2026-12-26" in note


def test_create_event_note_explicit_end(monkeypatch):
    """Note shows end resolution when end is explicitly provided."""
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.save_new_event.return_value = None
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.create_event(
        summary="Meeting",
        start="2026-07-16T15:00:00",
        end="2026-07-16T16:30:00",
        calendar="personal",
    )
    note = result["note"]
    assert "end resolved 2026-07-16T16:30:00 →" in note


# ── mark_done server tests ───────────────────────────────────────────────────


def test_mark_done_series_response_includes_done_and_scope(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.mark_done(
        uid="evt-1@cal-scheduler",
        calendar="personal",
    )

    assert result["ok"] is True
    assert result["done"] is True
    assert "done_at" in result
    assert result["scope"] == "series"
    assert "occurrence" not in result
    assert result["series_remaining"] == 10
    assert result["overrides"] == 0
    fake_store.write_back.assert_called_once()


def test_mark_done_occurrence_response_includes_done_and_scope(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    occ = (dtstart + timedelta(weeks=1)).isoformat()
    result = server.mark_done(
        uid="evt-1@cal-scheduler",
        occurrence=occ,
        calendar="personal",
    )

    assert result["ok"] is True
    assert result["done"] is True
    assert "done_at" in result
    assert result["scope"] == "occurrence"
    assert "occurrence" in result
    assert result["series_remaining"] == 10
    assert result["overrides"] == 1
    fake_store.write_back.assert_called_once()


def test_mark_done_occurrence_with_prior_override(monkeypatch):
    from unittest.mock import MagicMock

    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    # a prior move_occurrence on a different instance
    occ_prior = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ_prior,
        new_start=occ_prior + timedelta(hours=2), new_end=None, now=NOW,
    )
    raw = ical.serialize(cal)

    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.fetch_event.return_value = fake_event
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    occ = (dtstart + timedelta(weeks=3)).isoformat()
    result = server.mark_done(
        uid="evt-1@cal-scheduler",
        occurrence=occ,
        calendar="personal",
    )

    assert result["ok"] is True
    assert result["scope"] == "occurrence"
    assert result["overrides"] == 2  # 1 prior move + 1 new done
    assert result["series_remaining"] == 10


# ── mark_done helpers ────────────────────────────────────────────────────────


def test_mark_event_done_stamps_master():
    """Marking a single event adds DONE_PROPERTY on the master."""
    ev = ical.build_event(
        uid="evt-1@cal-scheduler", summary="Standup",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=None, now=NOW,
    )
    cal = ical.event_calendar(ev)
    ical.mark_event_done(cal, NOW)
    master_ev = ical.master(cal)
    assert ical.DONE_PROPERTY in master_ev


def test_mark_event_done_idempotent():
    """Re-marking replaces the prior done_at timestamp."""
    ev = ical.build_event(
        uid="evt-1@cal-scheduler", summary="Standup",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=None, now=NOW,
    )
    cal = ical.event_calendar(ev)
    later = NOW + timedelta(hours=2)
    ical.mark_event_done(cal, NOW)
    ical.mark_event_done(cal, later)
    master_ev = ical.master(cal)
    assert ical.DONE_PROPERTY in master_ev
    done_val = master_ev.decoded(ical.DONE_PROPERTY)
    if isinstance(done_val, datetime):
        assert done_val.replace(tzinfo=None) == later.replace(tzinfo=None)


def test_mark_event_done_all_day():
    """Marking an all-day event works (date-based, not datetime)."""
    ev = ical.build_event(
        uid="evt-1@cal-scheduler", summary="Holiday",
        dtstart=date(2026, 12, 25), dtend=None, now=NOW,
    )
    cal = ical.event_calendar(ev)
    ical.mark_event_done(cal, NOW)
    master_ev = ical.master(cal)
    assert ical.DONE_PROPERTY in master_ev


def test_add_done_override_creates_override_for_one_occurrence():
    """Marking one occurrence creates a RECURRENCE-ID override with DONE_PROPERTY."""
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_done_override(cal, occurrence=occ, now=NOW)
    overrides = [c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1
    assert ical.DONE_PROPERTY in overrides[0]
    assert ical.DONE_PROPERTY not in ical.master(cal)


def test_add_done_override_idempotent():
    """Re-marking same occurrence replaces timestamp; no duplicate overrides."""
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    later = NOW + timedelta(hours=3)
    ical.add_done_override(cal, occurrence=occ, now=NOW)
    ical.add_done_override(cal, occurrence=occ, now=later)
    overrides = [c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1
    assert ical.DONE_PROPERTY in overrides[0]
    done_val = overrides[0].decoded(ical.DONE_PROPERTY)
    if isinstance(done_val, datetime):
        assert done_val.replace(tzinfo=None) == later.replace(tzinfo=None)


def test_add_done_override_on_existing_move_override():
    """Mark-done on a moved occurrence stamps the existing override."""
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ,
        new_start=occ + timedelta(hours=2), new_end=None, now=NOW,
    )
    assert len([c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]) == 1
    ical.add_done_override(cal, occurrence=occ, now=NOW)
    overrides = [c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1, "should not create a second override"
    assert ical.DONE_PROPERTY in overrides[0]


def test_add_done_override_all_day():
    """Mark one occurrence of an all-day recurring series."""
    cal = _master(dtstart=date(2026, 6, 30), rrule="FREQ=DAILY;COUNT=5")
    occ = date(2026, 7, 2)
    ical.add_done_override(cal, occurrence=occ, now=NOW)
    overrides = [c for c in ical.vevents(cal) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1
    assert ical.DONE_PROPERTY in overrides[0]


def test_occurrence_dict_surfaces_done_for_master_mark():
    """list_events expansion surfaces done/done_at when master is marked."""
    import recurring_ical_events

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    ical.mark_event_done(cal, NOW)

    lo = dtstart
    hi = dtstart + timedelta(weeks=5)
    is_recurring = "RRULE" in ical.master(cal)
    for occ in recurring_ical_events.of(cal).between(lo, hi):
        d = ical.occurrence_dict(occ, recurring=is_recurring)
        assert d["done"] is True
        assert "done_at" in d


def test_occurrence_dict_surfaces_done_for_override_mark():
    """list_events surfaces done/done_at only on the marked occurrence."""
    import recurring_ical_events

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    marked_occ = dtstart + timedelta(weeks=1)
    ical.add_done_override(cal, occurrence=marked_occ, now=NOW)

    lo = dtstart
    hi = dtstart + timedelta(weeks=5)
    is_recurring = "RRULE" in ical.master(cal)
    for occ in recurring_ical_events.of(cal).between(lo, hi):
        d = ical.occurrence_dict(occ, recurring=is_recurring)
        occ_start = occ.start
        is_marked = (
            occ_start.astimezone(NZ) == marked_occ.astimezone(NZ)
            if isinstance(occ_start, datetime)
            else occ_start == marked_occ
        )
        if is_marked:
            assert d.get("done") is True, f"expected done on {occ_start}"
        else:
            assert d["done"] is False, f"unexpected done on {occ_start}"
            assert d["done_at"] is None, f"unexpected done_at on {occ_start}"


def test_occurrence_dict_done_false_when_not_marked():
    """Unmarked events have done=False, done_at=None."""
    import recurring_ical_events

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)

    lo = dtstart
    hi = dtstart + timedelta(weeks=5)
    is_recurring = "RRULE" in ical.master(cal)
    for occ in recurring_ical_events.of(cal).between(lo, hi):
        d = ical.occurrence_dict(occ, recurring=is_recurring)
        assert d["done"] is False
        assert d["done_at"] is None


def test_override_done_wins_over_master():
    """When both master and override marked, override done_at surfaces."""
    import recurring_ical_events

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    ical.mark_event_done(cal, NOW)
    marked_occ = dtstart + timedelta(weeks=1)
    override_now = NOW + timedelta(hours=5)
    ical.add_done_override(cal, occurrence=marked_occ, now=override_now)

    expected_override = override_now.astimezone(ZoneInfo("UTC")).isoformat()
    expected_master = NOW.astimezone(ZoneInfo("UTC")).isoformat()

    lo = dtstart
    hi = dtstart + timedelta(weeks=5)
    is_recurring = "RRULE" in ical.master(cal)
    for occ in recurring_ical_events.of(cal).between(lo, hi):
        d = ical.occurrence_dict(occ, recurring=is_recurring)
        is_marked = (
            occ.start.astimezone(NZ) == marked_occ.astimezone(NZ)
            if isinstance(occ.start, datetime)
            else occ.start == marked_occ
        )
        # All occurrences should have done=True (master is marked)
        assert d["done"] is True
        assert "done_at" in d
        if is_marked:
            # done_at comes from icalendar's .decoded() which may produce
            # space-separated or T-separated ISO; normalise for comparison.
            got = d["done_at"].replace(" ", "T")
            assert got == expected_override, (
                f"marked occurrence should use override stamp, "
                f"got {d['done_at']!r}, expected {expected_override!r}"
            )
        else:
            got = d["done_at"].replace(" ", "T")
            assert got == expected_master, (
                f"unmarked occurrence should use master stamp, "
                f"got {d['done_at']!r}, expected {expected_master!r}"
            )
