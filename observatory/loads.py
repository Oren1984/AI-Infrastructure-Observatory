"""Critical-load budget and workload priority.

Allocation runs in a fixed order: the critical load chain first (power
distribution, observability, network and security, storage, serving nodes,
accelerators), then interactive inference, optional work, batch and training.
Shedding therefore happens in reverse order. Inference is gated on the full
critical chain: if any link is unpowered, no inference is served.

Restoration is staged: the critical chain is restored as one stage, then each
workload, one stage at a time, after ``restore_stable_steps`` consecutive
steps in which the stage would fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Facility


@dataclass(frozen=True)
class LoadBlock:
    id: str
    label: str
    it_mw: float
    non_it_mw: float
    tier: str  # core | inference | workload
    kind: str
    fractional: bool

    def cost_mw(self, cooling_factor: float, fraction: float = 1.0) -> float:
        return (self.non_it_mw + self.it_mw * (1 + cooling_factor)) * fraction


@dataclass(frozen=True)
class Allocation:
    fractions: dict[str, float]
    reasons: dict[str, str]
    it_mw: float
    cooling_mw: float
    non_it_mw: float
    core_ok: bool
    inference_ok: bool
    requested_mw: float

    @property
    def total_mw(self) -> float:
        return self.it_mw + self.cooling_mw + self.non_it_mw

    def is_on(self, block_id: str) -> bool:
        return self.fractions.get(block_id, 0.0) > 1e-9


@dataclass
class LoadManager:
    facility: Facility
    blocks: list[LoadBlock] = field(init=False)
    stages: list[list[str]] = field(init=False)
    _on: dict[str, bool] = field(init=False)
    _streak: int = 0
    _initialised: bool = False

    def __post_init__(self) -> None:
        f = self.facility
        crit = [
            LoadBlock(c.id, c.label, c.mw if c.it_load else 0.0, 0.0 if c.it_load else c.mw, c.tier, "critical", False)
            for c in f.critical_load
        ]
        work = [
            LoadBlock(w.id, w.label, w.it_mw, 0.0, "workload", w.kind, w.fractional) for w in f.workloads
        ]
        self.blocks = crit + work
        self.stages = [[c.id for c in crit]] + [[w.id] for w in work]
        self._on = {b.id: True for b in self.blocks}

    @property
    def critical_ids(self) -> list[str]:
        return self.stages[0]

    def full_demand_mw(self) -> float:
        cf = self.facility.cooling_power_factor
        return sum(b.cost_mw(cf) for b in self.blocks)

    def critical_budget_mw(self, tier: str | None = None) -> float:
        cf = self.facility.cooling_power_factor
        return sum(
            b.cost_mw(cf) for b in self.blocks if b.tier != "workload" and (tier is None or b.tier == tier)
        )

    def allocate(
        self,
        *,
        budget_mw: float,
        reserve_core_mw: float,
        thermal_capacity_mw: float,
        forced_off: dict[str, str] | None = None,
    ) -> Allocation:
        """Allocate power in priority order.

        ``budget_mw`` may serve any block; ``reserve_core_mw`` is extra power
        that may only serve core-tier blocks (battery energy below reserve).
        """
        if budget_mw < 0 or reserve_core_mw < 0 or thermal_capacity_mw < 0:
            raise ValueError("budgets must be non-negative")
        forced_off = forced_off or {}
        cf = self.facility.cooling_power_factor
        general, reserve, thermal = budget_mw, reserve_core_mw, thermal_capacity_mw
        fractions: dict[str, float] = {}
        reasons: dict[str, str] = {}
        it = cool = non_it = 0.0

        pending_stage = self._first_pending_stage()
        stage_fit_ok = True  # does the pending stage fit this step?

        def take(block: LoadBlock, frac: float, core: bool) -> None:
            nonlocal general, reserve, thermal, it, cool, non_it
            cost = block.cost_mw(cf, frac)
            from_general = min(general, cost)
            general -= from_general
            if core:
                reserve -= cost - from_general
            thermal -= block.it_mw * frac
            it += block.it_mw * frac
            cool += block.it_mw * cf * frac
            non_it += block.non_it_mw * frac
            fractions[block.id] = frac

        # ---- critical chain (all or nothing per link, strictly in order)
        chain_broken = False
        crit_ids = set(self.critical_ids)
        crit_blocks = [b for b in self.blocks if b.id in crit_ids]
        crit_pending = pending_stage == 0
        if crit_pending:
            # restore the whole chain at once, only if it all fits
            total = sum(b.cost_mw(cf) for b in crit_blocks)
            core_total = sum(b.cost_mw(cf) for b in crit_blocks if b.tier == "core")
            fits = (
                total <= general + min(reserve, core_total) + 1e-9
                and sum(b.it_mw for b in crit_blocks) <= thermal + 1e-9
            )
            stage_fit_ok = fits
        for b in crit_blocks:
            core = b.tier == "core"
            if chain_broken:
                fractions[b.id] = 0.0
                reasons[b.id] = "Critical chain incomplete upstream"
                continue
            restore_ready = stage_fit_ok and self._streak + 1 >= self._n_restore()
            if crit_pending and not self._on[b.id] and not restore_ready:
                fractions[b.id] = 0.0
                reasons[b.id] = "Awaiting stable supply before restore" if stage_fit_ok else "Insufficient power for critical chain"
                chain_broken = True
                continue
            cost = b.cost_mw(cf)
            avail = general + (reserve if core else 0.0)
            if cost <= avail + 1e-9 and b.it_mw <= thermal + 1e-9:
                take(b, 1.0, core)
                reasons[b.id] = "Powered"
            else:
                fractions[b.id] = 0.0
                reasons[b.id] = "Cooling limit" if b.it_mw > thermal + 1e-9 else "Power budget exhausted"
                chain_broken = True
        core_ok = all(fractions.get(b.id, 0) > 0 for b in crit_blocks if b.tier == "core")
        inference_ok = not chain_broken

        # ---- workloads
        for idx, b in enumerate(self.blocks):
            if b.tier != "workload":
                continue
            stage_idx = self._stage_of(b.id)
            if not inference_ok:
                fractions[b.id] = 0.0
                reasons[b.id] = "Critical chain not available"
                continue
            if b.id in forced_off:
                fractions[b.id] = 0.0
                reasons[b.id] = forced_off[b.id]
                if stage_idx == pending_stage:
                    stage_fit_ok = False
                continue
            was_on = self._on[b.id]
            full_cost = b.cost_mw(cf)
            power_frac = min(1.0, general / full_cost) if full_cost > 0 else 1.0
            thermal_frac = min(1.0, thermal / b.it_mw) if b.it_mw > 0 else 1.0
            max_frac = max(0.0, min(power_frac, thermal_frac))
            limit = "Cooling limit" if thermal_frac < power_frac else "Power budget"
            if not was_on:
                if stage_idx != pending_stage:
                    fractions[b.id] = 0.0
                    reasons[b.id] = "Queued for staged restore"
                    continue
                fits = max_frac >= 1.0 - 1e-9
                stage_fit_ok = fits
                if not (fits and self._streak + 1 >= self._n_restore()):
                    fractions[b.id] = 0.0
                    reasons[b.id] = "Awaiting stable headroom before restore" if fits else f"Shed: {limit.lower()}"
                    continue
                take(b, 1.0, False)
                reasons[b.id] = "Restored"
                continue
            if b.fractional:
                if max_frac > 1e-9:
                    take(b, max_frac, False)
                    reasons[b.id] = "Running" if max_frac >= 1 - 1e-9 else f"Reduced concurrency ({limit.lower()})"
                else:
                    fractions[b.id] = 0.0
                    reasons[b.id] = f"Shed: {limit.lower()}"
            elif max_frac >= 1.0 - 1e-9:
                take(b, 1.0, False)
                reasons[b.id] = "Running"
            else:
                fractions[b.id] = 0.0
                reasons[b.id] = f"Shed: {limit.lower()}"

        requested = self.full_demand_mw()
        self._commit(fractions, pending_stage, stage_fit_ok)
        return Allocation(fractions, reasons, it, cool, non_it, core_ok, inference_ok, requested)

    # ----------------------------------------------------------------- helpers
    def _n_restore(self) -> int:
        return self.facility.thresholds.restore_stable_steps

    def _stage_of(self, block_id: str) -> int:
        for i, stage in enumerate(self.stages):
            if block_id in stage:
                return i
        raise KeyError(block_id)

    def _first_pending_stage(self) -> int | None:
        for i, stage in enumerate(self.stages):
            if not all(self._on[b] for b in stage):
                return i
        return None

    def _commit(self, fractions: dict[str, float], pending_stage: int | None, stage_fit_ok: bool) -> None:
        before = self._first_pending_stage()
        for bid, frac in fractions.items():
            self._on[bid] = frac > 1e-9
        after = self._first_pending_stage()
        if pending_stage is None or after != before:
            self._streak = 0
        elif stage_fit_ok:
            self._streak += 1
        else:
            self._streak = 0
