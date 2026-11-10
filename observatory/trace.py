"""Request trace: one chat request through the service and its physical dependencies.

The prompt text is never stored here or in any event; only a correlation ID
and the request length bucket are recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import EventRecorder

STAGES = [
    ("ingress", "Ingress / load balancer", ("network_security", "power_distribution")),
    ("auth", "Authentication & policy checks", ("network_security", "observability")),
    ("routing", "Routing", ("serving_nodes", "network_security")),
    ("queue", "Queueing", ("serving_nodes",)),
    ("serving", "Model serving", ("serving_nodes", "storage", "accelerators_min")),
    ("inference", "Inference (accelerators & GPU memory)", ("accelerators_min", "cooling")),
    ("response", "Response", ("network_security",)),
]

DEFAULT_PROMPT = "Summarise today's grid conditions for my operations team in three bullet points."


@dataclass(frozen=True)
class TraceStage:
    id: str
    label: str
    status: str  # ok | slow | blocked | skipped
    latency_ms: float
    dependencies: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class RequestTrace:
    request_id: str
    step: int
    sim_time: str
    stages: list[TraceStage]
    outcome: str
    model: str
    total_latency_s: float | None
    response_text: str


def correlation_id(run_id: str, step: int, seq: int) -> str:
    return f"req-{run_id[:6]}-{step:04d}-{seq:03d}"


def build_trace(row: dict, run_id: str, seq: int, load_reasons: dict[str, str]) -> RequestTrace:
    rid = correlation_id(run_id, row["step"], seq)
    powered = {k[5:]: v > 1e-9 for k, v in row.items() if k.startswith("load_")}
    powered["cooling"] = row["cooling_headroom_pct"] > 0
    quality = row["svc_quality"]
    lat = row["svc_latency_s"]
    stages: list[TraceStage] = []
    blocked = False
    queue_wait = 0.0
    if lat is not None and row["svc_capacity_req_s"] > 0:
        queue_wait = row["svc_queue"] / row["svc_capacity_req_s"]
    for sid, label, deps in STAGES:
        missing = [d for d in deps if not powered.get(d, True)]
        if blocked:
            stages.append(TraceStage(sid, label, "skipped", 0.0, deps, "Not reached"))
            continue
        if missing:
            why = "; ".join(f"{d}: {load_reasons.get(d, 'unpowered')}" for d in missing)
            stages.append(TraceStage(sid, label, "blocked", 0.0, deps, f"Dependency unavailable — {why}"))
            blocked = True
            continue
        if sid == "queue":
            ms = queue_wait * 1000
            status = "slow" if queue_wait > 1.0 else "ok"
            note = f"Queue depth {row['svc_queue']:.0f} requests" if row["svc_queue"] > 0 else "No queue"
        elif sid == "inference":
            ms = max(0.0, ((lat or 0) - queue_wait) * 1000 - 120)
            status = "slow" if quality == "DEGRADED" else "ok"
            note = f"{row['svc_model']} on {row['svc_replicas']} replicas"
        elif sid == "routing" and row["svc_routed_small"]:
            ms, status, note = 15.0, "slow", "Routed to the configured smaller model (capacity constraint)"
        else:
            ms, status, note = {"ingress": 20.0, "auth": 35.0, "routing": 15.0, "serving": 30.0, "response": 20.0}[sid], "ok", "Healthy"
        stages.append(TraceStage(sid, label, status, ms, deps, note))
    if blocked or quality == "UNAVAILABLE":
        outcome = "UNAVAILABLE"
        total = None
    else:
        outcome = quality
        total = sum(s.latency_ms for s in stages) / 1000
    return RequestTrace(rid, row["step"], row["sim_time"], stages, outcome, row["svc_model"], total, response_text(outcome, row))


def response_text(outcome: str, row: dict) -> str:
    """Deterministic stand-in answer whose content reflects the simulated state."""
    if row["grid_connected"]:
        power = ("Regional supply is adequate and the facility is grid-connected." if not row["dr_active"]
                 else "The grid operator has asked large users to cut demand; the facility is at its firm import level.")
    else:
        src = "standby generators" if row["supply_generator_mw"] > 0 else ("its battery" if row["supply_battery_mw"] > 0 else "UPS bridging")
        power = f"The regional grid is down; the facility is islanded and running on {src}."
    paused = [k[5:] for k, v in row.items() if k.startswith("load_") and k[5:] in ("training", "batch", "optional") and v <= 0]
    work = ("All workloads, including training and batch jobs, are running." if not paused
            else f"Paused to protect the chat service: {', '.join(paused)}.")
    if outcome == "FULL":
        return f"• {power}\n• {work}\n• Next review at the evening demand peak."
    if outcome == "REDUCED":
        return f"[Answered by the smaller model — shorter reply]\n• {power}\n• {work}"
    if outcome == "DEGRADED":
        return f"[Delayed response — the service is queueing requests]\n• {power}\n• Expect slower answers until capacity recovers."
    return ("Service temporarily unavailable. The AI service is paused while the facility "
            "operates on limited backup power. Please retry later — no answer was generated.")


def record_request(recorder: EventRecorder, trace: RequestTrace, prompt_chars: int) -> None:
    """Log the request without its content: only ID, outcome and a size bucket."""
    bucket = "short" if prompt_chars < 200 else ("medium" if prompt_chars < 1000 else "long")
    recorder.record(step=trace.step, sim_time=trace.sim_time, component="ai/request", severity="info",
                    event_type="request_received", reason=f"Chat request received ({bucket} prompt)",
                    action="Trace started", outcome="Accepted", request_id=trace.request_id)
    sev = "critical" if trace.outcome == "UNAVAILABLE" else ("warning" if trace.outcome == "DEGRADED" else "info")
    recorder.record(step=trace.step, sim_time=trace.sim_time, component="ai/request", severity=sev,
                    event_type="request_completed", reason=f"Outcome {trace.outcome}",
                    observed_value=trace.total_latency_s, unit="s", action="Response returned", outcome=trace.outcome,
                    request_id=trace.request_id)
