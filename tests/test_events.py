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


def test_end_default_message_for_timed_names_live_default():
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


# ── done marker: ical layer ──────────────────────────────────────────────────


def test_mark_done_stamps_x_property_with_utc_timestamp():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    ical.mark_done(ev, datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")))
    raw = ical.serialize(ical.event_calendar(ev))
    assert "X-CAL-SCHEDULER-DONE:20240107T210000Z" in raw


def test_mark_done_idempotent_no_duplicate_x_property():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ev, stamp)
    ical.mark_done(ev, stamp)
    raw = ical.serialize(ical.event_calendar(ev))
    assert raw.count("X-CAL-SCHEDULER-DONE") == 1


def test_mark_done_replaces_prior_timestamp():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    ical.mark_done(ev, datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")))
    ical.mark_done(ev, datetime(2024, 1, 8, 21, 0, tzinfo=ZoneInfo("UTC")))
    raw = ical.serialize(ical.event_calendar(ev))
    assert "20240108T210000Z" in raw
    assert "20240107T210000Z" not in raw


def test_clear_done_idempotent_on_unmarked_event():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    ical.clear_done(ev)  # no-op
    assert "X-CAL-SCHEDULER-DONE" not in ical.serialize(ical.event_calendar(ev))


def test_done_at_returns_parsed_utc_or_none():
    ev = ical.build_event(
        uid="u", summary="s",
        dtstart=datetime(2026, 6, 30, 21, 0, tzinfo=NZ),
        dtend=datetime(2026, 6, 30, 22, 0, tzinfo=NZ), now=NOW,
    )
    assert ical.done_at(ev) is None
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ev, stamp)
    parsed = ical.done_at(ev)
    assert parsed == stamp


def test_override_for_occurrence_returns_matching_override():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ, new_start=occ + timedelta(hours=2), new_end=None, now=NOW,
    )
    found = ical.override_for_occurrence(cal, occ)
    assert found is not None
    assert "RECURRENCE-ID" in found


def test_override_for_occurrence_returns_none_when_no_match():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    assert ical.override_for_occurrence(
        cal, dtstart + timedelta(weeks=1),
    ) is None


def test_override_is_done_only_true_for_minimal_override():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    override = ical.build_done_override(
        master_ev=ical.master(cal), occurrence=occ,
        done_at=datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")),
    )
    cal.add_component(override)
    assert ical.override_is_done_only(override, ical.master(cal)) is True


def test_override_is_done_only_false_when_summary_changed():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    ical.add_override(
        cal, occurrence=occ, new_start=occ, new_end=None, now=NOW,
        summary="different",
    )
    override = ical.override_for_occurrence(cal, occ)
    ical.mark_done(override, datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")))
    assert ical.override_is_done_only(override, ical.master(cal)) is False


def test_build_done_override_carries_recurrence_id_and_x():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    override = ical.build_done_override(
        master_ev=ical.master(cal), occurrence=occ, done_at=stamp,
    )
    assert "RECURRENCE-ID" in override
    assert ical.done_at(override) == stamp


def test_done_at_for_occurrences_master_only():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ical.master(cal), stamp)

    import recurring_ical_events
    expanded = list(
        recurring_ical_events.of(cal).between(
            dtstart, dtstart + timedelta(weeks=20),
        )
    )
    m = ical.done_at_for_occurrences(cal, expanded)
    assert all(v == stamp for v in m.values())


def test_done_at_for_occurrences_override_overrides_master():
    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    master_stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ical.master(cal), master_stamp)

    occ = dtstart + timedelta(weeks=1)
    override_stamp = datetime(2024, 1, 8, 21, 0, tzinfo=ZoneInfo("UTC"))
    override = ical.build_done_override(
        master_ev=ical.master(cal), occurrence=occ, done_at=override_stamp,
    )
    # override's done marker must clear the master's mark for this occ
    cal.add_component(override)

    import recurring_ical_events
    expanded = list(
        recurring_ical_events.of(cal).between(
            dtstart, dtstart + timedelta(weeks=20),
        )
    )
    m = ical.done_at_for_occurrences(cal, expanded)
    assert m[0] == master_stamp  # first occ, no override
    assert m[1] == override_stamp  # second occ, override wins
    assert m[2] == master_stamp  # third occ, no override


# ── done marker: server.mark_done tool ──────────────────────────────────────


def _fake_store_with(raw: str):
    """Build a MagicMock store that returns `raw` for fetch_event."""
    from unittest.mock import MagicMock
    fake_event = MagicMock()
    fake_event.data = raw
    fake_store = MagicMock()
    fake_store.calendar_names.return_value = ["personal"]
    fake_store.fetch_event.return_value = fake_event
    return fake_store, fake_event


def test_mark_done_one_off_stamps_x_property(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="dentist",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
    )
    raw = ical.serialize(ical.event_calendar(ev))

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.mark_done(uid="u@cal-scheduler", calendar="personal")

    assert result["ok"] is True
    assert result["done"] is True
    assert result["done_at"] is not None
    # The .ics actually written carries the X-
    written_ics = fake_store.write_back.call_args[0][1]
    assert "X-CAL-SCHEDULER-DONE:" in written_ics


def test_mark_done_whole_series_marks_all_occurrences(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    server.mark_done(uid="evt-1@cal-scheduler", calendar="personal")

    written_ics = fake_store.write_back.call_args[0][1]
    assert "X-CAL-SCHEDULER-DONE:" in written_ics
    # Re-parse: master carries the marker
    reparsed = ical.parse(written_ics)
    assert ical.done_at(ical.master(reparsed)) is not None


def test_mark_done_single_occurrence_adds_override(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    occ = dtstart + timedelta(weeks=1)
    result = server.mark_done(
        uid="evt-1@cal-scheduler", calendar="personal",
        occurrence=occ.isoformat(),
    )

    assert result["done_at"] is not None
    written_ics = fake_store.write_back.call_args[0][1]
    # Master is unmarked; the override carries the marker
    reparsed = ical.parse(written_ics)
    assert ical.done_at(ical.master(reparsed)) is None
    overrides = [c for c in ical.vevents(reparsed) if "RECURRENCE-ID" in c]
    assert len(overrides) == 1
    assert ical.done_at(overrides[0]) is not None


def test_mark_done_unmark_with_done_false_clears_master(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="x",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
    )
    ical.mark_done(ev, datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")))
    raw = ical.serialize(ical.event_calendar(ev))

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    result = server.mark_done(
        uid="u@cal-scheduler", calendar="personal", done=False,
    )

    assert result["done_at"] is None
    written_ics = fake_store.write_back.call_args[0][1]
    assert "X-CAL-SCHEDULER-DONE" not in written_ics


def test_mark_done_unmark_minimal_override_deletes_component(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    occ = dtstart + timedelta(weeks=1)
    override = ical.build_done_override(
        master_ev=ical.master(cal), occurrence=occ,
        done_at=datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC")),
    )
    cal.add_component(override)
    raw = ical.serialize(cal)

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    server.mark_done(
        uid="evt-1@cal-scheduler", calendar="personal",
        occurrence=occ.isoformat(), done=False,
    )

    written_ics = fake_store.write_back.call_args[0][1]
    reparsed = ical.parse(written_ics)
    overrides = [c for c in ical.vevents(reparsed) if "RECURRENCE-ID" in c]
    assert overrides == []  # override removed entirely


def test_mark_done_unmark_no_op_when_no_override_and_done_false(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    cal = _master(dtstart=dtstart)
    raw = ical.serialize(cal)

    fake_store, _ = _fake_store_with(raw)
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    occ = dtstart + timedelta(weeks=1)
    result = server.mark_done(
        uid="evt-1@cal-scheduler", calendar="personal",
        occurrence=occ.isoformat(), done=False,
    )

    assert result["done_at"] is None
    # write_back still happens (touch was a no-op but the function is
    # uniform — verify it ran rather than silently skipping)
    fake_store.write_back.assert_called_once()


def test_mark_done_412_surfaces_structured_retry_error(monkeypatch):
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="x",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
    )
    raw = ical.serialize(ical.event_calendar(ev))

    fake_store, _ = _fake_store_with(raw)
    fake_store.write_back.side_effect = Exception("412 Precondition Failed")
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    with pytest.raises(ValueError, match="modified concurrently"):
        server.mark_done(uid="u@cal-scheduler", calendar="personal")


# ── done marker: list_events surfaces done_at ───────────────────────────────


def test_list_events_surfaces_done_at_when_master_marked(monkeypatch):
    from unittest.mock import MagicMock
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 7, 1, 9, 0, tzinfo=NZ)
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="s",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
    )
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ev, stamp)
    raw = ical.serialize(ical.event_calendar(ev))

    fake_store = MagicMock()
    fake_store.search_raw.return_value = [raw]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    out = server.list_events(
        start="2026-07-01T00:00", end="2026-07-02T00:00", calendar="personal",
    )

    assert out["count"] == 1
    assert out["events"][0]["done_at"] == stamp.isoformat()


def test_list_events_anti_drift_done_at_matches_stored_x(monkeypatch):
    from unittest.mock import MagicMock
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 7, 1, 9, 0, tzinfo=NZ)
    recur = ical.validate_and_normalize_rrule(
        ical.parse_rrule("FREQ=WEEKLY;COUNT=3"), dtstart, NZ,
    )
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="s",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
        recur=recur,
    )
    stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ev, stamp)
    raw = ical.serialize(ical.event_calendar(ev))

    fake_store = MagicMock()
    fake_store.search_raw.return_value = [raw]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    out = server.list_events(
        start="2026-07-01T00:00", end="2026-07-31T00:00", calendar="personal",
    )

    for event in out["events"]:
        assert event["done_at"] == stamp.isoformat()
    # Anti-drift: the surfaced value is the literal stored X-, not a
    # reformatted / rounded version.
    assert out["events"][0]["done_at"] == "2024-01-07T21:00:00+00:00"


def test_list_events_override_done_at_takes_priority_over_master(monkeypatch):
    from unittest.mock import MagicMock
    from cal_scheduler import server

    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "Pacific/Auckland")

    dtstart = datetime(2026, 7, 1, 9, 0, tzinfo=NZ)
    recur = ical.validate_and_normalize_rrule(
        ical.parse_rrule("FREQ=WEEKLY;COUNT=3"), dtstart, NZ,
    )
    ev = ical.build_event(
        uid="u@cal-scheduler", summary="s",
        dtstart=dtstart, dtend=dtstart + timedelta(hours=1), now=NOW,
        recur=recur,
    )
    master_stamp = datetime(2024, 1, 7, 21, 0, tzinfo=ZoneInfo("UTC"))
    ical.mark_done(ev, master_stamp)

    occ2 = dtstart + timedelta(weeks=1)
    override_stamp = datetime(2024, 1, 8, 21, 0, tzinfo=ZoneInfo("UTC"))
    override = ical.build_done_override(
        master_ev=ev, occurrence=occ2, done_at=override_stamp,
    )
    cal = ical.event_calendar(ev)
    cal.add_component(override)
    raw = ical.serialize(cal)

    fake_store = MagicMock()
    fake_store.search_raw.return_value = [raw]
    monkeypatch.setattr(server, "_store", lambda: fake_store)

    out = server.list_events(
        start="2026-07-01T00:00", end="2026-07-31T00:00", calendar="personal",
    )

    stamps = [e["done_at"] for e in out["events"]]
    assert stamps == [
        master_stamp.isoformat(),    # occ 1: master marker
        override_stamp.isoformat(),  # occ 2: override wins
        master_stamp.isoformat(),    # occ 3: master marker
    ]
