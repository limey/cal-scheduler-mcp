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
class Config:
    base_url: str
    username: str
    password: str
    default_tz: str
    default_calendar: str | None

    @classmethod
    def from_env(cls) -> "Config":
        base_url = os.environ.get("CALDAV_BASE_URL", "").strip()
        if not base_url:
            raise ConfigError("CALDAV_BASE_URL is required (e.g. http://radicale:5232)")
        return cls(
            base_url=base_url,
            username=os.environ.get("CALDAV_USERNAME", "").strip(),
            password=os.environ.get("CALDAV_PASSWORD", "").strip(),
            # Deployment locale. Everything is stored zoned to this; a naive
            # datetime from the agent is assumed to be wall time here.
            default_tz=os.environ.get("CAL_DEFAULT_TZ", "Pacific/Auckland").strip(),
            # Optional: the calendar used when a tool call omits one.
            default_calendar=(os.environ.get("CAL_DEFAULT_CALENDAR", "").strip() or None),
        )
