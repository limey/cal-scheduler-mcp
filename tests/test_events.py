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

    # `calendar="personal"` skips _pick_calendar's config-touching fallbacks.
    result = server.create_event(
        summary="test", start="2026-06-30T21:00", calendar="personal",
    )
    start = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)
    end = ical.default_dtend(start)
    assert _humanize_timedelta(end - start) in result["note"]
