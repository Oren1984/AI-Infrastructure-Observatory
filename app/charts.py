"""Plotly figure builders. Pure functions of engine output."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Colour-blind-aware palette (Okabe–Ito based). Colour is always paired with a label.
SUPPLY_SERIES = [
    ("supply_grid_gas_mw", "Grid · natural gas", "#D55E00"),
    ("supply_grid_oil_mw", "Grid · oil-derived", "#8C510A"),
    ("supply_grid_hydro_mw", "Grid · hydro", "#0072B2"),
    ("supply_grid_wind_mw", "Grid · wind", "#56B4E9"),
    ("supply_grid_solar_mw", "Grid · solar", "#E6C229"),
    ("supply_onsite_solar_mw", "On-site solar", "#F0E442"),
    ("supply_private_wire_wind_mw", "Private-wire wind", "#9ECAE1"),
    ("supply_generator_mw", "Standby generator (diesel)", "#4D4D4D"),
    ("supply_battery_mw", "Battery", "#009E73"),
    ("supply_ups_mw", "UPS (bridging)", "#CC79A7"),
]
REGIONAL_SERIES = [
    ("regional_wind_mw", "Wind", "#56B4E9"),
    ("regional_solar_mw", "Solar", "#E6C229"),
    ("regional_hydro_mw", "Hydro", "#0072B2"),
    ("regional_gas_mw", "Natural gas", "#D55E00"),
    ("regional_oil_mw", "Oil-derived", "#8C510A"),
]
STATE_COLORS = {"Normal": "#2E7D32", "Capacity constrained": "#E67E22", "Emergency": "#C0392B"}
EVIDENCE_STYLE = {
    "observed": ("#0072B2", "circle", "Observed (published)"),
    "announced": ("#E69F00", "diamond", "Announced (legislated / planned)"),
    "hypothetical": ("#7F7F7F", "square-open", "Hypothetical (forward scenario)"),
}

FONT = dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=13, color="#1B2631")


def _layout(fig: go.Figure, title: str, y_title: str, height: int = 340) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        height=height,
        margin=dict(l=10, r=10, t=48, b=10),
        font=FONT,
        legend=dict(orientation="h", yanchor="top", y=-0.22, x=0, font=dict(size=11)),
        hovermode="x unified",
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    fig.update_yaxes(title_text=y_title, gridcolor="#E5E8EB", zeroline=False)
    fig.update_xaxes(title_text="Simulated minutes (T+)", gridcolor="#F1F3F5")
    return fig


def _cursor(fig: go.Figure, cursor: int, row=None, col=None) -> None:
    kwargs = dict(line_width=2, line_dash="dot", line_color="#1B2631")
    if row is not None:
        fig.add_vline(x=cursor, row=row, col=col, **kwargs)
    else:
        fig.add_vline(x=cursor, annotation_text="now", annotation_position="top", **kwargs)


def _state_bands(fig: go.Figure, df: pd.DataFrame) -> None:
    """Shade constrained/emergency periods so state is visible without colour alone (labelled)."""
    start, cur = 0, df["state"].iloc[0]
    states = list(df["state"]) + [None]
    for i, s in enumerate(states[1:], start=1):
        if s != cur:
            if cur != "Normal":
                fig.add_vrect(x0=start, x1=i - 1, fillcolor=STATE_COLORS[cur], opacity=0.07, line_width=0,
                              annotation_text=cur, annotation_position="top left", annotation_font_size=10)
            start, cur = i, s


def power_stack(df: pd.DataFrame, cursor: int) -> go.Figure:
    fig = go.Figure()
    x = df["step"]
    for col, label, color in SUPPLY_SERIES:
        if col in df and df[col].abs().sum() > 1e-6:
            fig.add_trace(go.Scatter(x=x, y=df[col], name=label, stackgroup="supply", mode="lines",
                                     line=dict(width=0.5, color=color), fillcolor=color,
                                     hovertemplate=f"{label}: %{{y:.2f}} MW<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=df["facility_requested_mw"], name="Facility demand (requested)", mode="lines",
                             line=dict(color="#1B2631", width=2, dash="dash"), hovertemplate="Requested: %{y:.1f} MW<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=df["available_supply_mw"], name="Available supply", mode="lines",
                             line=dict(color="#7B1FA2", width=2), hovertemplate="Available: %{y:.1f} MW<extra></extra>"))
    _state_bands(fig, df)
    _cursor(fig, cursor)
    return _layout(fig, "Power supplied to the facility by source (MW)", "Power (MW)", 400)


def regional_mix(df: pd.DataFrame, cursor: int) -> go.Figure:
    fig = go.Figure()
    for col, label, color in REGIONAL_SERIES:
        fig.add_trace(go.Scatter(x=df["step"], y=df[col] / 1000, name=label, stackgroup="r", mode="lines",
                                 line=dict(width=0.5, color=color), fillcolor=color,
                                 hovertemplate=f"{label}: %{{y:.2f}} GW<extra></extra>"))
    fig.add_trace(go.Scatter(x=df["step"], y=df["regional_demand_mw"] / 1000, name="Regional demand", mode="lines",
                             line=dict(color="#1B2631", width=2, dash="dash")))
    fig.add_trace(go.Scatter(x=df["step"], y=df["regional_available_mw"] / 1000, name="Regional available", mode="lines",
                             line=dict(color="#7B1FA2", width=2)))
    _cursor(fig, cursor)
    return _layout(fig, "Modelled regional generation mix (GW)", "Power (GW)", 380)


def storage_and_fuel(df: pd.DataFrame, cursor: int) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=df["step"], y=df["battery_soc_pct"], name="Battery SoC (%)", line=dict(color="#009E73", width=2)), secondary_y=False)
    fig.add_trace(go.Scatter(x=df["step"], y=df["ups_soc_pct"], name="UPS energy (%)", line=dict(color="#CC79A7", width=2, dash="dot")), secondary_y=False)
    fig.add_trace(go.Scatter(x=df["step"], y=df["generator_fuel_pct"], name="Generator fuel (%)", line=dict(color="#4D4D4D", width=2, dash="dash")), secondary_y=False)
    fig.add_trace(go.Scatter(x=df["step"], y=df["battery_energy_mwh"], name="Battery energy (MWh)", line=dict(color="#009E73", width=1), opacity=0.5), secondary_y=True)
    _cursor(fig, cursor)
    _layout(fig, "Stored energy and fuel (energy, not power)", "Percent of rated (%)", 380)
    fig.update_yaxes(title_text="Battery energy (MWh)", secondary_y=True, showgrid=False)
    return fig


def service_panel(df: pd.DataFrame, cursor: int, slo: float) -> go.Figure:
    fig = make_subplots(rows=2, cols=2, subplot_titles=("Latency (s)", "Throughput vs demand (req/s)",
                                                        "Queue depth (requests)", "GPU utilization & memory (%)"),
                        vertical_spacing=0.18, horizontal_spacing=0.08)
    x = df["step"]
    fig.add_trace(go.Scatter(x=x, y=df["svc_latency_s"], name="Mean latency", line=dict(color="#0F6E8C")), 1, 1)
    fig.add_hline(y=slo, row=1, col=1, line_dash="dash", line_color="#C0392B", annotation_text=f"SLO {slo:.0f} s", annotation_font_size=10)
    fig.add_trace(go.Scatter(x=x, y=df["svc_demand_req_s"], name="Demand", line=dict(color="#1B2631", dash="dash")), 1, 2)
    fig.add_trace(go.Scatter(x=x, y=df["svc_throughput_req_s"], name="Throughput", line=dict(color="#009E73")), 1, 2)
    fig.add_trace(go.Scatter(x=x, y=df["svc_capacity_req_s"], name="Capacity", line=dict(color="#7B1FA2", dash="dot")), 1, 2)
    fig.add_trace(go.Scatter(x=x, y=df["svc_queue"], name="Queue", line=dict(color="#E67E22"), fill="tozeroy"), 2, 1)
    fig.add_trace(go.Scatter(x=x, y=df["svc_gpu_util_pct"], name="GPU utilization", line=dict(color="#0072B2")), 2, 2)
    fig.add_trace(go.Scatter(x=x, y=df["svc_gpu_mem_pct"], name="GPU memory", line=dict(color="#CC79A7", dash="dot")), 2, 2)
    for r in (1, 2):
        for c in (1, 2):
            _cursor(fig, cursor, r, c)
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=40, b=10), font=FONT, hovermode="x unified",
                      legend=dict(orientation="h", y=-0.12, font=dict(size=11)), plot_bgcolor="#FFFFFF")
    fig.update_yaxes(gridcolor="#E5E8EB")
    return fig


def cooling_chart(df: pd.DataFrame, cursor: int) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["step"], y=df["cooling_capacity_mw"], name="Cooling capacity (MW IT heat)", line=dict(color="#0072B2", width=2)))
    fig.add_trace(go.Scatter(x=df["step"], y=df["it_load_mw"], name="IT heat load (MW)", line=dict(color="#D55E00", width=2), fill="tozeroy"))
    _cursor(fig, cursor)
    return _layout(fig, "Cooling capacity vs IT heat load", "MW (thermal, IT heat)", 320)


def trace_chart(trace) -> go.Figure:
    colors = {"ok": "#2E7D32", "slow": "#E67E22", "blocked": "#C0392B", "skipped": "#BDC3C7"}
    icons = {"ok": "✔", "slow": "⏱", "blocked": "✖", "skipped": "–"}
    labels = [s.label for s in trace.stages][::-1]
    vals = [max(s.latency_ms, 8) if s.status != "skipped" else 8 for s in trace.stages][::-1]
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h",
        marker_color=[colors[s.status] for s in trace.stages][::-1],
        text=[f"{icons[s.status]} {s.status.upper()} · {s.latency_ms:,.0f} ms" for s in trace.stages][::-1],
        textposition="auto", hovertext=[s.note for s in trace.stages][::-1], hoverinfo="text",
    ))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=40, b=10), font=FONT, showlegend=False,
                      title=dict(text=f"Request {trace.request_id} — stage timing (log scale)", font=dict(size=15)),
                      plot_bgcolor="#FFFFFF")
    fig.update_xaxes(type="log", title_text="Stage time (ms, log scale)", gridcolor="#E5E8EB")
    return fig


def comparison_bars(summary: pd.DataFrame, metric: str, label: str, unit: str) -> go.Figure:
    fig = go.Figure(go.Bar(x=summary["Scenario"], y=summary[metric],
                           marker_color=[STATE_COLORS.get(s, "#0F6E8C") for s in summary["Intent"]],
                           text=[f"{v:,.2f}" if isinstance(v, float) else str(v) for v in summary[metric]], textposition="outside"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=44, b=10), font=FONT, plot_bgcolor="#FFFFFF",
                      title=dict(text=f"{label} ({unit})", font=dict(size=14)))
    fig.update_yaxes(gridcolor="#E5E8EB", title_text=unit)
    return fig


def capital_chart(items: list[dict]) -> go.Figure:
    items = sorted(items, key=lambda i: i["value"])
    fig = go.Figure(go.Bar(x=[i["value"] for i in items], y=[i["category"] for i in items], orientation="h",
                           marker_color="#0F6E8C", text=[f"€{i['value']:,.1f}m" for i in items], textposition="auto"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=44, b=10), font=FONT, plot_bgcolor="#FFFFFF",
                      title=dict(text="Capital cost by category — assumption (EUR million, 2025 nominal)", font=dict(size=14)))
    fig.update_xaxes(title_text="EUR million", gridcolor="#E5E8EB")
    return fig


def timeline_chart(milestones: list[dict]) -> go.Figure:
    fig = go.Figure()
    for ev, (color, symbol, label) in EVIDENCE_STYLE.items():
        ms = [m for m in milestones if m["evidence"] == ev]
        if not ms:
            continue
        fig.add_trace(go.Scatter(
            x=[m["date"] for m in ms], y=[m["category"].replace("_", " ") for m in ms], mode="markers",
            name=label, marker=dict(size=14, color=color, symbol=symbol, line=dict(width=2, color=color)),
            text=[m["title"] for m in ms], hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra>" + label + "</extra>"))
    fig.add_vline(x="2026-11-10", line_dash="dot", line_color="#1B2631")
    fig.add_annotation(x="2026-11-10", y=1.04, yref="paper", text="catalog date", showarrow=False, font=dict(size=10))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=44, b=10), font=FONT, plot_bgcolor="#FFFFFF",
                      title=dict(text="2023–2030 milestones by evidence class (separate from the incident clock)", font=dict(size=15)),
                      legend=dict(orientation="h", y=-0.15))
    fig.update_xaxes(range=["2023-01-01", "2030-12-31"], gridcolor="#E5E8EB", title_text="Calendar date")
    fig.update_yaxes(gridcolor="#F1F3F5")
    return fig
