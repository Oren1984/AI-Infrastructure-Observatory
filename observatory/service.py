"""AI service capacity: replicas, routing, queueing and user-visible quality."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ModelProfile, Service

FULL = "FULL"
REDUCED = "REDUCED"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"

QUALITY_TEXT = {
    FULL: "Full service",
    REDUCED: "Reduced (smaller model)",
    DEGRADED: "Degraded (slow / queueing)",
    UNAVAILABLE: "Unavailable",
}


@dataclass(frozen=True)
class ServiceSnapshot:
    demand_req_s: float
    model_id: str | None
    model_label: str
    replicas: int
    capacity_req_s: float
    throughput_req_s: float
    queue_depth: float
    rejected_req: float
    latency_s: float | None
    utilization_pct: float
    gpu_memory_pct: float
    quality: str
    routed_to_small: bool
    concurrency_pct: float


class ServiceModel:
    """Deterministic queueing approximation of the interactive chat service."""

    def __init__(self, spec: Service):
        self.spec = spec
        self.queue = 0.0
        self._full_capacity = None

    def demand(self, minutes: float) -> float:
        s = self.spec
        return s.baseline_req_per_s * (1 + s.demand_swing * math.sin(2 * math.pi * minutes / 240.0))

    @staticmethod
    def _capacity(profile: ModelProfile, power_mw: float) -> tuple[int, float]:
        replicas = int(math.floor(power_mw / profile.replica_mw + 1e-9))
        return replicas, replicas * profile.req_per_s_per_replica

    def step(self, minutes: float, accel_power_mw: float, full_power_mw: float, inference_ok: bool, dt_s: float) -> ServiceSnapshot:
        s = self.spec
        lam = self.demand(minutes)
        if not inference_ok or accel_power_mw <= 1e-9:
            rejected = lam * dt_s + self.queue
            self.queue = 0.0
            return ServiceSnapshot(lam, None, "No model serving", 0, 0.0, 0.0, 0.0, rejected, None, 0.0, 0.0, UNAVAILABLE, False, 0.0)

        n_l, cap_l = self._capacity(s.large_model, accel_power_mw)
        _, cap_full = self._capacity(s.large_model, full_power_mw)
        routed = cap_l < lam * s.route_small_threshold
        profile = s.small_model if routed else s.large_model
        n, cap = (self._capacity(s.small_model, accel_power_mw) if routed else (n_l, cap_l))
        if cap <= 0:
            rejected = lam * dt_s + self.queue
            self.queue = 0.0
            return ServiceSnapshot(lam, profile.id, profile.label, 0, 0.0, 0.0, 0.0, rejected, None, 0.0, 0.0, UNAVAILABLE, routed, 0.0)

        backlog = self.queue + lam * dt_s
        served = min(backlog, cap * dt_s)
        queue = backlog - served
        rejected = max(0.0, queue - s.max_queue)
        self.queue = min(queue, float(s.max_queue))
        throughput = served / dt_s
        rho = min(lam / cap, 0.98)
        latency = profile.base_latency_s * (1 + 0.2 * rho / (1 - rho)) + self.queue / cap
        util = min(100.0, 100.0 * throughput / cap)
        mem = min(100.0, profile.weights_memory_pct + s.kv_cache_memory_pct * util / 100.0)
        if latency > s.slo_latency_s or rejected > 0:
            quality = DEGRADED
        elif routed:
            quality = REDUCED
        else:
            quality = FULL
        conc = 100.0 * cap_l / cap_full if cap_full > 0 else 0.0
        return ServiceSnapshot(lam, profile.id, profile.label, n, cap, throughput, self.queue, rejected, latency, util, mem, quality, routed, min(conc, 100.0))
