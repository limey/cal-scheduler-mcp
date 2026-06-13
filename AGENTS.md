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

## Configuration

The configuration field spec. **Single source of truth:**
[`config.py`](src/cal_scheduler/config.py) `SCHEMA` tuple —
the env loader, the `doctor` tool's `config` echo, and this
section all read through it, so adding a field propagates
everywhere.

All settings come from the environment. Pass them through
your harness's per-server `env` block (the MCP itself never
persists anything; the harness is the persister). For the
validation round-trip after wiring, see *Validate* below.

| Field | Required? | Default | Example | What goes wrong if wrong |
|---|---|---|---|---|
| `CALDAV_BASE_URL` | yes | — | `http://127.0.0.1:5232` | every CalDAV-backed tool fails with a connection error; `doctor` reports `blockers` with a reachability hint |
| `CALDAV_USERNAME` | no | (empty) | `alice` | auth fails against servers that require it; `doctor` reports `blockers` with an auth hint |
| `CALDAV_PASSWORD` | no | (empty) | — *(secret — no worked example)* | same as `CALDAV_USERNAME`; also, if `CALDAV_USERNAME` is set without `CALDAV_PASSWORD` (or vice versa), Radicale's `auth=none` mode rejects the request — set both or neither |
| `CAL_DEFAULT_TZ` | no | `Pacific/Auckland` | `Pacific/Auckland` | events are stored in the wrong zone; naive datetimes are misinterpreted (assumed to be wall time in the configured zone) |
| `CAL_DEFAULT_CALENDAR` | no | — | `personal` | tool calls that omit `calendar` fail when the account has more than one calendar |

## Configure

The MCP starts with **zero configuration.** When a tool
needs a setting the agent hasn't wired in, the call fails
with a caller-actionable error that points at the field name
and hints at the fix. The full field spec — names, defaults,
required-ness, examples, and "what goes wrong if wrong" — is
in the *Configuration* section above. The runtime check for
"is the wiring actually good?" is the `doctor` tool.

The flow:

1. Read the *Configuration* section to find the field(s) the
   error named.
2. Set the values through whatever your harness uses for MCP
   server config (env vars, `config.yaml`, install paths —
   every harness differs). The MCP does not write to your
   harness's config; you do.
3. Restart the MCP per your harness's rules.
4. Call `doctor` to validate. On success it returns the
   resolved config (password redacted) and the list of
   calendars on the account. On failure it returns actionable
   hints naming the field that's wrong.

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

If any step fails with a configuration error, call `doctor`
— the response lists which field is missing or invalid. For
the field spec, see *Configuration* above.

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

This file describes the **target** install loop, and the
implementation matches it as of the version you install:

- `cal-scheduler` console script and `cal_scheduler` Python
  module: present.
- The 11 core tools (3 calendar + 6 event + 1 datetime
  helper + 1 `doctor` preflight): present.
- The configuration field spec (this file's *Configuration*
  section): present, kept in lockstep with `config.py`'s
  `SCHEMA` tuple.

## Pointers

- `PHILOSOPHY.md` — the *why* (design pillars, deliberate
  reductions, the PCD contract, Chronos provenance).
- `README.md` — the *what* for humans.
- `pyproject.toml` — install coordinates and dependency floor.
- `tests/` — the test suite. `uv run pytest -q` runs it; no
  live CalDAV server is needed for the default suite.
