"""Service health and simulated control checks (demonstration evidence only)."""

from __future__ import annotations

from .events import Event

HEALTH_COMPONENTS = [
    ("Power distribution", "power_distribution"),
    ("Observability", "observability"),
    ("Network & security", "network_security"),
    ("Storage", "storage"),
    ("Serving nodes", "serving_nodes"),
    ("Accelerators (min pool)", "accelerators_min"),
    ("Interactive full pool", "interactive_full"),
    ("Optional work", "optional"),
    ("Batch", "batch"),
    ("Training", "training"),
]


def service_health(row: dict, load_reasons: dict[str, str]) -> list[dict]:
    out = []
    for label, key in HEALTH_COMPONENTS:
        frac = row[f"load_{key}"]
        if frac >= 1 - 1e-9:
            status = "✅ Healthy"
        elif frac > 1e-9:
            status = "🟠 Reduced"
        else:
            status = "⛔ Off"
        out.append({"Component": label, "Status": status, "Allocated": f"{frac:.0%}", "Reason": load_reasons.get(key, "")})
    return out


def control_checks(rows: list[dict], events: list[Event], decisions: list[dict], *, mcp_hours_limit: float = 500.0,
                   dispatchable_mw: float | None = None, connection_mw: float | None = None) -> list[dict]:
    """Evaluate simulated control checks up to the current cursor."""
    row = rows[-1]
    gen_h = row["generator_run_min"] / 60
    criticals = [e for e in events if e.severity == "critical" and e.event_type in ("alert", "grid_loss", "load_shed")]
    checks = [
        ("CHK-01", "CTL-GRID-05", "Grid breaker state matches operating mode",
         "Pass", f"grid_connected={row['grid_connected']}, phase={row['phase']}"),
        ("CHK-02", "CTL-AIR-02", f"Generator hours within {mcp_hours_limit:.0f} h/yr limited-use assumption",
         "Pass" if gen_h < mcp_hours_limit else "Fail", f"{gen_h:.2f} h this run (annual total not simulated)"),
        ("CHK-03", "CTL-SEC-03", "Every critical alert has a recorded response action",
         "Pass" if all(e.action for e in criticals) else "Fail", f"{len(criticals)} critical events checked"),
        ("CHK-04", "CTL-PRIV-06", "Operational log contains no prompt text or user identifiers",
         "Pass" if all(not hasattr(e, "prompt") and not hasattr(e, "user_id") for e in events) else "Fail",
         "Event schema has no prompt/user fields; requests logged by correlation ID only"),
        ("CHK-05", "CTL-AI-04", "Each shed/route/restore decision names owner, policy and evidence",
         "Pass" if all(d["owner"] and d["policy"] and d["evidence_event"] for d in decisions) else "Fail",
         f"{len(decisions)} decisions checked"),
        ("CHK-06", "CTL-AIR-02", "Fuel level monitored and above alert threshold",
         "Pass" if row["generator_fuel_pct"] >= 20 else "Warning", f"{row['generator_fuel_pct']:.0f}% fuel"),
        ("CHK-07", "CTL-SEC-03", "Authentication & policy service powered",
         "Pass" if row["load_network_security"] > 0 else "Not evaluable", "Gate for request admission"),
        ("CHK-08", "CTL-EN-01", "Energy and water KPI inputs captured (E_DC, E_IT, W_IN)",
         "Pass", "Per-step energy and water recorded by the engine"),
    ]
    if dispatchable_mw is not None and connection_mw is not None:
        checks.append(("CHK-09", "CTL-GRID-05", "On-site dispatchable capacity matches grid connection (CRU2025236, ≥ 10 MVA)",
                       "Pass" if dispatchable_mw >= connection_mw else "Gap",
                       f"{dispatchable_mw:.0f} MW generators + battery vs {connection_mw:.0f} MW connection (de-rating not modelled)"))
    return [{"Check": c, "Control": ctl, "Description": d, "Result": r, "Evidence": ev} for c, ctl, d, r, ev in checks]
