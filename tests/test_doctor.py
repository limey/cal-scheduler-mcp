"""doctor tool: PCD runtime preflight.

The doctor tool replaces the configure advisor. The configuration field
spec now lives in AGENTS.md *Configuration* (a doc, so a scraping agent
can self-teach without invoking a tool); `doctor` is what the agent
calls when something is wrong or during the install-validate round-trip.
It runs a live preflight and returns either `{status: "ready", config:
{...}}` or `{status: "blockers", hints: [...]}` with actionable hints.

These tests pin:
- the ready path: the response shape, the SCHEMA-derived config echo,
  and the password redaction (never echo the password itself);
- the auth-failure mapping: an `AuthorizationError` is surfaced as a
  `blockers` response whose *first* hint names the Radicale `auth=none`
  password-presence rule when the configured credentials trip it;
- the no-mutate-env guard, ported from `test_configure.py` (the MCP
  never persists; the preflight must not write to `os.environ`).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import caldav
import pytest

from cal_scheduler import server
from cal_scheduler.config import SCHEMA, Config
from cal_scheduler.server import _doctor_preflight, _password_presence_hint


# ── helpers ──────────────────────────────────────────────────────────────────


def _stub_principal(*, calendars, make_raises=None):
    """Build a MagicMock that quacks like a caldav.Principal.

    The doctor preflight calls `.calendars()` and `.make_calendar(name=...)`
    on the principal. The mock returns the supplied calendar list and
    optionally raises from `make_calendar` (for the write-failed path).
    """
    principal = MagicMock()
    principal.calendars.return_value = calendars
    if make_raises is not None:
        principal.make_calendar.side_effect = make_raises
    else:
        principal.make_calendar.return_value = MagicMock()
    return principal


def _stub_calendar(display_name: str) -> MagicMock:
    cal = MagicMock()
    cal.get_display_name.return_value = display_name
    cal.name = display_name
    return cal


@pytest.fixture
def stub_caldav(monkeypatch):
    """Replace `caldav.DAVClient` with a factory that returns a controllable client.

    Tests reassign `stub_caldav.principal` (side_effect or return_value)
    to drive the preflight through its different branches. The factory
    ignores its arguments and returns the same client every time — the
    preflight reads `cfg.base_url`/`cfg.username`/`cfg.password` but
    those don't matter at this layer, only the principal/calendars
    surface does.
    """
    client = MagicMock()
    client.principal.return_value = _stub_principal(calendars=[])

    def factory(url, username=None, password=None):
        return client

    monkeypatch.setattr(caldav, "DAVClient", factory)
    return client


# ── ready path (stubbed Store; SCHEMA-derived config echo) ───────────────────


def test_doctor_ready_path_redacts_password_and_matches_schema(stub_caldav):
    """Healthy account: status=ready, config echo matches SCHEMA exactly
    (plus the `calendars` enumeration), and the password is surfaced as
    the literal "<set>" — never the value."""
    cfg = Config(
        base_url="http://caldav.example",
        username="alice",
        password="hunter2-secret",
        default_tz="Pacific/Auckland",
    )
    stub_caldav.principal.return_value = _stub_principal(
        calendars=[_stub_calendar("personal"), _stub_calendar("work")],
    )

    result = _doctor_preflight(cfg)

    assert result["status"] == "ready"
    config = result["config"]
    # Doctor's config echo matches SCHEMA exactly, plus the `calendars`
    # enumeration the preflight discovered — no more, no less. A stray
    # field here would be a PCD violation (a hidden config knob an
    # agent can't discover through the spec).
    assert set(config.keys()) == {f.name for f in SCHEMA} | {"calendars"}
    # Resolved values mirror the config the agent wired (the doctor echoes
    # the live config, not SCHEMA defaults — defaults only fill in
    # anything absent from the env).
    assert config["CALDAV_BASE_URL"] == "http://caldav.example"
    assert config["CALDAV_USERNAME"] == "alice"
    assert config["CALDAV_PASSWORD"] == "<set>"
    assert config["CAL_DEFAULT_TZ"] == "Pacific/Auckland"
    # And the literal password never appears anywhere in the response.
    assert "hunter2-secret" not in repr(result)


# ── auth-failure mapping (AuthorizationError → blockers) ────────────────────


def test_doctor_auth_failure_with_password_presence_rule_is_first_hint(stub_caldav):
    """The Radicale `auth=none` gotcha: username set, password empty.

    AuthorizationError is mapped to `{status: "blockers"}`; the *first*
    hint is the password-presence rule, the second is the generic
    auth-failure hint. The raw exception is never surfaced — the agent
    sees actionable advice.
    """
    cfg = Config(
        base_url="http://caldav.example",
        username="alice",
        password="",  # <-- the gotcha
        default_tz="Pacific/Auckland",
    )
    stub_caldav.principal.side_effect = caldav.lib.error.AuthorizationError(
        "http://caldav.example", "test unauthorized",
    )

    result = _doctor_preflight(cfg)

    assert result["status"] == "blockers"
    hints = result["hints"]
    # Two hints: the specific rule, then the generic auth-failure hint.
    assert len(hints) >= 2, f"expected at least 2 hints, got {hints!r}"
    assert "auth=none" in hints[0]
    assert "CALDAV_USERNAME" in hints[0] and "CALDAV_PASSWORD" in hints[0]
    # The "username set but no password" half of the rule is named.
    assert "no password" in hints[0]
    # The raw exception class is not in the response.
    assert "AuthorizationError" not in repr(result)


def test_doctor_auth_failure_mirrors_password_only_half(stub_caldav):
    """The mirror-image of the gotcha: password set, username empty.

    Same shape, the rule names the *password-only* half. Belt-and-braces:
    a future change that hard-codes "username without password" would
    miss this case, so the test pins both directions.
    """
    cfg = Config(
        base_url="http://caldav.example",
        username="",
        password="hunter2",
        default_tz="Pacific/Auckland",
    )
    stub_caldav.principal.side_effect = caldav.lib.error.AuthorizationError(
        "http://caldav.example", "test unauthorized",
    )

    result = _doctor_preflight(cfg)

    assert result["status"] == "blockers"
    assert "auth=none" in result["hints"][0]
    assert "no username" in result["hints"][0]


def test_doctor_auth_failure_without_password_presence_rule(stub_caldav):
    """Auth fails *with* both creds set: the rule doesn't apply, but the
    preflight still returns `blockers` and the generic auth hint is the
    first (and only) entry — nothing about password presence, because
    there's nothing the agent should change there."""
    cfg = Config(
        base_url="http://caldav.example",
        username="alice",
        password="hunter2",
        default_tz="Pacific/Auckland",
    )
    stub_caldav.principal.side_effect = caldav.lib.error.AuthorizationError(
        "http://caldav.example", "test unauthorized",
    )

    result = _doctor_preflight(cfg)

    assert result["status"] == "blockers"
    hints = result["hints"]
    # Only the generic hint; the password-presence rule returns "" and
    # is not appended.
    assert len(hints) == 1
    assert "auth=none" not in hints[0]
    assert "auth failed" in hints[0]


def test_password_presence_hint_pure_unit():
    """The hint function in isolation — no I/O, no mocks.

    Pins the rule: empty-string username + non-empty password → "no
    username" half; the reverse → "no password" half; both empty or both
    set → empty string (the rule does not apply).
    """
    def cfg_with(username: str, password: str) -> Config:
        return Config(
            base_url="http://x",
            username=username,
            password=password,
            default_tz="UTC",
        )

    assert _password_presence_hint(cfg_with("", "")) == ""
    assert _password_presence_hint(cfg_with("alice", "hunter2")) == ""
    assert "no password" in _password_presence_hint(cfg_with("alice", ""))
    assert "no username" in _password_presence_hint(cfg_with("", "hunter2"))


# ── no-mutate-env guard (ported from test_configure.py) ─────────────────────


def test_doctor_does_not_mutate_environ(monkeypatch):
    """PCD contract: the MCP never persists. The doctor tool must not
    write env vars (it's a runtime check, not a configuration setter).

    Set the env to a known state, run the tool through its public
    surface, snapshot, and assert the env is unchanged. This is the
    regression test for "a future change reaches for os.environ to
    persist something" — the test fails loudly the moment that happens.
    """
    # Make Config.from_env() succeed (CALDAV_BASE_URL is required; the
    # stubbed DAVClient below keeps the preflight off the network).
    monkeypatch.setenv("CALDAV_BASE_URL", "http://test.invalid")
    monkeypatch.setenv("CALDAV_USERNAME", "alice")
    monkeypatch.setenv("CALDAV_PASSWORD", "secret")
    monkeypatch.setenv("CAL_DEFAULT_TZ", "UTC")

    # No-network stubs: principal() returns a no-calendars principal;
    # make_calendar() and the cleanup loop are no-ops. The preflight
    # walks the ready path without touching the network.
    fake_principal = MagicMock()
    fake_principal.calendars.return_value = []
    fake_principal.make_calendar.return_value = MagicMock()
    fake_client = MagicMock()
    fake_client.principal.return_value = fake_principal
    monkeypatch.setattr(caldav, "DAVClient", lambda **kw: fake_client)

    before = dict(os.environ)
    # Call through the public tool wrapper, not the private helper, so
    # any future change to the wrapper (the env-read path lives there)
    # is also covered.
    result = server.doctor()
    after = dict(os.environ)

    assert before == after, "doctor must not mutate os.environ"
    # And the preflight actually ran (sanity check on the test itself).
    assert result["status"] == "ready"


# ── tool registration: doctor is registered, configure is not ──────────────


def test_mcp_registers_doctor_and_drops_configure():
    """DoD verification: `mcp.list_tools()` returns `doctor` and not
    `configure`. The PCD-replacement is structural, not just doc — a
    fresh agent that introspects the tool surface sees the new shape.
    """
    import asyncio

    from cal_scheduler.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "doctor" in names
    assert "configure" not in names


def test_config_does_not_carry_implicit_calendar_member():
    """The MCP has no notion of an implicit calendar fallback. A future
    field that revives it would silently break the explicit-or-error
    contract — pin its absence at the dataclass level."""
    from dataclasses import fields

    field_names = {f.name for f in fields(Config)}
    # No member should be read by the tool layer as an implicit
    # calendar default. Substring match keeps the test honest to the
    # intent — "no calendar-related field in Config" — without naming
    # a specific removed symbol.
    assert not any("calendar" in n.lower() for n in field_names)


def test_schema_does_not_carry_implicit_calendar_field():
    """The env loader has no field that drives an implicit calendar
    default. A stray entry would leak a PCD-hidden knob an agent
    couldn't discover through the spec."""
    names = {f.name.lower() for f in SCHEMA}
    assert not any("calendar" in n for n in names)
