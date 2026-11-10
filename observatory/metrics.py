"""Metric registry with lineage.

Each displayed number has a definition: formula, inputs, unit, source,
reference date, classification and assumptions. ``explain`` returns the input
values actually used, so the UI can show the calculation behind the number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .catalog import Costs


@dataclass(frozen=True)
class MetricDef:
    id: str
    label: str
    unit: str
    scope: str  # "step" (value at cursor) or "run" (cumulative up to cursor)
    formula: str
    inputs: tuple[str, ...]
    classification: str
    source_ids: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    reference_date: str = "Simulated run"
    boundary: str = ""
    fn: Callable[[list[dict], Costs], float | None] = field(default=lambda rows, c: None, repr=False, compare=False)


def _sum(rows, key):
    return sum(r[key] for r in rows)


def _last(rows, key):
    return rows[-1][key] if rows else None


def _ratio(a, b):
    return a / b if b else None


def _p95(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]


SIM = ("sim-engine", "ds-reference-facility", "ds-scenarios")

METRICS: dict[str, MetricDef] = {m.id: m for m in [
    MetricDef("available_supply", "Available supply", "MW", "step",
              "grid import cap + usable local sources + generator (if running) + battery (above reserve) + UPS (bridging) [+ battery reserve for core load when islanded]",
              ("import_cap_mw", "supply_onsite_solar_mw", "generator_mode", "battery_soc_pct", "available_supply_mw"), "simulated", SIM,
              ("Battery below reserve SoC may only serve core load.", "UPS counts only while generators start."),
              fn=lambda r, c: _last(r, "available_supply_mw")),
    MetricDef("facility_load", "Facility load served", "MW", "step",
              "IT load + cooling power (IT × cooling factor) + non-IT critical load",
              ("it_load_mw", "cooling_power_mw", "facility_load_mw", "facility_requested_mw"), "simulated", SIM,
              ("Cooling power factor 0.22 MW per MW IT.",), fn=lambda r, c: _last(r, "facility_load_mw")),
    MetricDef("latency", "Chat latency (mean)", "s", "step",
              "base latency × (1 + 0.2·ρ/(1−ρ)) + queue / capacity, with ρ = demand / capacity (capped at 0.98)",
              ("svc_demand_req_s", "svc_capacity_req_s", "svc_queue", "svc_model", "svc_latency_s"), "simulated", SIM,
              ("Queueing approximation, not a measured trace.",), fn=lambda r, c: _last(r, "svc_latency_s")),
    MetricDef("throughput", "Throughput", "req/s", "step", "min(backlog, capacity × step) / step length",
              ("svc_demand_req_s", "svc_capacity_req_s", "svc_throughput_req_s"), "simulated", SIM,
              fn=lambda r, c: _last(r, "svc_throughput_req_s")),
    MetricDef("queue", "Queue depth", "requests", "step", "queue(t) = min(max_queue, queue(t−1) + demand·Δt − served)",
              ("svc_queue", "svc_rejected"), "simulated", SIM, ("Max queue 3,000 requests; overflow is rejected.",),
              fn=lambda r, c: _last(r, "svc_queue")),
    MetricDef("gpu_util", "Accelerator utilization (interactive pool)", "%", "step", "throughput / capacity × 100",
              ("svc_throughput_req_s", "svc_capacity_req_s", "svc_gpu_util_pct"), "simulated", SIM,
              fn=lambda r, c: _last(r, "svc_gpu_util_pct")),
    MetricDef("gpu_mem", "GPU memory in use", "%", "step", "model weights % + KV-cache % × utilization",
              ("svc_model", "svc_gpu_util_pct", "svc_gpu_mem_pct"), "simulated", SIM,
              ("Weights: 55% (large) / 25% (small); KV cache up to 35%.",), fn=lambda r, c: _last(r, "svc_gpu_mem_pct")),
    MetricDef("cooling_headroom", "Cooling headroom", "%", "step", "(cooling capacity − IT load) / cooling capacity × 100",
              ("cooling_capacity_mw", "it_load_mw", "cooling_headroom_pct"), "simulated", SIM,
              ("Cooling capacity expressed as MW of IT heat rejected.",), fn=lambda r, c: _last(r, "cooling_headroom_pct")),
    MetricDef("battery_soc", "Battery state of charge", "%", "step", "SoC(t) = SoC(t−1) − P·Δt / E_rated (floored at 0)",
              ("supply_battery_mw", "battery_energy_mwh", "battery_soc_pct"), "simulated", SIM,
              ("6 MW / 6 MWh; reserve 20% for core load.",), fn=lambda r, c: _last(r, "battery_soc_pct")),
    MetricDef("fuel", "Generator fuel remaining", "%", "step", "fuel(t) = fuel(t−1) − P·Δt × 270 L/MWh (floored at 0)",
              ("supply_generator_mw", "generator_fuel_l", "generator_fuel_pct"), "simulated", SIM,
              ("4,000 L on site; no resupply during the run.",), fn=lambda r, c: _last(r, "generator_fuel_pct")),
    MetricDef("pue", "PUE (run window)", "ratio", "run", "PUE = E_DC / E_IT  (EU 2024/1364, Annex III)",
              ("energy_mwh", "it_energy_mwh"), "simulated", SIM + ("eu-2024-1364",),
              ("Short run window, not the annual reporting period.",), boundary="Facility meter: IT + cooling + distribution.",
              fn=lambda r, c: _ratio(_sum(r, "energy_mwh"), _sum(r, "it_energy_mwh"))),
    MetricDef("wue", "WUE (run window)", "L/kWh", "run", "WUE = W_IN / E_IT  (EU 2024/1364, Annex III; m³/MWh = L/kWh)",
              ("direct_water_l", "it_energy_mwh"), "simulated", SIM + ("eu-2024-1364",),
              ("Direct water use assumed 0.25 L per kWh of IT energy.",), boundary="On-site cooling water input only.",
              fn=lambda r, c: _ratio(_sum(r, "direct_water_l") / 1000, _sum(r, "it_energy_mwh"))),
    MetricDef("energy", "Facility energy", "MWh", "run", "Σ served load × Δt", ("energy_mwh",), "simulated", SIM,
              fn=lambda r, c: _sum(r, "energy_mwh")),
    MetricDef("grid_co2", "Grid electricity emissions (location-based)", "t CO₂", "run",
              "Σ grid import × Δt × Σ_i (share_i × EF_i)", ("grid_energy_mwh", "grid_co2_t"), "simulated", SIM,
              ("Grid emission factors gas 0.40, oil 0.75, renewables 0 t/MWh are scenario assumptions (combustion only), not published grid factors.",),
              boundary="Scope 2, location-based; excludes generator fuel to avoid double counting.",
              fn=lambda r, c: _sum(r, "grid_co2_t")),
    MetricDef("gen_co2", "Generator emissions", "t CO₂", "run", "Σ fuel litres × 2.70 kg/L ÷ 1000",
              ("generator_litres", "generator_co2_t"), "simulated", SIM + ("epa-ghg-hub-2025",),
              ("Diesel factor 2.70 kg CO₂/L is published (EPA 2025: 10.21 kg/gal); fuel burn 270 L/MWh is an assumption.",),
              boundary="Scope 1, on-site combustion only.", fn=lambda r, c: _sum(r, "generator_co2_t")),
    MetricDef("gen_nox", "Generator NOx (local air pollution)", "kg", "run", "Σ generator MWh × 1000 × 6 g/kWh ÷ 1000",
              ("supply_generator_mw", "generator_nox_kg"), "assumption", SIM,
              ("NOx factor 6 g/kWh is a labelled assumption, not a measured or permitted value.",),
              boundary="Local air pollutant, reported separately from greenhouse gases.",
              fn=lambda r, c: _sum(r, "generator_nox_kg")),
    MetricDef("gen_hours", "Generator run time", "h", "run", "steps with generator output × Δt",
              ("generator_run_min",), "simulated", SIM, fn=lambda r, c: _last(r, "generator_run_min") / 60 if r else None),
    MetricDef("direct_water", "Direct facility water consumption", "m³", "run", "Σ IT energy (kWh) × 0.25 L/kWh ÷ 1000",
              ("it_energy_mwh", "direct_water_l"), "assumption", SIM,
              ("Withdrawal is not modelled separately and is UNKNOWN.",), boundary="On-site cooling consumption.",
              fn=lambda r, c: _sum(r, "direct_water_l") / 1000),
    MetricDef("generation_water", "Water consumed in electricity generation (est.)", "m³", "run",
              "Σ grid MWh × share_i × water factor_i ÷ 1000 (unknown factors excluded)",
              ("grid_energy_mwh", "generation_water_l", "generation_water_unknown"), "assumption", SIM,
              ("Gas 750 L/MWh, oil 1,100 L/MWh, wind/solar 0; hydro evaporation UNKNOWN and excluded.",),
              boundary="Off-site, upstream; consumption only.", fn=lambda r, c: _sum(r, "generation_water_l") / 1000),
    MetricDef("inference_availability", "Inference available", "% of time", "run",
              "steps with quality ≠ UNAVAILABLE ÷ steps × 100", ("svc_quality",), "simulated", SIM,
              fn=lambda r, c: 100 * sum(1 for x in r if x["svc_quality"] != "UNAVAILABLE") / len(r) if r else None),
    MetricDef("p95_latency", "p95 chat latency", "s", "run", "95th percentile of per-step mean latency (served steps)",
              ("svc_latency_s",), "simulated", SIM, fn=lambda r, c: _p95([x["svc_latency_s"] for x in r])),
    MetricDef("renewable_share", "Renewable share of grid import", "%", "run",
              "Σ (wind + solar + hydro import) ÷ Σ grid import × 100",
              ("supply_grid_wind_mw", "supply_grid_solar_mw", "supply_grid_hydro_mw", "grid_to_load_mw"), "simulated", SIM,
              fn=lambda r, c: _ratio(100 * sum(x["supply_grid_wind_mw"] + x["supply_grid_solar_mw"] + x["supply_grid_hydro_mw"] for x in r),
                                     sum(x["grid_to_load_mw"] for x in r))),
    MetricDef("cost_electricity", "Electricity cost", "EUR", "run", "Σ grid MWh × modelled marginal price",
              ("grid_energy_mwh", "price_eur_mwh"), "simulated", SIM + ("ds-costs",),
              ("Energy component only; network and capacity charges excluded.",),
              fn=lambda r, c: sum(x["grid_energy_mwh"] * x["price_eur_mwh"] for x in r)),
    MetricDef("cost_fuel", "Generator fuel cost", "EUR", "run", "Σ litres × EUR 1.45/L", ("generator_litres",), "assumption",
              SIM + ("ds-costs",), fn=lambda r, c: _sum(r, "generator_litres") * c.operating.diesel_eur_per_l),
    MetricDef("cost_water", "Direct water cost", "EUR", "run", "direct water m³ × EUR 2.80/m³", ("direct_water_l",), "assumption",
              SIM + ("ds-costs",), fn=lambda r, c: _sum(r, "direct_water_l") / 1000 * c.operating.water_eur_per_m3),
    MetricDef("cost_maintenance", "Generator maintenance", "EUR", "run", "generator run hours × EUR 135/h", ("generator_run_min",),
              "assumption", SIM + ("ds-costs",),
              fn=lambda r, c: (_last(r, "generator_run_min") or 0) / 60 * c.operating.generator_maintenance_eur_per_run_hour),
]}


def evaluate(metric_id: str, rows: list[dict], costs: Costs):
    return METRICS[metric_id].fn(rows, costs)


def summarize(rows: list[dict], costs: Costs, ids: list[str] | None = None) -> dict[str, float | None]:
    return {m: evaluate(m, rows, costs) for m in (ids or [k for k, v in METRICS.items() if v.scope == "run"])}


def explain(metric_id: str, rows: list[dict], costs: Costs) -> dict:
    m = METRICS[metric_id]
    if m.scope == "step":
        inputs = {k: rows[-1].get(k) for k in m.inputs} if rows else {}
    else:
        inputs = {}
        for k in m.inputs:
            vals = [r.get(k) for r in rows]
            if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals if v is not None):
                nums = [v for v in vals if v is not None]
                inputs[f"Σ {k}" if k not in ("generator_run_min",) else k] = sum(nums) if k != "generator_run_min" else (nums[-1] if nums else None)
            else:
                inputs[k] = f"{len(vals)} step values"
    return {"metric": m, "value": evaluate(metric_id, rows, costs), "inputs": inputs}
