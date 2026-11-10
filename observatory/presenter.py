"""Pure view-model helpers: turn engine rows into the values the UI displays.

Keeping this outside Streamlit lets tests assert that the dashboard shows the
same state and values as the engine output.
"""

from __future__ import annotations

STATE_BADGE = {
    "Normal": ("🟢", "Normal"),
    "Capacity constrained": ("🟠", "Capacity constrained"),
    "Emergency": ("🔴", "Emergency"),
}

QUALITY_BADGE = {
    "FULL": "🟢 Full service",
    "REDUCED": "🟡 Reduced (smaller model)",
    "DEGRADED": "🟠 Degraded (slow / queueing)",
    "UNAVAILABLE": "⛔ Unavailable",
}


def power_source_label(row: dict) -> str:
    parts = []
    if row["grid_to_load_mw"] > 1e-6:
        parts.append(f"Grid {row['grid_to_load_mw']:.1f} MW")
    if row["supply_generator_mw"] > 1e-6:
        parts.append(f"Generator {row['supply_generator_mw']:.1f} MW")
    if row["supply_battery_mw"] > 1e-6:
        parts.append(f"Battery {row['supply_battery_mw']:.1f} MW")
    if row["supply_ups_mw"] > 1e-6:
        parts.append(f"UPS {row['supply_ups_mw']:.1f} MW")
    local = row.get("supply_onsite_solar_mw", 0) + row.get("supply_private_wire_wind_mw", 0)
    if local > 1e-6:
        parts.append(f"On-site renewables {local:.1f} MW")
    return " + ".join(parts) if parts else "No supply"


def status_cards(row: dict) -> list[dict]:
    icon, label = STATE_BADGE[row["state"]]
    lat = row["svc_latency_s"]
    return [
        {"title": "Operating state", "value": f"{icon} {label}", "detail": row["phase"], "metric": None},
        {"title": "Source of power", "value": power_source_label(row),
         "detail": "Grid-connected" if row["grid_connected"] else "Islanded", "metric": "available_supply"},
        {"title": "Available capacity", "value": f"{row['available_supply_mw']:.1f} MW",
         "detail": f"Serving {row['facility_load_mw']:.1f} of {row['facility_requested_mw']:.1f} MW requested", "metric": "available_supply"},
        {"title": "Cooling", "value": f"{row['cooling_headroom_pct']:.0f}% headroom",
         "detail": f"{row['it_load_mw']:.1f} / {row['cooling_capacity_mw']:.0f} MW IT heat", "metric": "cooling_headroom"},
        {"title": "Active alert", "value": row["active_alert"],
         "detail": f"{row['active_alerts']} active", "metric": None},
        {"title": "User-visible response", "value": QUALITY_BADGE[row["svc_quality"]],
         "detail": "No response" if lat is None else f"{lat:.1f} s · {row['svc_model']}", "metric": "latency"},
    ]
