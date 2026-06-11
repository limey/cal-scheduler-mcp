"""Datetime/timezone resolution — the core correctness layer."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from cal_scheduler.timezones import TimeError, get_zone, resolve

NZ = ZoneInfo("Pacific/Auckland")  # June = NZST = +12:00 (no DST in winter)


def test_date_only_is_all_day():
    r = resolve("2026-06-30", NZ)
    assert r.is_all_day
    assert r.value == date(2026, 6, 30)
    assert not r.assumed


def test_naive_is_assumed_wall_time_in_default_zone():
    r = resolve("2026-06-30T21:00", NZ)
    assert isinstance(r.value, datetime)
    assert r.value.tzinfo == NZ
    assert (r.value.hour, r.value.minute) == (21, 0)
    assert r.assumed
    assert "assumed Pacific/Auckland" in r.note


def test_offset_matching_zone_keeps_wall_clock():
    # +12:00 already matches NZST, so 21:00 stays 21:00.
    r = resolve("2026-06-30T21:00+12:00", NZ)
    assert (r.value.hour, r.value.minute) == (21, 0)
    assert not r.assumed
    assert "interpreted in Pacific/Auckland" in r.note


def test_offset_mismatch_is_converted():
    # 09:00 UTC on the 30th = 21:00 NZST same day.
    r = resolve("2026-06-30T09:00+00:00", NZ)
    assert (r.value.hour, r.value.minute) == (21, 0)
    assert not r.assumed
    assert "converted" in r.note


def test_trailing_z_is_utc():
    r = resolve("2026-06-30T09:00Z", NZ)
    assert (r.value.hour, r.value.minute) == (21, 0)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2026-13-40T99:99"])
def test_unparseable_raises(bad):
    with pytest.raises(TimeError):
        resolve(bad, NZ)


def test_unknown_zone_raises():
    with pytest.raises(TimeError):
        get_zone("Mars/Olympus_Mons")
