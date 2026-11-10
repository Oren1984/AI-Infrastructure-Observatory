"""Structured event recording with alert de-duplication.

Events never contain prompt text, credentials or user identifiers: the schema
has no field for them, and ``record`` refuses unknown keyword arguments.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field

SEVERITIES = ("info", "notice", "warning", "critical")


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    step: int
    sim_time: str
    component: str
    severity: str
    event_type: str
    reason: str
    observed_value: float | None = None
    unit: str = ""
    threshold: float | None = None
    action: str = ""
    outcome: str = ""
    request_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class EventRecorder:
    run_id: str
    events: list[Event] = field(default_factory=list)
    _active_alerts: dict[str, Event] = field(default_factory=dict)
    suppressed: dict[str, int] = field(default_factory=dict)

    def record(
        self,
        *,
        step: int,
        sim_time: str,
        component: str,
        severity: str,
        event_type: str,
        reason: str,
        observed_value: float | None = None,
        unit: str = "",
        threshold: float | None = None,
        action: str = "",
        outcome: str = "",
        request_id: str | None = None,
    ) -> Event:
        if severity not in SEVERITIES:
            raise ValueError(f"unknown severity {severity!r}")
        ev = Event(
            event_id=f"{self.run_id[:8]}-{len(self.events) + 1:05d}",
            run_id=self.run_id,
            step=step,
            sim_time=sim_time,
            component=component,
            severity=severity,
            event_type=event_type,
            reason=reason,
            observed_value=None if observed_value is None else round(float(observed_value), 3),
            unit=unit,
            threshold=threshold,
            action=action,
            outcome=outcome,
            request_id=request_id,
        )
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------ alerts
    def alert(self, key: str, condition: bool, **kwargs) -> Event | None:
        """Raise an alert once while ``condition`` holds; record recovery once.

        Repeated evaluations of an already-active alert are counted in
        ``suppressed`` instead of producing duplicate events.
        """
        active = key in self._active_alerts
        if condition and not active:
            ev = self.record(event_type="alert", **kwargs)
            self._active_alerts[key] = ev
            return ev
        if condition and active:
            self.suppressed[key] = self.suppressed.get(key, 0) + 1
            return None
        if not condition and active:
            raised = self._active_alerts.pop(key)
            return self.record(
                step=kwargs["step"],
                sim_time=kwargs["sim_time"],
                component=raised.component,
                severity="info",
                event_type="recovery",
                reason=f"Recovered: {raised.reason}",
                observed_value=kwargs.get("observed_value"),
                unit=raised.unit,
                threshold=raised.threshold,
                action="Alert cleared",
                outcome=f"Alert raised at step {raised.step} resolved",
            )
        return None

    @property
    def active_alerts(self) -> list[Event]:
        return list(self._active_alerts.values())


def events_to_csv(events: list[Event]) -> str:
    buf = io.StringIO()
    names = list(Event.__dataclass_fields__)
    writer = csv.DictWriter(buf, fieldnames=names)
    writer.writeheader()
    for e in events:
        writer.writerow(e.as_dict())
    return buf.getvalue()


def events_to_json(events: list[Event]) -> str:
    return json.dumps([e.as_dict() for e in events], indent=2)
