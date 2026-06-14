# cal-scheduler

A thin, **timezone-correct** [MCP](https://modelcontextprotocol.io) server for
scheduling against any [CalDAV](https://en.wikipedia.org/wiki/CalDAV) calendar.
It gives an LLM agent a small, strict set of calendar tools and does the
deterministic, fiddly parts of iCalendar correctly so the model doesn't have to.

It is **not** an NLP layer: the agent phrases the request, the server validates
it, rejects bad input loudly, and persists clean zoned `.ics`.

## Why it exists

Most calendar tooling an LLM reaches for gets three things subtly wrong. cal-scheduler
fixes them by construction:

- **Stores zoned, not bare UTC.** Events are written with `TZID`/`VTIMEZONE`, so a
  weekly 9am *stays* 9am across a daylight-saving boundary instead of drifting an
  hour. Storing bare UTC is the classic cause of that drift.
- **Validates recurrence.** It rejects an `RRULE` whose anchor contradicts it (e.g.
  a series starting on the 30th but set to repeat on the 1st), and normalises
  `UNTIL` to UTC under a zoned `DTSTART` as RFC 5545 requires.
- **Does real single-occurrence edits.** Exclude one instance (`EXDATE`) or move one
  instance (`RECURRENCE-ID`) without disturbing the rest of the series — the
  operations naive wrappers tend to lack.

## How it works

A small `uv` Python package that **composes** mature libraries rather than
implementing a calendar engine:

| Module | Role |
|---|---|
| `config.py` | environment config (`CALDAV_*`, `CAL_DEFAULT_TZ`, `CAL_DEFAULT_CALENDAR`) |
| `timezones.py` | parse datetimes; naive → assume default-zone wall time, offset → normalise into the zone; report what was assumed |
| `ical.py` | build/parse VEVENTs (`icalendar`), expand ranges (`recurring-ical-events`), recurrence validation, EXDATE/RECURRENCE-ID ops |
| `store.py` | CalDAV transport (`caldav`) — list/create/delete calendars, get/put/delete events, read-modify-write with etag |
| `server.py` | the FastMCP stdio server and the tool surface |

It runs as a **stdio MCP server** that an MCP host (Claude, an agent harness, etc.)
spawns as a subprocess.

## Install

Requires Python ≥ 3.11. The package is not yet on PyPI;
install from a clone until the first release ships (the repo
is private — `gh repo clone` needs your GitHub auth).

```bash
# run straight from the repo with uv (no global install)
uv run cal-scheduler

# or install the console script into a tool environment
gh repo clone limey/cal-scheduler-mcp
uv tool install /path/to/cal-scheduler-mcp
# or, with SSH GitHub access, in one step:
uv tool install git+ssh://git@github.com/limey/cal-scheduler-mcp
```

Once the package is published:

```bash
uv tool install cal-scheduler
# or
pip install cal-scheduler
```

> Don't `uv add cal-scheduler` for the MCP — `uv add` writes
> into whatever project you're sitting in, not into the tool
> environment. For an MCP server (spawned as a subprocess),
> `uv tool install` is the right shape.

## Configure

All configuration is via environment variables:

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `CALDAV_BASE_URL` | ✅ | — | CalDAV server URL, e.g. `http://127.0.0.1:5232` |
| `CALDAV_USERNAME` |  | — | CalDAV account user |
| `CALDAV_PASSWORD` |  | — | CalDAV account password |
| `CAL_DEFAULT_TZ` |  | `Pacific/Auckland` | IANA zone every event is stored in |
| `CAL_DEFAULT_CALENDAR` |  | — | calendar used when a call omits one |

Many MCP hosts strip inherited environment from stdio servers, so set these in the
host's per-server `env` block rather than relying on the ambient shell.

### Example MCP host config

The MCP runs as a stdio subprocess that the host spawns. Many hosts strip
inherited `PATH` from that subprocess, so wire the `uv run --directory`
form rather than relying on the `cal-scheduler` shim being on the spawn
host's `PATH`:

```json
{
  "mcpServers": {
    "cal-scheduler": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/cal-scheduler-mcp", "cal-scheduler"],
      "env": {
        "CALDAV_BASE_URL": "http://127.0.0.1:5232",
        "CALDAV_USERNAME": "me",
        "CALDAV_PASSWORD": "secret",
        "CAL_DEFAULT_TZ": "Pacific/Auckland"
      }
    }
  }
}
```

`/abs/path/to/cal-scheduler-mcp` is the absolute path to a local clone of
this repo (see *Install* above). For a post-release install, point the same
argument at the package install directory — find it with
`pip show cal-scheduler`.

Pair it with any CalDAV server. A simple self-hosted option is
[Radicale](https://radicale.org/) (plain `http://`, no TLS needed for local use).

## Tool surface

**Events** — `list_events(start, end, [calendar])`, `create_event(summary, start,
[end, calendar, description, location, rrule])`, `update_event(uid, …)`,
`delete_event(uid, [calendar])`, `exclude_occurrence(uid, occurrence, [calendar])`,
`move_occurrence(uid, occurrence, new_start, [new_end, calendar])`.

**Calendars** — `list_calendars`, `create_calendar(name)`, `delete_calendar(name)`.

**Helper** — `resolve_datetime(value)` — preview how a datetime will be interpreted,
without writing anything.

### The timezone rule (the whole point)

Every event is stored zoned to `CAL_DEFAULT_TZ`.

- A **naive** datetime (`2026-06-30T21:00`) is assumed to be wall time in that zone,
  and the tool response says so (`"assumed Pacific/Auckland wall time"`).
- An **offset-qualified** datetime (`…+12:00`) is honoured as an instant and
  re-expressed in the zone — same wall clock when the offset matches, a correct
  conversion otherwise.
- A **date-only** value (`2026-06-30`) is an all-day event.

## Develop

```bash
uv sync                 # install deps + dev tools
uv run ruff check       # lint
uv run pytest           # unit tests (no server required)
```

The unit tests cover the pure layers (timezone resolution, recurrence validation,
EXDATE/RECURRENCE-ID construction) and need no running CalDAV server. To exercise
the full stack end to end, point `CALDAV_BASE_URL` at a throwaway CalDAV account.

## License

[MIT](LICENSE) © 2026 Robert Clark
