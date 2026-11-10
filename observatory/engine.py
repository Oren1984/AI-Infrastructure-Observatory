"""Deterministic simulation engine.

``SimulationEngine.step()`` advances one tick of the simulation clock;
``run()`` produces the complete, replayable result for a scenario. The UI never
mutates engine state: it indexes into the finished ``RunResult`` with a
playback cursor, so Streamlit reruns cannot duplicate transitions or alerts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from . import energy as en
from .config import ReferenceModel, Scenario, validate_scenario_targets
from .events import Event, EventRecorder
from .loads import Allocation, LoadManager
from .service import QUALITY_TEXT, UNAVAILABLE, ServiceModel

NORMAL, CONSTRAINED, EMERGENCY = "Normal", "Capacity constrained", "Emergency"

# Operational decisions are tied to an owner and a policy. Policy IDs are
# referenced from data/catalog/governance.yaml (related_control).
POLICIES = {
    "shed": ("POL-LS-01 Load-shedding priority", "Facility shift lead"),
    "restore": ("POL-RC-02 Staged recovery", "Facility shift lead"),
    "route": ("POL-SV-03 Model routing under constraint", "SRE on call"),
    "island": ("POL-EM-04 Islanding and backup dispatch", "Electrical duty engineer"),
    "shutdown": ("POL-EM-05 Controlled shutdown", "Incident commander"),
    "demand_response": ("POL-DR-06 Grid operator instructions", "Energy desk"),
}


@dataclass
class RunResult:
    run_id: str
    scenario: Scenario
    steps: list[dict]
    events: list[Event]
    suppressed_alerts: dict[str, int]
    source_reasons: list[dict[str, str]]
    load_reasons: list[dict[str, str]]
    decisions: list[dict] = field(default_factory=list)

    def at(self, step: int) -> dict:
        return self.steps[max(0, min(step, len(self.steps) - 1))]

    def events_until(self, step: int) -> list[Event]:
        return [e for e in self.events if e.step <= step]


class SimulationEngine:
    def __init__(self, reference: ReferenceModel, scenario: Scenario, run_id: str | None = None):
        validate_scenario_targets(reference, scenario)
        self.ref = reference
        self.scenario = scenario
        self.f = reference.facility
        self.run_id = run_id or uuid.uuid4().hex
        self.recorder = EventRecorder(self.run_id)
        self.grid = en.RegionalGrid(reference.region)
        self.loads = LoadManager(self.f)
        self.service = ServiceModel(self.f.service)
        self.battery = en.BatteryState(self.f.battery)
        self.gen = en.GeneratorState(self.f.generators)
        self.ups = en.UpsState(self.f.ups)
        self.local_factors = {s.fuel: 1.0 for s in reference.region.sources}
        self.cooling_capacity = self.f.cooling_capacity_mw_it
        self.connected = True
        self.ever_islanded = False
        self.island_steps = 0
        self.reconnect_streak = 0
        self.dr_latched = False
        self.t = 0
        self.steps: list[dict] = []
        self.source_reasons: list[dict[str, str]] = []
        self.load_reasons: list[dict[str, str]] = []
        self.decisions: list[dict] = []
        self._prev_source_reason: dict[str, str] = {}
        self._prev_on: dict[str, bool] = {}
        self._prev_routed = False
        self._prev_phase = ""
        self._prev_state = ""
        self._events_by_step = {}
        for ev in scenario.events:
            self._events_by_step.setdefault(ev.step, []).append(ev)

    # ----------------------------------------------------------------- helpers
    @property
    def dt_h(self) -> float:
        return self.scenario.step_minutes / 60.0

    def sim_time(self, step: int) -> str:
        minutes = self.ref.region.start_hour * 60 + step * self.scenario.step_minutes
        h, m = divmod(int(round(minutes)) % (24 * 60), 60)
        return f"{h:02d}:{m:02d} (T+{int(step * self.scenario.step_minutes)} min)"

    def _rec(self, **kw) -> Event:
        return self.recorder.record(step=self.t, sim_time=self.sim_time(self.t), **kw)

    def _decision(self, action: str, policy_key: str, evidence: Event, detail: str) -> None:
        policy, owner = POLICIES[policy_key]
        self.decisions.append(
            {
                "step": self.t,
                "sim_time": self.sim_time(self.t),
                "decision": action,
                "detail": detail,
                "owner": owner,
                "policy": policy,
                "evidence_event": evidence.event_id,
            }
        )

    # -------------------------------------------------------------------- step
    def step(self) -> dict:
        if self.t >= self.scenario.steps:
            raise RuntimeError("scenario already complete")
        f, th, dt_h = self.f, self.f.thresholds, self.dt_h
        minutes = self.t * self.scenario.step_minutes

        # 1. scenario inputs
        for ev in self._events_by_step.get(self.t, []):
            if ev.kind == "source_availability":
                self.grid.factors[ev.target] = ev.value
                self.local_factors[ev.target] = ev.value
                sev = "warning" if ev.value < 0.5 else "notice"
                self._rec(component=f"grid/{ev.target}", severity=sev, event_type="scenario_input", reason=ev.reason,
                          observed_value=ev.value * 100, unit="% availability", action="Scenario input applied", outcome="Regional availability changed")
            elif ev.kind == "cooling_capacity":
                self.cooling_capacity = ev.value
                self._rec(component="cooling/plant", severity="warning" if ev.value < f.cooling_capacity_mw_it else "info",
                          event_type="scenario_input", reason=ev.reason, observed_value=ev.value, unit="MW IT heat",
                          action="Scenario input applied", outcome="Cooling capacity changed")
            elif ev.kind == "demand_factor":
                self.grid.demand_factor = ev.value
                self._rec(component="grid/demand", severity="info", event_type="scenario_input", reason=ev.reason,
                          observed_value=ev.value, unit="× base demand", action="Scenario input applied", outcome="Regional demand changed")
            else:
                self._rec(component="scenario", severity="info", event_type="note", reason=ev.reason)

        # 2. regional grid and connection status
        reg = self.grid.dispatch(minutes)
        ratio = reg.total_available / reg.demand_mw
        if self.connected and ratio < th.grid_collapse_ratio:
            self.connected = False
            self.ever_islanded = True
            self.island_steps = 0
            ev = self._rec(component="grid/connection", severity="critical", event_type="grid_loss",
                           reason="Regional supply below collapse threshold; facility islands",
                           observed_value=ratio * 100, unit="% of demand available", threshold=th.grid_collapse_ratio * 100,
                           action="Open grid breaker, UPS bridges, start generators", outcome="Islanded")
            self._decision("Island facility and dispatch local backup", "island", ev, "Grid lost; local assets only")
        elif not self.connected:
            self.reconnect_streak = self.reconnect_streak + 1 if reg.margin >= th.reconnect_margin else 0
            if self.reconnect_streak >= th.reconnect_stable_steps:
                self.connected = True
                self.reconnect_streak = 0
                ev = self._rec(component="grid/connection", severity="notice", event_type="grid_restored",
                               reason=f"Regional margin ≥ {th.reconnect_margin:.0%} for {th.reconnect_stable_steps} steps",
                               observed_value=reg.margin * 100, unit="% reserve margin", threshold=th.reconnect_margin * 100,
                               action="Resynchronise and close grid breaker", outcome="Grid-connected; staged recovery begins")
                self._decision("Reconnect to grid and begin staged recovery", "restore", ev, "Stable regional margin")
        islanded = not self.connected
        if islanded:
            self.island_steps += 1
        if not self.connected:
            self.dr_latched = False
        elif reg.margin < th.demand_response_margin:
            self.dr_latched = True
        elif reg.margin >= th.demand_response_release_margin:
            self.dr_latched = False
        dr_active = self.connected and self.dr_latched
        import_cap = 0.0 if islanded else (f.firm_import_mw if dr_active else f.grid_connection_mw)

        # 3. local sources
        local_avail: dict[str, float] = {}
        reasons: dict[str, str] = {}
        for src in f.local_sources:
            raw = src.capacity_mw * self.local_factors.get(src.fuel, 1.0) * en.profile_value(src.profile, minutes, self.ref.region.start_hour)
            usable, why = en.local_source_status(src, raw, islanded)
            local_avail[src.id] = usable
            reasons[src.id] = why

        # 4. generator, battery, UPS limits
        if islanded and f.generators.start_on_island:
            if self.gen.mode == "off":
                self.gen.request_start()
        elif self.connected and not f.generators.grid_support_allowed and self.gen.mode in ("starting", "running"):
            self.gen.stop()
        gen_allowed = islanded or f.generators.grid_support_allowed
        gen_max = self.gen.max_output_mw(dt_h) if gen_allowed else 0.0
        bat_ok = (islanded and f.battery.island_capable) or (self.connected and f.battery.grid_support_allowed)
        bat_general = self.battery.max_discharge_mw(dt_h, allow_reserve=False) if bat_ok else 0.0
        bat_reserve = 0.0
        if islanded and bat_ok:
            bat_reserve = max(0.0, self.battery.max_discharge_mw(dt_h, allow_reserve=True) - bat_general)
        bridging = islanded and self.gen.mode in ("off", "starting")
        ups_max = self.ups.max_discharge_mw(dt_h) if bridging else 0.0
        local_total = sum(local_avail.values())
        budget = import_cap + local_total + gen_max + bat_general + ups_max

        # 5. load allocation
        forced: dict[str, str] = {}
        if islanded and f.shed_discretionary_on_island:
            forced = {w.id: "Paused by island policy (discretionary)" for w in f.workloads if w.kind in ("training", "batch", "optional")}
        alloc = self.loads.allocate(budget_mw=budget, reserve_core_mw=bat_reserve,
                                    thermal_capacity_mw=self.cooling_capacity, forced_off=forced)

        # 6. supply dispatch, in merit order for the current mode
        need = alloc.total_mw
        supply: dict[str, float] = {}
        for sid, mw in local_avail.items():
            supply[sid] = min(mw, need)
            need -= supply[sid]
        grid_import = min(import_cap, need)
        need -= grid_import
        if islanded:
            gen_mw, litres = self.gen.run(min(gen_max, need), dt_h)
        else:
            gen_mw, litres = (self.gen.run(min(gen_max, need), dt_h) if gen_max > 0 else (0.0, 0.0))
        need -= gen_mw
        bat_mw = self.battery.discharge(min(bat_general, need), dt_h) if bat_general > 0 else 0.0
        need -= bat_mw
        ups_mw = self.ups.discharge(min(ups_max, need), dt_h) if ups_max > 0 else 0.0
        need -= ups_mw
        if need > 1e-9 and bat_reserve > 0:
            extra = self.battery.discharge(min(bat_reserve, need), dt_h)
            bat_mw += extra
            need -= extra
        unserved = max(0.0, need)

        # recharge from spare grid import
        charge_mw = 0.0
        if self.connected:
            spare = max(0.0, import_cap - grid_import)
            ups_ch = self.ups.recharge(spare, dt_h)
            spare -= ups_ch
            bat_ch = self.battery.charge(spare, dt_h) if bat_mw <= 1e-9 else 0.0
            charge_mw = ups_ch + bat_ch
            grid_import += charge_mw
        self.gen.tick_start()

        # 7. source reason codes
        shares = reg.shares()
        for s in self.ref.region.sources:
            if islanded:
                reasons[s.id] = en.UNREACHABLE
            elif reg.available_mw[s.fuel] <= 1e-9:
                reasons[s.id] = en.UNAVAILABLE
            elif reg.dispatched_mw[s.fuel] <= 1e-9:
                reasons[s.id] = en.STANDBY
            elif reg.dispatched_mw[s.fuel] >= reg.available_mw[s.fuel] - 1e-6:
                reasons[s.id] = en.AT_CAPACITY
            else:
                reasons[s.id] = en.DISPATCHED
        for sid in list(local_avail):
            if reasons[sid] == en.DISPATCHED and supply.get(sid, 0) <= 1e-9:
                reasons[sid] = en.STANDBY
        if self.gen.mode == "exhausted":
            reasons["generator"] = en.EXHAUSTED
        elif self.gen.mode == "starting":
            reasons["generator"] = en.STARTING
        elif not gen_allowed:
            reasons["generator"] = en.POLICY_RESTRICTED
        elif gen_mw > 1e-9:
            reasons["generator"] = en.AT_CAPACITY if gen_mw >= gen_max - 1e-6 else en.DISPATCHED
        else:
            reasons["generator"] = en.STANDBY
        if not bat_ok:
            reasons["battery"] = en.POLICY_RESTRICTED
        elif self.battery.soc <= 1e-6:
            reasons["battery"] = en.EXHAUSTED
        elif bat_mw > 1e-9:
            reasons["battery"] = en.DISPATCHED
        elif self.battery.soc <= f.battery.reserve_soc + 1e-6 and islanded:
            reasons["battery"] = en.RESERVE_HELD
        else:
            reasons["battery"] = en.STANDBY
        if ups_mw > 1e-9:
            reasons["ups"] = en.DISPATCHED
        elif self.ups.energy_mwh <= 1e-9:
            reasons["ups"] = en.EXHAUSTED
        else:
            reasons["ups"] = en.STANDBY
        self._record_source_changes(reasons, reg, local_avail, gen_mw, bat_mw, ups_mw)

        # 8. AI service
        accel = sum(b.it_mw * alloc.fractions.get(b.id, 0.0) for b in self.loads.blocks
                    if b.id == "accelerators_min" or b.kind == "interactive")
        full_accel = sum(b.it_mw for b in self.loads.blocks if b.id == "accelerators_min" or b.kind == "interactive")
        svc = self.service.step(minutes, accel, full_accel, alloc.inference_ok, self.scenario.step_minutes * 60)
        self._record_load_changes(alloc, svc)

        # 9. state and phase
        shed_any = any(not alloc.is_on(b.id) or alloc.fractions[b.id] < 1 - 1e-9 for b in self.loads.blocks)
        headroom_pct = 100.0 * (self.cooling_capacity - alloc.it_mw) / self.cooling_capacity if self.cooling_capacity > 0 else 0.0
        recovering = self.ever_islanded and self.connected and shed_any
        if islanded:
            if not alloc.core_ok:
                phase = "Blackout"
            elif not alloc.inference_ok:
                phase = "Controlled shutdown"
            elif bridging:
                phase = "UPS bridging / generator starting"
            elif gen_mw > 1e-9:
                phase = "Islanded on generator"
            else:
                phase = "Islanded on battery"
        elif recovering:
            phase = "Staged recovery"
        elif dr_active:
            phase = "Demand reduction instruction"
        elif shed_any:
            phase = "Load shedding"
        else:
            phase = "Grid-connected"
        if islanded or recovering:
            state = EMERGENCY
        elif dr_active or shed_any or headroom_pct < th.cooling_headroom_alert_pct:
            state = CONSTRAINED
        else:
            state = NORMAL
        if phase != self._prev_phase:
            self._rec(component="facility/operations", severity="critical" if state == EMERGENCY else ("warning" if state == CONSTRAINED else "info"),
                      event_type="phase_change", reason=f"{self._prev_phase or 'Start'} → {phase}", action="Operating phase updated", outcome=state)
        self._prev_phase, self._prev_state = phase, state

        # 10. alerts (deduplicated)
        T = dict(step=self.t, sim_time=self.sim_time(self.t))
        self.recorder.alert("dr", dr_active, component="grid/connection", severity="warning",
                            reason="Grid operator demand-reduction instruction active", observed_value=reg.margin * 100,
                            unit="% reserve margin", threshold=th.demand_response_margin * 100,
                            action=f"Cap import at firm level {f.firm_import_mw} MW", outcome="Load shed as needed", **T)
        self.recorder.alert("islanded", islanded, component="grid/connection", severity="critical",
                            reason="Facility islanded from grid", observed_value=ratio * 100, unit="% of demand available",
                            threshold=th.grid_collapse_ratio * 100, action="Local backup dispatch", outcome="Running on local assets", **T)
        self.recorder.alert("cooling", headroom_pct < th.cooling_headroom_alert_pct, component="cooling/plant", severity="warning",
                            reason="Cooling headroom below threshold", observed_value=headroom_pct, unit="% headroom",
                            threshold=th.cooling_headroom_alert_pct, action="Hold IT load within cooling capacity", outcome="Monitored", **T)
        self.recorder.alert("battery_low", self.battery.soc < th.battery_low_soc, component="power/battery", severity="warning",
                            reason="Battery state of charge low", observed_value=self.battery.soc * 100, unit="% SoC",
                            threshold=th.battery_low_soc * 100, action="Preserve reserve for core load", outcome="Monitored", **T)
        self.recorder.alert("fuel_low", self.gen.fuel_pct < th.fuel_low_pct, component="power/generator", severity="critical",
                            reason="Generator fuel low", observed_value=self.gen.fuel_pct, unit="% fuel",
                            threshold=th.fuel_low_pct, action="Request fuel delivery; plan load reduction", outcome="Monitored", **T)
        lat = svc.latency_s if svc.latency_s is not None else 0.0
        self.recorder.alert("latency", svc.latency_s is not None and lat > th.latency_alert_s, component="ai/service", severity="warning",
                            reason="Chat latency above SLO", observed_value=lat, unit="s", threshold=th.latency_alert_s,
                            action="Reduce concurrency or route to smaller model", outcome="Monitored", **T)
        self.recorder.alert("queue", svc.queue_depth > th.queue_alert, component="ai/service", severity="warning",
                            reason="Request queue above threshold", observed_value=svc.queue_depth, unit="requests",
                            threshold=th.queue_alert, action="Shed optional traffic", outcome="Monitored", **T)
        self.recorder.alert("unavailable", svc.quality == UNAVAILABLE, component="ai/service", severity="critical",
                            reason="Inference unavailable: critical chain not powered", observed_value=accel, unit="MW accelerators",
                            action="Return explicit unavailable response", outcome="Users see service unavailable", **T)

        # 11. accounting
        it_mwh = alloc.it_mw * dt_h
        grid_to_load = grid_import - charge_mw
        grid_mix = {s.fuel: grid_to_load * shares.get(s.fuel, 0.0) for s in self.ref.region.sources}
        import_mix = {s.fuel: grid_import * shares.get(s.fuel, 0.0) for s in self.ref.region.sources}
        gen_water_l = 0.0
        water_unknown = False
        grid_co2 = 0.0
        for s in self.ref.region.sources:
            mwh = import_mix[s.fuel] * dt_h
            grid_co2 += mwh * s.emission_tco2_mwh
            if s.water_consumption_l_mwh is None:
                water_unknown = water_unknown or mwh > 1e-9
            else:
                gen_water_l += mwh * s.water_consumption_l_mwh
        row = {
            "step": self.t,
            "minutes": minutes,
            "sim_time": self.sim_time(self.t),
            "state": state,
            "phase": phase,
            "grid_connected": self.connected,
            "dr_active": dr_active,
            "regional_demand_mw": reg.demand_mw,
            "regional_available_mw": reg.total_available,
            "regional_margin_pct": reg.margin * 100,
            "regional_shortfall_mw": reg.shortfall_mw,
            "price_eur_mwh": reg.price_eur_mwh,
            **{f"regional_{k}_mw": v for k, v in reg.dispatched_mw.items()},
            **{f"regional_avail_{k}_mw": v for k, v in reg.available_mw.items()},
            **{f"supply_grid_{k}_mw": v for k, v in grid_mix.items()},
            **{f"supply_{k}_mw": v for k, v in supply.items()},
            "supply_generator_mw": gen_mw,
            "supply_battery_mw": bat_mw,
            "supply_ups_mw": ups_mw,
            "grid_import_mw": grid_import,
            "grid_to_load_mw": grid_to_load,
            "charging_mw": charge_mw,
            "import_cap_mw": import_cap,
            "available_supply_mw": budget + bat_reserve,
            "facility_requested_mw": alloc.requested_mw,
            "facility_load_mw": alloc.total_mw,
            "unserved_mw": unserved,
            "it_load_mw": alloc.it_mw,
            "cooling_power_mw": alloc.cooling_mw,
            "cooling_capacity_mw": self.cooling_capacity,
            "cooling_headroom_pct": headroom_pct,
            "critical_budget_mw": self.loads.critical_budget_mw(),
            "core_ok": alloc.core_ok,
            "inference_ok": alloc.inference_ok,
            "battery_soc_pct": self.battery.soc * 100,
            "battery_energy_mwh": self.battery.energy_mwh,
            "ups_soc_pct": self.ups.soc * 100,
            "generator_mode": self.gen.mode,
            "generator_fuel_l": self.gen.fuel_l,
            "generator_fuel_pct": self.gen.fuel_pct,
            "generator_litres": litres,
            "generator_run_min": self.gen.run_steps * self.scenario.step_minutes,
            "energy_mwh": (alloc.total_mw - unserved) * dt_h,
            "it_energy_mwh": it_mwh,
            "grid_energy_mwh": grid_import * dt_h,
            "grid_co2_t": grid_co2,
            "generator_co2_t": litres * f.generators.co2_kg_per_l / 1000.0,
            "generator_nox_kg": gen_mw * dt_h * 1000 * f.generators.nox_g_per_kwh / 1000.0,
            "direct_water_l": it_mwh * 1000 * f.wue_l_per_kwh_it,
            "generation_water_l": gen_water_l,
            "generation_water_unknown": water_unknown,
            **{f"load_{b.id}": alloc.fractions.get(b.id, 0.0) for b in self.loads.blocks},
            "svc_demand_req_s": svc.demand_req_s,
            "svc_model": svc.model_label,
            "svc_model_id": svc.model_id,
            "svc_replicas": svc.replicas,
            "svc_capacity_req_s": svc.capacity_req_s,
            "svc_throughput_req_s": svc.throughput_req_s,
            "svc_queue": svc.queue_depth,
            "svc_rejected": svc.rejected_req,
            "svc_latency_s": svc.latency_s,
            "svc_gpu_util_pct": svc.utilization_pct,
            "svc_gpu_mem_pct": svc.gpu_memory_pct,
            "svc_quality": svc.quality,
            "svc_quality_text": QUALITY_TEXT[svc.quality],
            "svc_routed_small": svc.routed_to_small,
            "svc_concurrency_pct": svc.concurrency_pct,
            "active_alerts": len(self.recorder.active_alerts),
            "active_alert": self.recorder.active_alerts[-1].reason if self.recorder.active_alerts else "None",
        }
        self.steps.append(row)
        self.source_reasons.append(dict(reasons))
        self.load_reasons.append(dict(alloc.reasons))
        self.t += 1
        return row

    # --------------------------------------------------------- change events
    def _record_source_changes(self, reasons, reg, local_avail, gen_mw, bat_mw, ups_mw) -> None:
        # Observed MW: delivered output for storage/backup, usable output for local sources,
        # regional available capacity for grid sources.
        values = {"generator": gen_mw, "battery": bat_mw, "ups": ups_mw, **local_avail}
        for sid, why in reasons.items():
            if self._prev_source_reason.get(sid) == why:
                continue
            fuel = sid.replace("grid_", "")
            val = values.get(sid, reg.available_mw.get(fuel))
            sev = "warning" if why in (en.EXHAUSTED, en.UNAVAILABLE, en.UNREACHABLE, en.NOT_ISLAND_CAPABLE) else "info"
            self._rec(component=f"source/{sid}", severity=sev, event_type="source_status", reason=f"{why}: {en.REASON_TEXT[why]}",
                      observed_value=val, unit="MW", action="Dispatch updated", outcome=why)
            self._prev_source_reason[sid] = why

    def _record_load_changes(self, alloc: Allocation, svc) -> None:
        for b in self.loads.blocks:
            on = alloc.is_on(b.id)
            prev = self._prev_on.get(b.id, True)
            if on != prev:
                if not on:
                    crit = b.tier != "workload"
                    ev = self._rec(component=f"load/{b.id}", severity="critical" if crit else "warning",
                                   event_type="load_shed", reason=alloc.reasons.get(b.id, ""), observed_value=b.cost_mw(self.f.cooling_power_factor),
                                   unit="MW incl. cooling", action=f"{'Shut down' if crit else 'Paused'}: {b.label}", outcome="Off")
                    self._decision(f"{'Shut down' if crit else 'Pause'} {b.label}", "shutdown" if crit else "shed", ev, alloc.reasons.get(b.id, ""))
                else:
                    ev = self._rec(component=f"load/{b.id}", severity="notice", event_type="load_restored", reason=alloc.reasons.get(b.id, "Restored"),
                                   observed_value=b.cost_mw(self.f.cooling_power_factor), unit="MW incl. cooling", action=f"Restored: {b.label}", outcome="On")
                    self._decision(f"Restore {b.label}", "restore", ev, "Stable headroom")
            self._prev_on[b.id] = on
        # Routing is only meaningful while inference is served; during shutdown keep the last routing state.
        if svc.quality != UNAVAILABLE and svc.routed_to_small != self._prev_routed:
            ev = self._rec(component="ai/routing", severity="warning" if svc.routed_to_small else "notice", event_type="model_routing",
                           reason="Large-model capacity below routing threshold" if svc.routed_to_small else "Large-model capacity sufficient again",
                           observed_value=svc.large_capacity_req_s, unit="req/s (large-model capacity)",
                           threshold=round(svc.demand_req_s * self.f.service.route_small_threshold, 3),
                           action=f"Route to {self.f.service.small_model.label}" if svc.routed_to_small else f"Route to {self.f.service.large_model.label}",
                           outcome="Routed")
            self._decision(ev.action, "route", ev, ev.reason)
            self._prev_routed = svc.routed_to_small

    # --------------------------------------------------------------------- run
    def run(self) -> RunResult:
        while self.t < self.scenario.steps:
            self.step()
        return RunResult(self.run_id, self.scenario, self.steps, list(self.recorder.events),
                         dict(self.recorder.suppressed), self.source_reasons, self.load_reasons, self.decisions)


def run_scenario(reference: ReferenceModel, scenario: Scenario, run_id: str | None = None) -> RunResult:
    return SimulationEngine(reference, scenario, run_id).run()
