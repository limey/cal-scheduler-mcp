"""Runtime configuration, read from the environment.

All settings come from the environment so the server stays a self-contained stdio
subprocess. Many MCP hosts strip inherited env from stdio servers, so pass these
through the host's per-server `env` block rather than relying on the ambient
environment. CAL_DEFAULT_TZ is the single zone every event is stored in — this is
a single-user, single-locale calendar by design.

Settings:
- CALDAV_BASE_URL      (required) e.g. http://127.0.0.1:5232
- CALDAV_USERNAME      (optional) CalDAV account user
- CALDAV_PASSWORD      (optional) CalDAV account password
- CAL_DEFAULT_TZ       (optional, default Pacific/Auckland) IANA zone for storage
- CAL_DEFAULT_CALENDAR (optional) calendar used when a call omits one
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class ConfigField:
    """One configurable setting.

    `default` is the value used when the env var is absent. `None` means
    "no default — the field must be supplied (when required) or stays unset".
    `example` is a non-secret illustrative value used by tests and
    documentation; pass `None` for secrets (passwords, tokens) so we never
    leak one by accident if a future field is added without thinking.
    """

    name: str
    required: bool
    default: str | None
    description: str
    example: str | None


# One source of truth for the env loader (`Config.from_env`) and any
# docs/tests that need to enumerate the configurable surface. Adding a
# field here propagates everywhere — the loader reads it, and any
# test that asserts the response field set against SCHEMA stays correct.
SCHEMA: tuple[ConfigField, ...] = (
    ConfigField(
        name="CALDAV_BASE_URL",
        required=True,
        default=None,
        description="Base URL of your CalDAV server (e.g. Radicale).",
        example="http://127.0.0.1:5232",
    ),
    ConfigField(
        name="CALDAV_USERNAME",
        required=False,
        default="",
        description="CalDAV account user; empty for anonymous (depends on server).",
        example="alice",
    ),
    ConfigField(
        name="CALDAV_PASSWORD",
        required=False,
        default="",
        description="CalDAV account password.",
        example=None,  # secret — never surface a worked example
    ),
    ConfigField(
        name="CAL_DEFAULT_TZ",
        required=False,
        default="Pacific/Auckland",
        description="IANA timezone every event is stored in; naive datetimes are assumed in this zone.",
        example="Pacific/Auckland",
    ),
    ConfigField(
        name="CAL_DEFAULT_CALENDAR",
        required=False,
        default=None,
        description="Calendar used when a tool call omits `calendar`; if unset, the only calendar on the account is used.",
        example="personal",
    ),
)


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    default_tz: str
    default_calendar: str | None

    @classmethod
    def from_env(cls) -> "Config":
        # Reads through SCHEMA so the env loader and any consumer of the
        # field list (tests, the doctor tool, future docs) can never
        # drift on field names, defaults, or required-ness.
        values: dict[str, str] = {}
        for f in SCHEMA:
            env_default = f.default if f.default is not None else ""
            raw = os.environ.get(f.name, env_default).strip()
            if f.required and not raw:
                raise ConfigError(
                    f"{f.name} is required (e.g. {f.example!r} — "
                    "call `doctor` to validate the live wiring)"
                )
            values[f.name] = raw
        return cls(
            base_url=values["CALDAV_BASE_URL"],
            username=values["CALDAV_USERNAME"],
            password=values["CALDAV_PASSWORD"],
            # Empty string is treated as "use the project default" — the
            # legacy `from_env` did the same.
            default_tz=values["CAL_DEFAULT_TZ"] or "Pacific/Auckland",
            default_calendar=(values["CAL_DEFAULT_CALENDAR"] or None),
        )
