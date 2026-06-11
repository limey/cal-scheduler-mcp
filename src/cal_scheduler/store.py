"""CalDAV transport.

Thin wrapper over `caldav`: we do not speak the protocol, we drive the client.
Talks to any CalDAV server at CALDAV_BASE_URL — including a plain-`http://`
TLS-free local server such as Radicale, which is the common self-hosted setup.

Calendars are addressed by their human display name (what a CalDAV web UI shows
and what callers use), resolved to the underlying collection URL here.
"""
from __future__ import annotations

import caldav


class NotFound(ValueError):
    """A named calendar or event UID does not exist. Message is agent-facing."""


class Store:
    def __init__(self, base_url: str, username: str, password: str):
        self._client = caldav.DAVClient(
            url=base_url, username=username or None, password=password or None
        )
        self._principal = self._client.principal()

    # ── calendars ─────────────────────────────────────────────────────────────

    def calendar_names(self) -> list[str]:
        return sorted(self._display_name(c) for c in self._principal.calendars())

    def _display_name(self, cal: caldav.Calendar) -> str:
        try:
            return str(cal.get_display_name())
        except Exception:
            return str(cal.name)

    def _resolve(self, name: str) -> caldav.Calendar:
        for c in self._principal.calendars():
            if self._display_name(c) == name:
                return c
        raise NotFound(
            f"calendar {name!r} not found; available: {', '.join(self.calendar_names()) or '(none)'}"
        )

    def create_calendar(self, name: str) -> None:
        if name in self.calendar_names():
            raise NotFound(f"calendar {name!r} already exists")
        self._principal.make_calendar(name=name)

    def delete_calendar(self, name: str) -> None:
        self._resolve(name).delete()

    # ── events ──────────────────────────────────────────────────────────────--

    def save_new_event(self, calendar: str, ics: str) -> None:
        self._resolve(calendar).save_event(ics)

    def fetch_event(self, calendar: str, uid: str) -> caldav.Event:
        cal = self._resolve(calendar)
        try:
            ev = cal.event_by_uid(uid)
        except caldav.lib.error.NotFoundError as exc:
            raise NotFound(f"event {uid!r} not found in calendar {calendar!r}") from exc
        if ev is None:
            raise NotFound(f"event {uid!r} not found in calendar {calendar!r}")
        return ev

    def write_back(self, event: caldav.Event, ics: str) -> None:
        """Persist an edited .ics back to the same resource (read-modify-write).

        `caldav` carries the etag from the fetch and sends If-Match on save, so a
        concurrent change is rejected rather than silently clobbered.
        """
        event.data = ics
        event.save()

    def delete_event(self, calendar: str, uid: str) -> None:
        self.fetch_event(calendar, uid).delete()

    def search_raw(self, calendar: str, start, end) -> list[str]:
        """Return the raw .ics of every event resource overlapping [start, end].

        We narrow with a server-side date-range REPORT, then expand client-side
        with `recurring_ical_events` (Radicale's own expansion is limited). A
        recurring master can fall outside the window yet still have an occurrence
        inside it, so we also include any event carrying an RRULE.
        """
        cal = self._resolve(calendar)
        seen: dict[str, str] = {}
        for ev in cal.search(start=start, end=end, event=True, expand=False):
            seen[str(ev.url)] = ev.data
        for ev in cal.events():
            data = ev.data
            if "RRULE" in data and str(ev.url) not in seen:
                seen[str(ev.url)] = data
        return list(seen.values())
