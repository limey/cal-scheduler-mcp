# AGENTS.md

You are reading this because you are an agent evaluating
`cal-scheduler` as a candidate MCP for calendar / scheduling
work. This document is for you. (`README.md` is for humans.)

## What it is

`cal-scheduler` is a **timezone-correct CalDAV scheduling MCP**
that exposes a deliberately small surface over the
calendar-and-scheduling slice of CalDAV. It is un-opinionated
about the calendar application itself — it is a layer between
agents and CalDAV, not a calendar app.

## When it fits

Pick `cal-scheduler` when **all** of the following are true:

- The work is **calendar / scheduling** (events, recurring
  series, single-occurrence edits) against a CalDAV account.
- You can express the operation as one of: list / create /
  update / delete events or calendars, with optional RRULE
  recurrence and single-occurrence overrides.
- The user has a single CalDAV account and a single timezone
  they care about (this is by design — see *Deliberate
  reductions* below).
- You can tolerate a strict, deterministic server: bad input
  fails loudly with a caller-actionable error; the `.ics` store
  is zoned and RFC-faithful.

## When it doesn't

Skip `cal-scheduler` and look elsewhere when:

- The work is **tasks / todos**. CalDAV-adjacent functionality
  is deliberately not exposed. Use a tasks-specific MCP.
- The work needs **multi-account or multi-timezone
  reconciliation**. Single account, single timezone is a
  primary design reduction. Multi-user shared calendars
  (Google Calendar, iCloud shared, etc.) are out of scope.
- The work needs **the full CalDAV surface** (freebusy,
  scheduling messages, principal properties, raw `.ics`
  editing). This MCP exposes calendar + scheduling only.
- The work is not on CalDAV at all (Exchange, Google Calendar
  API directly, etc.). `cal-scheduler` is CalDAV-only.

## Install

PyPI (after first public release — install from the pinned
ref or local clone until then):

```bash
uv tool install cal-scheduler
# or
pip install cal-scheduler
```

Project-local (with `uv`):

```bash
uv add cal-scheduler
```

The `cal-scheduler` console script and the `cal_scheduler`
Python module are both installed. Pin to a specific version
for reproducible installs.

## Configure

The MCP starts with **zero configuration**. When you call a
tool that needs settings, the tool returns an error that
points you at the `configure` tool. Call `configure` — it is
the domain expert and will tell you exactly which fields the
MCP needs (`CALDAV_BASE_URL`, optional `CALDAV_USERNAME` /
`CALDAV_PASSWORD`, `CAL_DEFAULT_TZ`, optional
`CAL_DEFAULT_CALENDAR`), with formats and a worked example.

`configure` is an **advisor**, not a persister. It describes
*what* the MCP needs; it does not write to your harness's
config. Apply the returned values through whatever your
harness uses for MCP server config (env vars, `config.yaml`,
install paths — every harness differs), then restart the MCP
per your harness's rules, then retry the original call to
validate.

## Validate

After install + configure + restart, a minimal round-trip:

1. `list_calendars` — should return at least the calendars on
   the wired account.
2. `create_calendar` with a throwaway name, then
   `list_calendars` — confirms the create round-trips.
3. `create_event` with a `summary` and a `start` only —
   should return a 1h event in the configured zone and
   surface the default in the response (this is the
   self-teaching response; see `PHILOSOPHY.md` §5).
4. `list_events` over a window covering the event — confirms
   the read round-trips.
5. `delete_event` (or `delete_calendar`) on the throwaway —
   cleans up.

If any step fails with a configuration error, call
`configure` again — the response will tell you which field is
missing or invalid.

## Deliberate reductions (read once, internalise)

From `PHILOSOPHY.md` §4 and §6, the design reductions that
shape this MCP's surface. They are deliberate, not gaps:

- **One account, one timezone.** Multi-account and
  multi-timezone reconciliation are out of scope.
- **No tasks / todos.** Tasks and other CalDAV-adjacent
  functionality are deliberately not exposed.
- **No account management.** The assumption is that an
  account already exists; account setup lives behind the MCP
  surface.
- **Local-first in origin, remote-capable by design.**
  Sovereign data is the originating use case, but the MCP
  does not get in the way of pointing it at a remote, secure
  CalDAV instance — single-user only.

If your use case doesn't fit these, `cal-scheduler` is the
wrong tool.

## Implementation status

This file describes the **target** install loop. The
implementation is delivered in pieces. As of the version you
install, expect:

- `cal-scheduler` console script and `cal_scheduler` Python
  module: present.
- The 10 core tools (3 calendar + 6 event + 1 datetime
  helper): present.
- The `configure` tool: tracked under Phase 3 (see the
  project's issue tracker). If it is not yet registered when
  you read this, the workaround is to read `PHILOSOPHY.md`'s
  *Progressive Configuration Discovery* section and set the
  listed env vars in your harness's per-server `env` block
  directly.

## Pointers

- `PHILOSOPHY.md` — the *why* (design pillars, deliberate
  reductions, the PCD contract, Chronos provenance).
- `README.md` — the *what* for humans.
- `pyproject.toml` — install coordinates and dependency floor.
- `tests/` — the test suite. `uv run pytest -q` runs it; no
  live CalDAV server is needed for the default suite.
