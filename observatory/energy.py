"""Regional dispatch and facility energy assets.

Power (MW) and energy (MWh) are tracked separately. Every source reports a
reason code for each step so the UI can explain why it is or is not supplying
the facility.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Battery, Generators, LocalSource, Region, Ups

# Reason codes shown whenever a source changes status.
DISPATCHED = "DISPATCHED"
AT_CAPACITY = "AT_CAPACITY"
STANDBY = "STANDBY"
UNAVAILABLE = "UNAVAILABLE"
EXHAUSTED = "EXHAUSTED"
UNREACHABLE = "UNREACHABLE"
NOT_ISLAND_CAPABLE = "NOT_ISLAND_CAPABLE"
POLICY_RESTRICTED = "POLICY_RESTRICTED"
STARTING = "STARTING"
RESERVE_HELD = "RESERVE_HELD"

REASON_TEXT = {
    DISPATCHED: "Supplying load",
    AT_CAPACITY: "Supplying load at its available limit",
    STANDBY: "Available but not needed",
    UNAVAILABLE: "Resource or plant unavailable",
    EXHAUSTED: "Stored energy or fuel exhausted",
    UNREACHABLE: "Not electrically connected to the islanded facility",
    NOT_ISLAND_CAPABLE: "Reachable but cannot form or follow an island",
    POLICY_RESTRICTED: "Held back by operating policy",
    STARTING: "Starting / synchronising",
    RESERVE_HELD: "Remaining energy reserved for core load",
}


def profile_value(profile: str, minutes: float, start_hour: float) -> float:
    """Deterministic resource profile in [0, 1]."""
    hour = (start_hour + minutes / 60.0) % 24
    if profile == "flat":
        return 1.0
    if profile == "wind":
        return max(0.0, 0.35 + 0.08 * math.sin(2 * math.pi * minutes / 180.0))
    if profile == "solar":
        # daylight 06:00–20:00, peak around 13:00
        if hour < 6 or hour > 20:
            return 0.0
        return max(0.0, 0.65 * math.sin(math.pi * (hour - 6) / 14.0))
    raise ValueError(f"unknown profile {profile}")


@dataclass(frozen=True)
class RegionalDispatch:
    demand_mw: float
    available_mw: dict[str, float]
    dispatched_mw: dict[str, float]
    price_eur_mwh: float
    shortfall_mw: float

    @property
    def total_available(self) -> float:
        return sum(self.available_mw.values())

    @property
    def total_dispatched(self) -> float:
        return sum(self.dispatched_mw.values())

    @property
    def margin(self) -> float:
        """Reserve margin: (available - demand) / demand."""
        return (self.total_available - self.demand_mw) / self.demand_mw

    def shares(self) -> dict[str, float]:
        total = self.total_dispatched
        if total <= 0:
            return {k: 0.0 for k in self.dispatched_mw}
        return {k: v / total for k, v in self.dispatched_mw.items()}


class RegionalGrid:
    """Merit-order dispatch over the five modelled regional sources."""

    def __init__(self, region: Region):
        self.region = region
        self.factors: dict[str, float] = {s.fuel: 1.0 for s in region.sources}
        self.demand_factor = 1.0

    def dispatch(self, minutes: float) -> RegionalDispatch:
        r = self.region
        demand = r.base_demand_mw * self.demand_factor * (
            1 + r.demand_swing * math.sin(2 * math.pi * (minutes + 120) / 480.0)
        )
        available = {
            s.fuel: s.capacity_mw * s.availability * self.factors[s.fuel] * profile_value(s.profile, minutes, r.start_hour)
            for s in r.sources
        }
        remaining = demand
        dispatched = {s.fuel: 0.0 for s in r.sources}
        price = 0.0
        for s in sorted(r.sources, key=lambda s: s.marginal_cost_eur_mwh):
            take = min(available[s.fuel], remaining)
            if take > 0:
                dispatched[s.fuel] = take
                remaining -= take
                price = s.marginal_cost_eur_mwh
        shortfall = max(0.0, remaining)
        if shortfall > 1e-9:
            price = r.scarcity_price_eur_mwh
        return RegionalDispatch(demand, available, dispatched, price, shortfall)


# ---------------------------------------------------------------- local assets


@dataclass
class BatteryState:
    spec: Battery
    soc: float = field(init=False)

    def __post_init__(self) -> None:
        self.soc = self.spec.initial_soc

    @property
    def energy_mwh(self) -> float:
        return self.soc * self.spec.energy_mwh

    def max_discharge_mw(self, dt_h: float, *, allow_reserve: bool) -> float:
        floor = 0.0 if allow_reserve else self.spec.reserve_soc
        usable = max(0.0, (self.soc - floor) * self.spec.energy_mwh)
        if self.spec.energy_mwh <= 0 or dt_h <= 0:
            return 0.0
        return min(self.spec.power_mw, usable / dt_h)

    def discharge(self, mw: float, dt_h: float) -> float:
        mw = max(0.0, min(mw, self.spec.power_mw, self.energy_mwh / dt_h if dt_h > 0 else 0.0))
        if self.spec.energy_mwh > 0:
            self.soc = max(0.0, self.soc - mw * dt_h / self.spec.energy_mwh)
        return mw

    def charge(self, mw: float, dt_h: float) -> float:
        if self.spec.energy_mwh <= 0:
            return 0.0
        room = (1.0 - self.soc) * self.spec.energy_mwh / self.spec.round_trip_efficiency
        mw = max(0.0, min(mw, self.spec.charge_mw, room / dt_h))
        self.soc = min(1.0, self.soc + mw * dt_h * self.spec.round_trip_efficiency / self.spec.energy_mwh)
        return mw


@dataclass
class GeneratorState:
    spec: Generators
    fuel_l: float = field(init=False)
    mode: str = "off"  # off | starting | running | exhausted
    start_counter: int = 0
    run_steps: int = 0

    def __post_init__(self) -> None:
        self.fuel_l = self.spec.fuel_l

    @property
    def fuel_pct(self) -> float:
        return 100.0 * self.fuel_l / self.spec.fuel_l if self.spec.fuel_l > 0 else 0.0

    def request_start(self) -> None:
        if self.mode == "off" and self.fuel_l > 0 and self.spec.units > 0:
            self.mode = "starting"
            self.start_counter = 0
            if self.spec.start_delay_steps == 0:
                self.mode = "running"

    def tick_start(self) -> None:
        if self.mode == "starting":
            self.start_counter += 1
            if self.start_counter >= self.spec.start_delay_steps:
                self.mode = "running"

    def stop(self) -> None:
        if self.mode in ("starting", "running"):
            self.mode = "off"

    def max_output_mw(self, dt_h: float) -> float:
        if self.mode != "running" or dt_h <= 0:
            return 0.0
        fuel_limited = self.fuel_l / (self.spec.consumption_l_per_mwh * dt_h)
        return min(self.spec.capacity_mw, fuel_limited)

    def run(self, mw: float, dt_h: float) -> tuple[float, float]:
        """Run at ``mw``; returns (delivered MW, fuel litres burned)."""
        mw = max(0.0, min(mw, self.max_output_mw(dt_h)))
        litres = mw * dt_h * self.spec.consumption_l_per_mwh
        self.fuel_l = max(0.0, self.fuel_l - litres)
        if mw > 0:
            self.run_steps += 1
        if self.fuel_l <= 1e-6:
            self.fuel_l = 0.0
            self.mode = "exhausted"
        return mw, litres


@dataclass
class UpsState:
    spec: Ups
    energy_mwh: float = field(init=False)

    def __post_init__(self) -> None:
        self.energy_mwh = self.spec.energy_mwh

    @property
    def soc(self) -> float:
        return self.energy_mwh / self.spec.energy_mwh if self.spec.energy_mwh > 0 else 0.0

    def max_discharge_mw(self, dt_h: float) -> float:
        if dt_h <= 0:
            return 0.0
        return min(self.spec.power_mw, self.energy_mwh / dt_h)

    def discharge(self, mw: float, dt_h: float) -> float:
        mw = max(0.0, min(mw, self.max_discharge_mw(dt_h)))
        self.energy_mwh = max(0.0, self.energy_mwh - mw * dt_h)
        return mw

    def recharge(self, mw: float, dt_h: float) -> float:
        room = self.spec.energy_mwh - self.energy_mwh
        mw = max(0.0, min(mw, self.spec.recharge_mw, room / dt_h if dt_h > 0 else 0.0))
        self.energy_mwh = min(self.spec.energy_mwh, self.energy_mwh + mw * dt_h)
        return mw


def local_source_status(
    src: LocalSource, available_mw: float, islanded: bool
) -> tuple[float, str]:
    """Return (usable MW, reason) for a facility-connected source.

    Islanding eligibility: once islanded, only sources that are both
    electrically reachable and island-capable may supply the facility.
    """
    if available_mw <= 1e-9:
        return 0.0, UNAVAILABLE
    if islanded and not src.island_reachable:
        return 0.0, UNREACHABLE
    if islanded and not src.island_capable:
        return 0.0, NOT_ISLAND_CAPABLE
    return available_mw, DISPATCHED


def grid_source_status_islanded() -> str:
    """Regional sources cannot reach an islanded facility."""
    return UNREACHABLE
