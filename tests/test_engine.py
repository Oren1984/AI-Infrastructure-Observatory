from __future__ import annotations

import pytest

from observatory import energy as en
from observatory.config import Battery, Scenario, load_reference, load_scenarios
from observatory.engine import EMERGENCY, run_scenario
from observatory.events import EventRecorder
from observatory.loads import LoadManager
from observatory.presenter import status_cards


@pytest.fixture(scope="module")
def ref():
    return load_reference()


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios()


@pytest.fixture(scope="module")
def runs(ref, scenarios):
    return {sid: run_scenario(ref, s, run_id=f"test-{sid}") for sid, s in scenarios.items()}


# ------------------------------------------------------------- dispatch limits
def test_dispatch_respects_power_and_energy_limits(ref, runs):
    f = ref.facility
    for r in runs.values():
        for row in r.steps:
            assert row["supply_battery_mw"] <= f.battery.power_mw + 1e-9
            assert 0 <= row["battery_soc_pct"] <= 100 + 1e-9
            assert row["supply_generator_mw"] <= f.generators.capacity_mw + 1e-9
            assert row["generator_fuel_l"] >= 0
            assert row["supply_ups_mw"] <= f.ups.power_mw + 1e-9
            assert row["grid_import_mw"] <= row["import_cap_mw"] + 1e-9
            assert row["facility_load_mw"] <= row["available_supply_mw"] + 1e-9
            assert row["it_load_mw"] <= row["cooling_capacity_mw"] + 1e-9
            for fuel in ("gas", "oil", "hydro", "wind", "solar"):
                assert row[f"regional_{fuel}_mw"] <= row[f"regional_avail_{fuel}_mw"] + 1e-9


def test_storage_and_fuel_never_go_negative():
    b = en.BatteryState(Battery(power_mw=5, energy_mwh=1, initial_soc=0.5, reserve_soc=0.1, charge_mw=1,
                                round_trip_efficiency=0.9, grid_support_allowed=True, island_capable=True))
    for _ in range(100):
        b.discharge(5, 1 / 60)
    assert b.soc == pytest.approx(0.0)
    assert b.max_discharge_mw(1 / 60, allow_reserve=True) == 0


def test_zero_capacity_is_explicit(ref):
    lm = LoadManager(ref.facility)
    alloc = lm.allocate(budget_mw=0, reserve_core_mw=0, thermal_capacity_mw=30)
    assert not alloc.core_ok and not alloc.inference_ok
    assert alloc.total_mw == 0
    assert all(v == 0 for v in alloc.fractions.values())
    with pytest.raises(ValueError):
        lm.allocate(budget_mw=-1, reserve_core_mw=0, thermal_capacity_mw=30)


# ------------------------------------------------------- islanding eligibility
def test_islanding_eligibility_rules(ref):
    solar, wind = ref.facility.local_sources
    assert en.local_source_status(wind, 2.0, islanded=False) == (2.0, en.DISPATCHED)
    assert en.local_source_status(wind, 2.0, islanded=True) == (0.0, en.NOT_ISLAND_CAPABLE)
    assert en.local_source_status(solar, 0.5, islanded=True) == (0.5, en.DISPATCHED)
    assert en.local_source_status(solar, 0.0, islanded=True) == (0.0, en.UNAVAILABLE)


def test_no_grid_supply_while_islanded(runs):
    r = runs["emergency"]
    island = [(row, reasons) for row, reasons in zip(r.steps, r.source_reasons, strict=True) if not row["grid_connected"]]
    assert island, "emergency scenario must island"
    for row, reasons in island:
        assert row["grid_import_mw"] == 0
        for sid in ("grid_gas", "grid_oil", "grid_hydro", "grid_wind", "grid_solar"):
            assert reasons[sid] == en.UNREACHABLE


def test_cascade_order_and_reason_codes(runs):
    r = runs["emergency"]
    first_unavailable = {}
    for row, reasons in zip(r.steps, r.source_reasons, strict=True):
        for sid in ("grid_oil", "grid_hydro", "grid_wind", "grid_solar"):
            if reasons[sid] == en.UNAVAILABLE and sid not in first_unavailable:
                first_unavailable[sid] = row["step"]
    order = sorted(first_unavailable, key=first_unavailable.get)
    assert order == ["grid_oil", "grid_hydro", "grid_wind", "grid_solar"]
    statuses = [e for e in r.events if e.event_type == "source_status"]
    assert any("UNREACHABLE" in e.reason for e in statuses)
    assert any("EXHAUSTED" in e.reason and e.component == "source/generator" for e in statuses)


# ----------------------------------------------------------- critical load order
def test_shedding_follows_priority_order(ref):
    lm = LoadManager(ref.facility)
    full = lm.full_demand_mw()
    crit = lm.critical_budget_mw()
    seen_off = []
    for budget in [full - i * 0.5 for i in range(int(full / 0.5) + 2)]:
        alloc = lm.allocate(budget_mw=max(budget, 0), reserve_core_mw=0, thermal_capacity_mw=30)
        for b in reversed(lm.blocks):  # same-step shutdowns listed in shutdown order
            if not alloc.is_on(b.id) and b.id not in seen_off:
                seen_off.append(b.id)
    # discretionary first (training, batch, optional), interactive next, then critical chain from the end
    assert seen_off[:3] == ["training", "batch", "optional"]
    assert seen_off[3] == "interactive_full"
    assert seen_off[4:] == ["accelerators_min", "serving_nodes", "storage", "network_security", "observability", "power_distribution"]
    assert crit < full


def test_inference_gated_on_critical_chain(ref):
    lm = LoadManager(ref.facility)
    core = lm.critical_budget_mw("core")
    alloc = lm.allocate(budget_mw=core + 0.01, reserve_core_mw=0, thermal_capacity_mw=30)
    assert alloc.core_ok
    assert not alloc.inference_ok
    assert all(not alloc.is_on(w.id) for w in ref.facility.workloads)


def test_battery_reserve_only_serves_core(ref):
    lm = LoadManager(ref.facility)
    alloc = lm.allocate(budget_mw=0, reserve_core_mw=50, thermal_capacity_mw=30)
    assert alloc.core_ok and not alloc.inference_ok


# ------------------------------------------------------------ replay & alerts
def test_replay_is_repeatable(ref, scenarios):
    for s in scenarios.values():
        a = run_scenario(ref, s, run_id="a" * 32)
        b = run_scenario(ref, s, run_id="b" * 32)
        assert a.steps == b.steps
        strip = lambda evs: [{k: v for k, v in e.as_dict().items() if k not in ("run_id", "event_id")} for e in evs]
        assert strip(a.events) == strip(b.events)


def test_alert_deduplication_and_recovery():
    rec = EventRecorder("r1")
    kw = dict(component="x", severity="warning", reason="too hot", sim_time="t")
    for i in range(5):
        rec.alert("hot", True, step=i, **kw)
    rec.alert("hot", False, step=5, **kw)
    rec.alert("hot", False, step=6, **kw)
    types = [e.event_type for e in rec.events]
    assert types == ["alert", "recovery"]
    assert rec.suppressed["hot"] == 4


def test_engine_alerts_not_duplicated(runs):
    r = runs["emergency"]
    island_alerts = [e for e in r.events if e.event_type == "alert" and e.reason == "Facility islanded from grid"]
    assert len(island_alerts) == 1
    assert any(e.event_type == "recovery" and "islanded" in e.reason for e in r.events)


# -------------------------------------------------------- shutdown & recovery
def test_emergency_walkthrough(runs):
    r = runs["emergency"]
    phases = [row["phase"] for row in r.steps]
    for p in ["UPS bridging / generator starting", "Islanded on generator", "Islanded on battery",
              "Controlled shutdown", "Staged recovery"]:
        assert p in phases, p
    # order of phases
    idx = [phases.index(p) for p in ["UPS bridging / generator starting", "Islanded on generator",
                                      "Islanded on battery", "Controlled shutdown", "Staged recovery"]]
    assert idx == sorted(idx)
    qualities = {row["svc_quality"] for row in r.steps}
    assert {"FULL", "REDUCED", "UNAVAILABLE"} <= qualities
    assert any(row["supply_ups_mw"] > 0 for row in r.steps)
    assert any(row["state"] == EMERGENCY for row in r.steps)


def test_staged_recovery_restores_in_priority_order(runs, ref):
    r = runs["emergency"]
    shutdown_step = next(row["step"] for row in r.steps if row["phase"] == "Controlled shutdown")
    restored = [e.component.split("/")[1] for e in r.events if e.event_type == "load_restored" and e.step > shutdown_step]
    order = ["serving_nodes", "accelerators_min", "interactive_full", "optional", "batch", "training"]
    assert [x for x in restored if x in order] == order
    last = r.steps[-1]
    assert last["state"] == "Normal"
    assert all(last[f"load_{b}"] == 1.0 for b in order)


def test_constrained_keeps_interactive_and_sheds_discretionary(runs):
    r = runs["constrained"]
    probe = r.at(r.scenario.probe_step)
    assert probe["state"] == "Capacity constrained"
    assert probe["load_training"] == 0
    assert probe["inference_ok"]
    assert probe["svc_quality"] != "UNAVAILABLE"


def test_normal_runs_everything(runs):
    for row in runs["normal"].steps:
        assert row["state"] == "Normal"
        assert row["svc_quality"] == "FULL"


# --------------------------------------------------------------- validation
def test_scenario_validation_rejects_bad_inputs(ref):
    with pytest.raises(ValueError):
        Scenario(id="x", label="x", state_intent="normal", summary="", steps=10, step_minutes=1, probe_step=20)
    with pytest.raises(ValueError):
        Battery(power_mw=1, energy_mwh=1, initial_soc=0.1, reserve_soc=0.5, charge_mw=1,
                round_trip_efficiency=0.9, grid_support_allowed=True, island_capable=True)
    bad = Scenario(id="x", label="x", state_intent="normal", summary="", steps=10, step_minutes=1, probe_step=1,
                   events=[{"step": 1, "kind": "source_availability", "target": "coal", "value": 0, "reason": "?"}])
    with pytest.raises(ValueError):
        run_scenario(ref, bad)


# ------------------------------------------------------------ UI consistency
def test_status_cards_match_engine_output(runs):
    for r in runs.values():
        for step in (0, r.scenario.probe_step, len(r.steps) - 1):
            row = r.at(step)
            cards = {c["title"]: c for c in status_cards(row)}
            assert row["state"] in cards["Operating state"]["value"]
            assert cards["Operating state"]["detail"] == row["phase"]
            assert cards["Available capacity"]["value"] == f"{row['available_supply_mw']:.1f} MW"
            assert cards["Active alert"]["value"] == row["active_alert"]


def test_supply_by_source_matches_served_load(runs):
    for r in runs.values():
        for row in r.steps:
            delivered = sum(v for k, v in row.items() if k.startswith("supply_") and k.endswith("_mw"))
            assert delivered == pytest.approx(row["facility_load_mw"] - row["unserved_mw"], abs=1e-6)
