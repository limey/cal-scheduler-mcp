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

The package is **not yet on PyPI.** Use the pre-release path
below. `uv tool install cal-scheduler` and `pip install
cal-scheduler` will work once it ships; the `uv add` warning
below stays valid either way.

Pre-release (the repo is private — `gh repo clone` needs your
GitHub auth):

```bash
gh repo clone limey/cal-scheduler-mcp
uv tool install /path/to/cal-scheduler-mcp
# or, with SSH GitHub access, in one step:
uv tool install git+ssh://git@github.com/limey/cal-scheduler-mcp
```

> **`uv add` is the wrong tool here.** `uv add cal-scheduler`
> writes the dependency into the *current directory's*
> `pyproject.toml` — for an MCP server (a spawned subprocess,
> not an embedded library) that mutates whichever project the
> agent is sitting in, not the MCP install. Use the isolated
> `uv tool install` above. Reserve `uv add` for embedding
> `cal_scheduler` as a library.

Post-release:

```bash
uv tool install cal-scheduler
# or
pip install cal-scheduler
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
| `CALDAV_PASSWORD` | no | (empty) | — *(secret — no worked example)* | same as `CALDAV_USERNAME`; also, an empty password means the caldav client sends no Basic auth header at all — some servers (Radicale `auth=none`) need one to route to `/<username>/`, so a non-empty placeholder is required even though its value is ignored (see callout below) |
| `CAL_DEFAULT_TZ` | no | `Pacific/Auckland` | `Pacific/Auckland` | events are stored in the wrong zone; naive datetimes are misinterpreted (assumed to be wall time in the configured zone) |
| `CAL_DEFAULT_CALENDAR` | no | — | `personal` | tool calls that omit `calendar` fail when the account has more than one calendar |

> **Radicale `auth=none` (and other username-routed servers):** set a non-empty placeholder password (e.g. `x`). The caldav client only sends a Basic auth header when `CALDAV_PASSWORD` is set, and the username in that header is how the server routes to `/<username>/`. An empty password means no header is sent, and routing fails with what looks like an auth error.

## Configure

There is no configure tool — by design the MCP never
persists. `doctor` is the intended *first* call after
writing the configuration: it is the discovery surface
for "is the wiring actually good?" and will name any
missing or unreachable field. Your harness owns
persistence.

**Do not port-scan, `curl PROPFIND` the server, or
username-probe with `uv run python -c` one-liners before
calling `doctor`.** `doctor` opens the same connection
(and more) and reports the result in one round-trip,
structured for the agent to act on. The shell is the
slower path; the tool is the intended one.

The MCP starts with **zero configuration.** When a tool
needs a setting the agent hasn't wired in, the call fails
with a caller-actionable error that points at the field
name and hints at the fix. The full field spec — names,
defaults, required-ness, examples, and "what goes wrong
if wrong" — is in the *Configuration* section above. The
runtime check for "is the wiring actually good?" is the
`doctor` tool.

**Reload semantics.** Whether a manual restart is required
is harness-specific. Some harnesses (e.g. Claude Code)
hot-load newly added servers, so a freshly wired-in
server's tools can appear with no manual restart; others
require an explicit reload or session restart per the
harness's own rules. The first call to `doctor` after
writing the config confirms both that the server is live
and that the harness has picked the new env up — a
`ready` status means you can stop restarting; a
`blockers` response suggests either the harness has not
yet picked the new env up, or a field is wrong.

The flow:

1. Read the *Configuration* section to find the field
   spec (names, defaults, required-ness).
2. Wire your best-guess values through whatever your
   harness uses for MCP server config (env vars,
   `config.yaml`, install paths — every harness differs).
   The MCP does not write to your harness's config; you
   do. If the only available hints are "the server is at
   `<host>:<port>`" and "the account is `<user>`", wire
   those — `doctor` will name the gap if a field is
   still wrong.
3. **First call: `doctor`.** This is the discovery move.
   On success it returns the resolved config (password
   redacted) and the list of calendars on the account.
   On failure it returns actionable hints naming the
   field that is missing or unreachable. If your harness
   needs a restart to pick up new env (see *Reload
   semantics* above), restart before this call.
4. Loop on the `doctor` response. Fix the named field,
   restart if your harness requires it, and call `doctor`
   again. The loop terminates when `doctor` returns
   `status: ready`.

## Validate

After install + configure, a minimal round-trip:

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
