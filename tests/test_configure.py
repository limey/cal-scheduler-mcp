"""configure tool: schema discovery + candidate validation.

The configure tool is the PCD advisor (see PHILOSOPHY.md). It is pure:
no env, no Store, no I/O. These tests pin both modes against the
contract AGENTS.md promises to a scraping agent.
"""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from cal_scheduler.config import validate_config
from cal_scheduler.server import configure


# ── schema mode (configure() with no args) ────────────────────────────────────


def test_schema_lists_every_documented_field():
    names = {f["name"] for f in configure()["fields"]}
    assert names == {
        "CALDAV_BASE_URL",
        "CALDAV_USERNAME",
        "CALDAV_PASSWORD",
        "CAL_DEFAULT_TZ",
        "CAL_DEFAULT_CALENDAR",
    }


def test_schema_marks_base_url_required_with_no_default():
    base_url = next(f for f in configure()["fields"] if f["name"] == "CALDAV_BASE_URL")
    assert base_url["required"] is True
    assert base_url["default"] is None
    assert base_url["example"]  # non-empty illustrative value


def test_schema_marks_default_tz_optional_with_iana_default():
    tz = next(f for f in configure()["fields"] if f["name"] == "CAL_DEFAULT_TZ")
    assert tz["required"] is False
    assert tz["default"] == "Pacific/Auckland"
    # The default must be a real IANA zone — `zoneinfo` is the canary.
    ZoneInfo(tz["default"])


def test_schema_does_not_surface_a_password_example():
    pw = next(f for f in configure()["fields"] if f["name"] == "CALDAV_PASSWORD")
    # Either None or a placeholder string — never a literal secret value.
    assert pw["example"] in (None,) or "<" in pw["example"]


def test_schema_response_carries_apply_hint_and_advisor_note():
    resp = configure()
    assert "how_to_apply" in resp and resp["how_to_apply"]
    assert "note" in resp and resp["note"]
    # PCD contract: configure is an advisor, not a persister. The response
    # must say so, so a scraping agent reading the response alone learns it.
    note = resp["note"].lower()
    assert "advisor" in note and "persist" in note


def test_schema_example_includes_base_url_and_zone():
    example = configure()["example"]
    assert "CALDAV_BASE_URL" in example and example["CALDAV_BASE_URL"]
    assert "CAL_DEFAULT_TZ" in example and example["CAL_DEFAULT_TZ"]


# ── validation mode (configure(config={...})) ─────────────────────────────────


def test_validate_missing_required_field_when_absent():
    resp = configure(config={})
    assert resp["valid"] is False
    assert "CALDAV_BASE_URL" in resp["missing"]


def test_validate_missing_required_field_when_blank():
    resp = configure(config={"CALDAV_BASE_URL": "   "})
    assert resp["valid"] is False
    assert "CALDAV_BASE_URL" in resp["missing"]


def test_validate_invalid_iana_timezone():
    resp = configure(config={"CALDAV_BASE_URL": "http://x", "CAL_DEFAULT_TZ": "Foo/Bar"})
    assert resp["valid"] is False
    assert resp["missing"] == []
    assert len(resp["invalid"]) == 1
    assert resp["invalid"][0]["field"] == "CAL_DEFAULT_TZ"
    assert "Foo/Bar" in resp["invalid"][0]["reason"]


def test_validate_minimal_valid_config_fills_defaults():
    resp = configure(config={"CALDAV_BASE_URL": "http://127.0.0.1:5232"})
    assert resp["valid"] is True
    assert resp["resolved"]["CALDAV_BASE_URL"] == "http://127.0.0.1:5232"
    # Optional fields fall through to their SCHEMA defaults.
    assert resp["resolved"]["CAL_DEFAULT_TZ"] == "Pacific/Auckland"
    assert resp["resolved"]["CAL_DEFAULT_CALENDAR"] is None


def test_validate_full_valid_config_preserves_overrides():
    resp = configure(config={
        "CALDAV_BASE_URL": "http://127.0.0.1:5232",
        "CALDAV_USERNAME": "alice",
        "CALDAV_PASSWORD": "secret",
        "CAL_DEFAULT_TZ": "Europe/London",
        "CAL_DEFAULT_CALENDAR": "work",
    })
    assert resp["valid"] is True
    assert resp["resolved"]["CAL_DEFAULT_TZ"] == "Europe/London"
    assert resp["resolved"]["CAL_DEFAULT_CALENDAR"] == "work"
    # The success note steers the agent toward applying the config and retrying.
    note = resp["note"].lower()
    assert "looks good" in note and "retry" in note


def test_validate_invalid_response_steers_toward_another_call():
    # The self-teaching response (PHILOSOPHY §5): tell the agent what to do
    # next, in the same turn. The `note` field is the channel.
    resp = configure(config={})
    assert resp["valid"] is False
    assert "configure" in resp["note"].lower()  # agent should call again


# ── pure validator: missing/invalid independent of the tool wrapper ───────────


def test_validate_config_pure_for_unknown_zone():
    missing, invalid = validate_config(
        {"CALDAV_BASE_URL": "http://x", "CAL_DEFAULT_TZ": "Mars/Olympus"}
    )
    assert missing == []
    assert invalid and invalid[0][0] == "CAL_DEFAULT_TZ"


def test_validate_config_pure_for_blank_required():
    missing, invalid = validate_config({"CALDAV_BASE_URL": ""})
    assert missing == ["CALDAV_BASE_URL"]
    assert invalid == []


# ── guard: the tool must not mutate the environment ───────────────────────────


def test_configure_does_not_mutate_environ():
    """PCD contract: the MCP never persists. The tool must not write env."""
    # The tool's two modes never call os.environ[...] = ...; this test
    # catches a regression where a future change reaches for the env.
    before = dict(os.environ)
    configure()
    configure(config={"CALDAV_BASE_URL": "http://x"})
    after = dict(os.environ)
    assert before == after, "configure() must not mutate os.environ"
