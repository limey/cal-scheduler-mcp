# cal-scheduler — philosophy & principles

The body of knowledge that guides product, development, and API decisions for
cal-scheduler. Not API documentation and not baked into the tool surface — this is
the *why* behind the shape of the thing. When a design question is ambiguous, this
document is the tie-breaker. Keep the direction pure; if reality starts to contradict
a principle here, change the principle deliberately rather than letting the two drift.

## 1. Agent-first

The MCP is **for the agent.** The human is (usually) the beneficiary — they get a
calendar that schedules things, reminds them, acts on a known schedule, and into which
they can drop information that the agent will get around to — but the *consumer of this
interface is the agent*, and every design call is made from the agent's point of view.

## 2. Open — no harness favouritism

Not Copilot-only, not Claude-only, not Hermes-only. **`AGENTS.md` and open standards,
all the way.** Claude, Codex, Cline — they all understand it, and that universality is
the point. We do not optimise the surface for one vendor's harness; we optimise it for
*any* competent agent following open conventions.

## 3. A narrow, robust, complete slice — do one thing well

cal-scheduler is **intentionally a useful subset** of calendaring. It does not try to
expose the full surface of the CalDAV standard to an agent. The analogy: it's like
using a database purely through **SQL queries** — rather than dragging in XML document
passing, stored-procedure complexity, and manual timezone conversion. CalDAV is a
broad standard; the *one thing* we build on it is **calendar + scheduling
functionality**, done narrowly, robustly, and completely.

"Complete" matters as much as "narrow": the slice we expose is whole enough to be
genuinely useful (event CRUD, single-occurrence edits, calendar management,
timezone-correct storage) without bleeding into adjacent territory.

## 4. Deliberate reductions: one account, one timezone

The originating pain point was **local harness, local agent, local calendar** —
sovereign, private calendaring information, with a solid, reliable, accurate
implementation. From that:

- **One calendaring account, one timezone.** It's all local to the user. This is a
  primary reason the API complexity is reduced — we don't carry the machinery for
  multi-account, multi-timezone reconciliation because the use case doesn't need it.
- **No adjacent CalDAV functionality.** Tasks / To-Do lists and other CalDAV-adjacent
  features are deliberately *not* exposed.
- **No account management.** The assumption is that a user account already exists and
  is being plugged in; account setup lives **behind** the MCP surface, not as tools.

## 5. A self-teaching surface — guide the agent, don't assume expertise

**No assumption that the agent knows what CalDAV is.** This drives a core principle:
**tool responses guide the consuming agent to reason about and fix its own approach.**
The agent can then keep its own memory of what it learned.

> Example: the agent creates an event and provides only a start time. The tool creates
> a one-hour event (the default) and replies with a meaningful message about *what it
> did and why*. The agent learns from that and saves a local memory — "default event
> length is one hour; I need to provide a narrow time range for shorter or
> instantaneous events."

The MCP teaches through its responses; the agent remembers. (See also: *Progressive
Configuration Discovery*, below — the same principle applied to configuration.)

## 6. What it is NOT

- **Not a calendar.** It stays **un-opinionated about calendar choices.** It is a
  *layer between agents and the calendaring functionality of CalDAV* — not an
  application with its own opinions about how you should organise your time.
- **Not locked to local.** It grew up around local use, and local aligns with the
  sovereign-data principle — but **nothing should get in the way of pointing it at a
  remote, secure CalDAV instance.** (Still one account, one timezone — see §4. Remote
  *single-user* is fully supported; remote *multi-user / multi-timezone shared*
  calendars remain out of scope by design.)

## 7. Provenance — lessons that forced this build

cal-scheduler exists because other implementations didn't hold up. Chronos MCP, in
particular, **failed on timezone ambiguity and lost timezone information in the backing
`.ics` data** (events stored as bare UTC with no `TZID`). The hard-won lesson: zoned,
deterministic, RFC-faithful storage is non-negotiable for a calendar an agent can
trust. Our timezone discipline (store everything zoned to the configured zone; assume
naive datetimes are wall-time in that zone; honour and normalise offset-qualified ones;
reject contradictory recurrence anchors) is a direct response to those failures.

---

## Progressive Configuration Discovery (PCD)

The configuration philosophy that follows from §1, §2, and §5.

> Surface note: `AGENTS.md` *Configuration* is the spec for initial wiring; `doctor` is the runtime diagnostic. The MCP still never persists.

Instead of requiring configuration upfront before the server is useful, the server
**advertises its configuration requirements only when needed — through tool error
responses.** A tool that needs settings fails with a caller-actionable error that
**points to a dedicated `doctor` tool** (with the field spec itself in `AGENTS.md`
*Configuration* — a doc, so a scraping agent can self-teach without invoking a tool).

Crucially, **`doctor` does not persist anything.** It is a *domain-expert advisor*:
it tells the using agent exactly **what configuration the MCP needs** — the fields,
their formats, an example — and lets the agent's harness do what only it knows how to
do: wire those settings in (environment variables, config files, install paths — every
harness does this differently). The agent then restarts/reloads as its harness requires
and validates by calling `doctor`.

- **Progressive** — requirements surface gradually as the agent explores what the
  server can do, driven by *actual tool failures*, not an upfront wall of config.
- **Self-healing** — the agent always has a clear, in-conversation path to fix the
  problem (via `doctor`) without leaving the loop.
- **Harness-agnostic** — the MCP describes *what*; the harness owns *how*. We do not
  assume a persistence model, because we don't own the harness's.

This is the same teach-the-agent move as §5, applied to setup: the MCP stays simple
until it's actually used, and the agent learns what's needed by trying to help and
hitting the guardrails.

PCD describes what the *server* does — surfaces config needs gradually, as the
agent explores — not what the *agent* must do. A careful agent is free to read the
SCHEMA in `AGENTS.md` up front and pre-wire what it can predict; PCD is the
fallback for everything it didn't anticipate, not the prescribed path.
