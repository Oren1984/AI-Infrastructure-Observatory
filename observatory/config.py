"""Shared scenario schema.

Every number the simulation uses comes from a validated YAML file under
``data/``. Validation happens here, before the engine runs, so that invalid
inputs (negative capacities, reserve above capacity, unknown sources in an
event) fail early with a clear message instead of producing odd charts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

Classification = Literal["published", "assumption", "simulated"]


class _Strict(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}


# --------------------------------------------------------------------------- grid


class GridSource(_Strict):
    """A regional generation source that contributes to the modelled grid mix."""

    id: str
    label: str
    fuel: Literal["gas", "oil", "hydro", "wind", "solar"]
    capacity_mw: float = Field(ge=0)
    availability: float = Field(ge=0, le=1, description="Baseline available fraction")
    profile: Literal["flat", "wind", "solar"] = "flat"
    marginal_cost_eur_mwh: float = Field(ge=0)
    emission_tco2_mwh: float = Field(ge=0)
    water_consumption_l_mwh: float | None = Field(
        default=None, ge=0, description="None = unknown; excluded from totals and flagged"
    )


class Region(_Strict):
    name: str
    country: str
    system_operator_note: str
    base_demand_mw: float = Field(gt=0)
    demand_swing: float = Field(ge=0, lt=1)
    start_hour: float = Field(ge=0, lt=24)
    scarcity_price_eur_mwh: float = Field(gt=0)
    sources: list[GridSource]


# ----------------------------------------------------------------------- facility


class LocalSource(_Strict):
    """A source connected at the facility (behind the meter or private wire)."""

    id: str
    label: str
    fuel: Literal["solar", "wind"]
    capacity_mw: float = Field(ge=0)
    profile: Literal["flat", "wind", "solar"]
    island_reachable: bool
    island_capable: bool
    note: str = ""


class Battery(_Strict):
    power_mw: float = Field(ge=0)
    energy_mwh: float = Field(ge=0)
    initial_soc: float = Field(ge=0, le=1)
    reserve_soc: float = Field(ge=0, le=1)
    charge_mw: float = Field(ge=0)
    round_trip_efficiency: float = Field(gt=0, le=1)
    grid_support_allowed: bool
    island_capable: bool

    @model_validator(mode="after")
    def _reserve_below_initial(self) -> Battery:
        if self.reserve_soc > self.initial_soc:
            raise ValueError("battery.reserve_soc must not exceed battery.initial_soc")
        return self


class Generators(_Strict):
    units: int = Field(ge=0)
    unit_mw: float = Field(ge=0)
    start_delay_steps: int = Field(ge=0)
    fuel_l: float = Field(ge=0)
    consumption_l_per_mwh: float = Field(gt=0)
    co2_kg_per_l: float = Field(ge=0)
    nox_g_per_kwh: float = Field(ge=0)
    grid_support_allowed: bool
    start_on_island: bool

    @property
    def capacity_mw(self) -> float:
        return self.units * self.unit_mw


class Ups(_Strict):
    power_mw: float = Field(ge=0)
    energy_mwh: float = Field(ge=0)
    recharge_mw: float = Field(ge=0)


class CriticalItem(_Strict):
    id: str
    label: str
    mw: float = Field(ge=0)
    it_load: bool
    tier: Literal["core", "inference"]


class Workload(_Strict):
    id: str
    label: str
    it_mw: float = Field(ge=0)
    kind: Literal["interactive", "optional", "batch", "training"]
    fractional: bool = False


class ModelProfile(_Strict):
    id: str
    label: str
    replica_mw: float = Field(gt=0)
    req_per_s_per_replica: float = Field(gt=0)
    base_latency_s: float = Field(gt=0)
    weights_memory_pct: float = Field(ge=0, le=100)
    quality_note: str


class Service(_Strict):
    baseline_req_per_s: float = Field(ge=0)
    demand_swing: float = Field(ge=0, lt=1)
    slo_latency_s: float = Field(gt=0)
    max_queue: int = Field(ge=0)
    route_small_threshold: float = Field(gt=0, le=1)
    kv_cache_memory_pct: float = Field(ge=0, le=100)
    large_model: ModelProfile
    small_model: ModelProfile


class Thresholds(_Strict):
    demand_response_margin: float = Field(description="Regional reserve margin that triggers a demand-reduction instruction")
    demand_response_release_margin: float = Field(description="Margin at which the instruction is lifted (hysteresis)")
    grid_collapse_ratio: float = Field(gt=0, le=1, description="available/demand below which the grid is lost")
    reconnect_margin: float
    reconnect_stable_steps: int = Field(ge=1)
    restore_stable_steps: int = Field(ge=1, description="Steps of sufficient headroom before a tier is restored")
    cooling_headroom_alert_pct: float = Field(ge=0, le=100)
    battery_low_soc: float = Field(ge=0, le=1)
    fuel_low_pct: float = Field(ge=0, le=100)
    latency_alert_s: float = Field(gt=0)
    queue_alert: int = Field(ge=0)


class Facility(_Strict):
    name: str
    classification: Classification
    location_note: str
    grid_connection_mw: float = Field(ge=0)
    firm_import_mw: float = Field(ge=0, description="Import cap under a demand-reduction instruction")
    cooling_capacity_mw_it: float = Field(ge=0, description="Maximum IT heat the cooling plant can reject")
    cooling_power_factor: float = Field(ge=0, description="MW of cooling electricity per MW of IT load")
    wue_l_per_kwh_it: float = Field(ge=0)
    shed_discretionary_on_island: bool
    local_sources: list[LocalSource]
    battery: Battery
    generators: Generators
    ups: Ups
    critical_load: list[CriticalItem]
    workloads: list[Workload]
    service: Service
    thresholds: Thresholds

    @model_validator(mode="after")
    def _checks(self) -> Facility:
        if self.thresholds.demand_response_release_margin < self.thresholds.demand_response_margin:
            raise ValueError("demand_response_release_margin must be >= demand_response_margin")
        if self.firm_import_mw > self.grid_connection_mw:
            raise ValueError("firm_import_mw must not exceed grid_connection_mw")
        ids = [c.id for c in self.critical_load] + [w.id for w in self.workloads]
        if len(ids) != len(set(ids)):
            raise ValueError("critical_load and workload ids must be unique")
        if not any(c.tier == "inference" for c in self.critical_load):
            raise ValueError("critical_load needs at least one inference-tier item")
        return self


# ---------------------------------------------------------------------- scenarios


class ScenarioEvent(_Strict):
    step: int = Field(ge=0)
    kind: Literal["source_availability", "cooling_capacity", "demand_factor", "note"]
    target: str = ""
    value: float = 0.0
    reason: str


class Scenario(_Strict):
    id: str
    label: str
    state_intent: Literal["normal", "constrained", "emergency"]
    summary: str
    steps: int = Field(gt=0, le=2000)
    step_minutes: float = Field(gt=0)
    probe_step: int = Field(ge=0)
    events: list[ScenarioEvent] = []

    @model_validator(mode="after")
    def _probe_in_range(self) -> Scenario:
        if self.probe_step >= self.steps:
            raise ValueError("probe_step must be inside the run")
        for e in self.events:
            if e.step >= self.steps:
                raise ValueError(f"event at step {e.step} is outside the run")
        return self


class ReferenceModel(_Strict):
    version: str
    region: Region
    facility: Facility


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_reference(path: Path | None = None) -> ReferenceModel:
    return ReferenceModel.model_validate(_read_yaml(path or DATA_DIR / "reference_facility.yaml"))


def load_scenarios(directory: Path | None = None) -> dict[str, Scenario]:
    directory = directory or DATA_DIR / "scenarios"
    order = {"normal": 0, "constrained": 1, "emergency": 2}
    scenarios = [Scenario.model_validate(_read_yaml(p)) for p in sorted(directory.glob("*.yaml"))]
    scenarios.sort(key=lambda s: order.get(s.state_intent, 9))
    return {s.id: s for s in scenarios}


def validate_scenario_targets(reference: ReferenceModel, scenario: Scenario) -> None:
    """Fail when an event references a source that does not exist."""
    fuels = {s.fuel for s in reference.region.sources}
    for e in scenario.events:
        if e.kind == "source_availability" and e.target not in fuels:
            raise ValueError(f"scenario {scenario.id}: unknown source fuel '{e.target}'")
