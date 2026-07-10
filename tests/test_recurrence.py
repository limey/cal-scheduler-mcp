"""RRULE validation/normalisation — reject self-contradicting series, fix UNTIL."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cal_scheduler.ical import (
    _WEEKDAYS,
    ValidationError,
    parse_rrule,
    validate_and_normalize_rrule,
)

NZ = ZoneInfo("Pacific/Auckland")
UTC = ZoneInfo("UTC")
DTSTART = datetime(2026, 6, 30, 21, 0, tzinfo=NZ)  # the 30th, a Tuesday


def _validate(rule, dtstart=DTSTART):
    return validate_and_normalize_rrule(parse_rrule(rule), dtstart, NZ)


def test_strips_rrule_prefix():
    recur = parse_rrule("RRULE:FREQ=WEEKLY;COUNT=4")
    assert str(recur.get("FREQ")[0]) == "WEEKLY"


def test_missing_freq_rejected():
    with pytest.raises(ValidationError, match="FREQ"):
        _validate("BYDAY=MO")


def test_count_and_until_together_rejected():
    with pytest.raises(ValidationError, match="COUNT.*UNTIL|both"):
        _validate("FREQ=DAILY;COUNT=5;UNTIL=20260710T000000Z")


def test_bymonthday_contradicting_anchor_rejected():
    # start on the 30th but repeat on the 1st — the canonical bad series.
    with pytest.raises(ValidationError, match="BYMONTHDAY|anchor"):
        _validate("FREQ=MONTHLY;BYMONTHDAY=1")


def test_bymonthday_matching_anchor_ok():
    recur = _validate("FREQ=MONTHLY;BYMONTHDAY=30")
    assert recur is not None


def test_byday_contradicting_anchor_rejected():
    wrong_day = _WEEKDAYS[(DTSTART.weekday() + 1) % 7]
    with pytest.raises(ValidationError, match="BYDAY|anchor"):
        _validate(f"FREQ=WEEKLY;BYDAY={wrong_day}")


def test_byday_matching_anchor_ok():
    right_day = _WEEKDAYS[DTSTART.weekday()]
    recur = _validate(f"FREQ=WEEKLY;BYDAY={right_day}")
    assert recur is not None


def test_bymonth_contradicting_anchor_rejected():
    # DTSTART is in June (month 6); say the series is only in December.
    with pytest.raises(ValidationError, match="BYMONTH|anchor"):
        _validate("FREQ=YEARLY;BYMONTH=12")


def test_until_normalized_to_utc_under_zoned_start():
    recur = _validate("FREQ=DAILY;UNTIL=20260710T235959")
    until = recur["UNTIL"][0]
    assert isinstance(until, datetime)
    assert until.tzinfo == UTC
