"""Behind the Chat — AI Infrastructure Observatory (Streamlit entry point).

The UI only reads engine output. A run is computed once per (session, run ID)
and the playback cursor indexes into it, so reruns never duplicate
transitions or alerts.
"""

from __future__ import annotations

import html
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import charts  # noqa: E402
from observatory.catalog import load_catalogs  # noqa: E402
from observatory.config import load_reference, load_scenarios  # noqa: E402
from observatory.energy import REASON_TEXT  # noqa: E402
from observatory.engine import run_scenario  # noqa: E402
from observatory.events import EventRecorder, events_to_csv, events_to_json  # noqa: E402
from observatory.metrics import METRICS, explain, summarize  # noqa: E402
from observatory.observability import control_checks, service_health  # noqa: E402
from observatory.presenter import QUALITY_BADGE, status_cards  # noqa: E402
from observatory.trace import DEFAULT_PROMPT, build_trace, record_request  # noqa: E402

st.set_page_config(page_title="Behind the Chat — AI Infrastructure Observatory", page_icon="⚡",
                   layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1500px;}
.obs-card {border:1px solid #D5DDE3; border-radius:10px; padding:10px 14px; background:#F7F9FA; min-height:104px; margin-bottom:10px;}
.obs-card h4 {font-size:0.74rem; letter-spacing:.05em; text-transform:uppercase; color:#4A5A68; margin:0 0 4px 0; padding:0;}
.obs-card .v {font-size:1.02rem; font-weight:650; color:#1B2631; line-height:1.3;}
.obs-card .d {font-size:.82rem; color:#4A5A68; margin-top:3px;}
.obs-flow {display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:4px 0 8px 0;}
.obs-chip {border:1px solid #D5DDE3; border-radius:999px; padding:4px 10px; font-size:.84rem; background:#FFFFFF;}
.obs-arrow {color:#7F8C8D; font-weight:700;}
.obs-banner {background:#EEF5F8; border-left:4px solid #0F6E8C; padding:8px 12px; border-radius:6px; font-size:.86rem; color:#1B2631;}
.obs-muted {color:#566573; font-size:.85rem;}
@media (max-width: 640px) { .obs-card {min-height:auto;} }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------- data
@st.cache_resource
def load_all():
    return load_reference(), load_scenarios(), load_catalogs()


@st.cache_resource
def comparison_runs():
    ref, scs, _ = load_all()
    return {sid: run_scenario(ref, s, run_id="comparison" + "0" * 22) for sid, s in scs.items()}


REF, SCENARIOS, CAT = load_all()
SRC = CAT.sources.by_id()
ss = st.session_state


def new_run(sid: str, play: bool = False) -> None:
    run = run_scenario(REF, SCENARIOS[sid], run_id=uuid.uuid4().hex)
    ss.run = run
    ss.scenario_id = sid
    ss.cursor = 0
    ss.playing = play
    ss.traces = []
    ss.req_recorder = EventRecorder(run.run_id)


if "run" not in ss:
    # Optional deep link: ?scenario=emergency&t=100 (validated; anything else falls back to defaults)
    qp = st.query_params
    sid0 = qp.get("scenario", "normal")
    sid0 = sid0 if sid0 in SCENARIOS else "normal"
    ss.speed = 2
    new_run(sid0)
    try:
        ss.cursor = max(0, min(int(qp.get("t", 0)), SCENARIOS[sid0].steps - 1))
    except ValueError:
        ss.cursor = 0

run = ss.run
LAST = len(run.steps) - 1


# ----------------------------------------------------------------- header
st.markdown("## ⚡ Behind the Chat — AI Infrastructure Observatory")
st.markdown(
    "<div class='obs-banner'>One chat request, one fictional facility (<b>ORF-1</b>, Dublin region, Ireland), three operating "
    "states. <b>Telemetry is simulated</b>; capacities and prices are <b>scenario assumptions</b>; regulatory and statistical "
    "references are <b>published sources</b> with their verification status in the Evidence catalog.</div>",
    unsafe_allow_html=True)
st.write("")

# --------------------------------------------------------------- controls
c = st.columns([2.3, 1.1, 1.0, 0.9, 0.9, 0.9, 1.1])
labels = {sid: s.label for sid, s in SCENARIOS.items()}
selected = c[0].selectbox("Scenario", list(SCENARIOS), index=list(SCENARIOS).index(ss.scenario_id),
                          format_func=lambda s: labels[s], help="Same request and workload profile in every scenario.")
if selected != ss.scenario_id:
    new_run(selected)
    st.rerun()
c[1].write("")
if c[1].button("▶ Run scenario", type="primary", width="stretch", help="Start a fresh run of the selected scenario and play it"):
    new_run(selected, play=True)
    st.rerun()
c[2].write("")
if c[2].button("⏸ Pause" if ss.playing else "⏵ Play", width="stretch"):
    ss.playing = not ss.playing and ss.cursor < LAST
    st.rerun()
c[3].write("")
if c[3].button("⏭ Step", width="stretch", help="Advance one simulated minute"):
    ss.playing = False
    ss.cursor = min(LAST, ss.cursor + 1)
    st.rerun()
c[4].write("")
if c[4].button("↺ Reset", width="stretch", help="New run ID, clock back to T+0, request log cleared"):
    new_run(ss.scenario_id)
    st.rerun()
c[5].write("")
if c[5].button("⟲ Replay", width="stretch", help="Replay this run from T+0 (identical values)"):
    ss.cursor = 0
    ss.playing = True
    st.rerun()
ss.speed = c[6].selectbox("Playback speed", [1, 2, 5, 10], index=[1, 2, 5, 10].index(ss.speed),
                          format_func=lambda v: f"{v} min / tick")

# A number input + progress bar is used instead of st.slider: the slider thumb renders
# mirrored when the browser locale is right-to-left (e.g. Hebrew), which misreads the clock.
def _jump() -> None:
    ss.cursor = int(ss.clock_input)
    ss.playing = False


ss.clock_input = ss.cursor
k1, k2 = st.columns([1, 4])
k1.number_input("Simulation clock (T+ minutes)", min_value=0, max_value=LAST, step=1, key="clock_input", on_change=_jump,
                help="Jump to any simulated minute. The 2023–2030 timeline is a separate calendar.")
k2.write("")
k2.progress(ss.cursor / LAST if LAST else 1.0, text=f"T+{ss.cursor} of {LAST} min")

cursor = ss.cursor
row = run.at(cursor)
rows_to_cursor = run.steps[: cursor + 1]
load_reasons = run.load_reasons[cursor]
source_reasons = run.source_reasons[cursor]
df = pd.DataFrame(run.steps)
st.markdown(
    f"<span class='obs-muted'>Run <code>{run.run_id[:8]}</code> · {html.escape(run.scenario.label)} · "
    f"{html.escape(row['sim_time'])} · {'▶ playing' if ss.playing else '⏸ paused'}</span>", unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
def card(title: str, value: str, detail: str) -> str:
    return (f"<div class='obs-card' role='group' aria-label='{html.escape(title)}'><h4>{html.escape(title)}</h4>"
            f"<div class='v'>{html.escape(value)}</div><div class='d'>{html.escape(detail)}</div></div>")


def fmt(v, digits=2):
    if v is None:
        return "Unknown"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,.{digits}f}"
    return str(v)


def metric_panel(key: str, default: str = "available_supply") -> None:
    """Metric detail panel: input data, calculation, units, source, date, assumptions."""
    ids = list(METRICS)
    mid = st.selectbox("Explain a number", ids, index=ids.index(default), key=f"metric_{key}",
                       format_func=lambda m: f"{METRICS[m].label} ({METRICS[m].unit})")
    ex = explain(mid, rows_to_cursor, CAT.costs)
    m = ex["metric"]
    a, b = st.columns([1, 1.3])
    with a:
        st.metric(f"{m.label}", f"{fmt(ex['value'])} {m.unit}",
                  help="Value at the current clock position" if m.scope == "step" else "Cumulative from T+0 to the current clock position")
        st.markdown(f"**Calculation** — `{m.formula}`")
        st.markdown(f"**Classification:** {m.classification} · **Scope:** {'value at cursor' if m.scope == 'step' else 'run to cursor'}")
        st.markdown(f"**Reference date:** {m.reference_date} ({row['sim_time']})")
        if m.boundary:
            st.markdown(f"**Calculation boundary:** {m.boundary}")
        for asm in m.assumptions:
            st.markdown(f"- Assumption: {asm}")
    with b:
        st.markdown("**Input data**")
        st.dataframe(pd.DataFrame([{"Input": k, "Value": fmt(v, 3)} for k, v in ex["inputs"].items()]),
                     hide_index=True, width="stretch")
        st.markdown("**Sources**")
        st.dataframe(pd.DataFrame([{
            "Source": SRC[s].title, "Class": SRC[s].classification, "Verification": SRC[s].verification_status,
            "Edition": SRC[s].edition, "URL": SRC[s].url or "—"} for s in m.source_ids]),
            hide_index=True, width="stretch",
            column_config={"URL": st.column_config.LinkColumn("URL")})


def all_events() -> list:
    evs = run.events_until(cursor) + [e for e in ss.req_recorder.events if e.step <= cursor]
    return sorted(evs, key=lambda e: (e.step, e.event_id))


tabs = st.tabs(["🏠 Overview", "🔎 Request trace", "⚡ Power & facility", "🧠 AI service",
                "❄ Cooling, environment & community", "💶 Cost & governance", "🛡 Security & observability",
                "⚖ Scenario comparison", "📅 2023–2030", "📚 Evidence catalog", "❓ Help Center"])

# ================================================================ OVERVIEW
with tabs[0]:
    left, right = st.columns([1, 1.45], gap="large")
    with left:
        st.markdown("#### 💬 Simulated chat")
        prompt = st.text_area("Your request (same request in every scenario)", value=DEFAULT_PROMPT, height=90, key="prompt")
        b1, b2 = st.columns([1, 1.6])
        if b1.button("📨 Send request", type="primary", width="stretch"):
            ss.req_seq = ss.get("req_seq", 0) + 1
            tr = build_trace(row, run.run_id, ss.req_seq, load_reasons)
            record_request(ss.req_recorder, tr, len(prompt))
            ss.traces.append(tr)
        b2.caption("The prompt is shown here only. The event log stores a correlation ID, never the text.")
        if ss.traces:
            tr = ss.traces[-1]
            with st.chat_message("user"):
                st.write(prompt)
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(f"**{QUALITY_BADGE.get(tr.outcome, tr.outcome)}** · `{tr.request_id}` · {tr.sim_time}")
                st.text(tr.response_text)
                st.caption(f"Model: {tr.model} · Latency: " + ("no response" if tr.total_latency_s is None else f"{tr.total_latency_s:.2f} s") + " · see the Request trace tab")
        else:
            st.info("Press **Send request** to send the prefilled request at the current simulation time.")
        st.markdown("#### What this run shows")
        st.write(run.scenario.summary)

    with right:
        st.markdown("#### 🏭 Infrastructure right now")
        cards = status_cards(row)
        for i in range(0, 6, 3):
            cols = st.columns(3)
            for col, cd in zip(cols, cards[i:i + 3]):
                col.markdown(card(cd["title"], cd["value"], cd["detail"]), unsafe_allow_html=True)
        chain = "all powered" if row["inference_ok"] else ("core only" if row["core_ok"] else "down")
        chips = [
            f"⚡ Power: {'grid' if row['grid_connected'] else 'islanded'} · {row['facility_load_mw']:.1f} MW",
            f"❄ Cooling: {row['cooling_headroom_pct']:.0f}% headroom",
            f"🧱 Critical chain: {chain}",
            f"🧠 {row['svc_model']} · {row['svc_replicas']} replicas",
            f"👤 User: {QUALITY_BADGE[row['svc_quality']]}",
        ]
        st.markdown("<div class='obs-flow' aria-label='Path from facility to user'>" +
                    "<span class='obs-arrow'>→</span>".join(f"<span class='obs-chip'>{html.escape(x)}</span>" for x in chips) +
                    "</div>", unsafe_allow_html=True)
    st.plotly_chart(charts.power_stack(df, cursor), width="stretch", key="ov_power")
    e1, e2 = st.columns([1.3, 1])
    with e1:
        st.markdown("#### 🔔 Latest events")
        recent = [e for e in all_events() if e.event_type != "source_status"][-8:][::-1]
        st.dataframe(pd.DataFrame([{"Time": e.sim_time.split(" ")[0], "Severity": e.severity.upper(), "Component": e.component,
                                    "Event": e.event_type, "Reason": e.reason} for e in recent]),
                     hide_index=True, width="stretch")
    with e2:
        st.markdown("#### 🧮 Metric detail")
        metric_panel("overview")

# ============================================================ REQUEST TRACE
with tabs[1]:
    st.markdown("#### Live request trace")
    if not ss.traces:
        st.info("No request yet. Send the prefilled request from the **Overview** tab; each request gets a correlation ID.")
    else:
        ids = [t.request_id for t in ss.traces][::-1]
        pick = st.selectbox("Correlation ID", ids)
        tr = next(t for t in ss.traces if t.request_id == pick)
        st.markdown(f"**Outcome:** {QUALITY_BADGE.get(tr.outcome, tr.outcome)} · **Sent at:** {tr.sim_time} · "
                    f"**Model:** {tr.model} · **End-to-end:** " + ("no response" if tr.total_latency_s is None else f"{tr.total_latency_s:.2f} s"))
        st.plotly_chart(charts.trace_chart(tr), width="stretch", key="trace_chart")
        trow = run.at(tr.step)
        lr = run.load_reasons[tr.step]
        st.dataframe(pd.DataFrame([{
            "Stage": s.label, "Status": {"ok": "✔ ok", "slow": "⏱ slow", "blocked": "✖ blocked", "skipped": "– skipped"}[s.status],
            "Time (ms)": round(s.latency_ms, 1), "Physical dependencies": ", ".join(s.dependencies), "Note": s.note} for s in tr.stages]),
            hide_index=True, width="stretch")
        st.markdown("##### Physical dependencies at the time of the request")
        effects = {
            "power_distribution": "Without distribution nothing downstream is powered.",
            "observability": "Without minimum observability operators cannot see or act safely.",
            "network_security": "Ingress, authentication and policy checks stop.",
            "storage": "Model weights and session state cannot be read.",
            "serving_nodes": "Requests cannot be routed or scheduled.",
            "accelerators_min": "No inference capacity; the service must return 'unavailable'.",
            "interactive_full": "Fewer replicas: lower concurrency, more queueing, or routing to the smaller model.",
        }
        deps = [{"Dependency": k.replace("_", " "), "Powered": "✅ yes" if trow[f"load_{k}"] >= 1 - 1e-9 else
                 ("🟠 partial" if trow[f"load_{k}"] > 0 else "⛔ no"), "Allocated": f"{trow[f'load_{k}']:.0%}",
                 "Reason": lr.get(k, ""), "Effect when constrained": v} for k, v in effects.items()]
        deps.append({"Dependency": "cooling", "Powered": "✅ yes" if trow["cooling_headroom_pct"] > 0 else "⛔ no",
                     "Allocated": f"{trow['cooling_headroom_pct']:.0f}% headroom", "Reason": "",
                     "Effect when constrained": "IT load is capped at the cooling plant's heat-rejection capacity."})
        st.dataframe(pd.DataFrame(deps), hide_index=True, width="stretch")

# ======================================================== POWER & FACILITY
with tabs[2]:
    st.markdown("#### Power and facility")
    ind = st.columns(5)
    gen_reason = source_reasons.get("generator", "")
    indicators = [
        ("Grid connection", "🟢 Connected" if row["grid_connected"] else "🔴 Islanded",
         f"Import cap {row['import_cap_mw']:.0f} MW" + (" (demand-reduction instruction)" if row["dr_active"] else "")),
        ("Local dispatch", f"{row.get('supply_onsite_solar_mw', 0) + row.get('supply_private_wire_wind_mw', 0):.2f} MW",
         f"Solar: {source_reasons.get('onsite_solar')} · Wind: {source_reasons.get('private_wire_wind')}"),
        ("UPS", f"{row['supply_ups_mw']:.1f} MW · {row['ups_soc_pct']:.0f}%", source_reasons.get("ups", "")),
        ("Battery", f"{row['supply_battery_mw']:.1f} MW · {row['battery_soc_pct']:.0f}% SoC", source_reasons.get("battery", "")),
        ("Generator", f"{row['supply_generator_mw']:.1f} MW · fuel {row['generator_fuel_pct']:.0f}%", f"{row['generator_mode']} · {gen_reason}"),
    ]
    for col, (t, v, d) in zip(ind, indicators):
        col.markdown(card(t, v, d), unsafe_allow_html=True)
    st.plotly_chart(charts.power_stack(df, cursor), width="stretch", key="pw_power")
    p1, p2 = st.columns(2)
    p1.plotly_chart(charts.regional_mix(df, cursor), width="stretch", key="pw_region")
    p2.plotly_chart(charts.storage_and_fuel(df, cursor), width="stretch", key="pw_storage")
    rr = pd.DataFrame(rows_to_cursor)
    en_cols = st.columns(4)
    en_cols[0].metric("Facility energy to now", f"{rr['energy_mwh'].sum():.2f} MWh")
    en_cols[1].metric("Grid energy to now", f"{rr['grid_energy_mwh'].sum():.2f} MWh")
    en_cols[2].metric("Generator energy to now", f"{(rr['supply_generator_mw'].sum() * run.scenario.step_minutes / 60):.2f} MWh")
    en_cols[3].metric("Battery discharge to now", f"{(rr['supply_battery_mw'].sum() * run.scenario.step_minutes / 60):.2f} MWh")
    st.markdown("##### Source status and reason codes")
    names = {s.id: s.label for s in REF.region.sources} | {s.id: s.label for s in REF.facility.local_sources} | {
        "generator": "Standby generators", "battery": "Battery", "ups": "UPS"}
    st.dataframe(pd.DataFrame([{"Source": names.get(k, k), "Reason code": v, "Meaning": REASON_TEXT.get(v, "")}
                               for k, v in source_reasons.items()]), hide_index=True, width="stretch")
    st.caption("MW = power (rate) · MWh = energy (amount). Regional sources contribute to the grid mix only while the facility "
               "is grid-connected; once islanded, only reachable and island-capable local assets may supply it.")

# ============================================================== AI SERVICE
with tabs[3]:
    st.markdown("#### AI service")
    m = st.columns(5)
    m[0].metric("Model profile", row["svc_model"].split(" (")[0])
    m[1].metric("Latency", f"{fmt(row['svc_latency_s'], 1)} s")
    m[2].metric("Throughput", f"{row['svc_throughput_req_s']:.1f} req/s", f"demand {row['svc_demand_req_s']:.1f}", delta_color="off")
    m[3].metric("Queue depth", f"{row['svc_queue']:.0f}")
    m[4].metric("GPU util / memory", f"{row['svc_gpu_util_pct']:.0f}% / {row['svc_gpu_mem_pct']:.0f}%")
    st.plotly_chart(charts.service_panel(df, cursor, REF.facility.service.slo_latency_s), width="stretch", key="svc")
    wl = []
    for w in REF.facility.workloads:
        frac = row[f"load_{w.id}"]
        reason = load_reasons.get(w.id, "")
        if frac >= 1 - 1e-9:
            status = "🟢 Active"
        elif frac > 0:
            status = "🟡 Active (reduced concurrency)"
        elif "Critical chain" in reason:
            status = "⛔ Suspended"
        else:
            status = "⏸ Deferred"
        wl.append({"Workload": w.label, "Status": status, "Allocated": f"{frac:.0%}", "IT power": f"{w.it_mw * frac:.2f} / {w.it_mw:.1f} MW", "Reason": reason})
    st.markdown("##### Workloads: active, deferred, suspended")
    st.dataframe(pd.DataFrame(wl), hide_index=True, width="stretch")
    lm, sm = REF.facility.service.large_model, REF.facility.service.small_model
    st.markdown("##### Configured model profiles (assumptions)")
    st.dataframe(pd.DataFrame([{"Profile": p.label, "MW per replica": p.replica_mw, "req/s per replica": p.req_per_s_per_replica,
                                "Base latency (s)": p.base_latency_s, "Weights memory (%)": p.weights_memory_pct, "Quality": p.quality_note}
                               for p in (lm, sm)]), hide_index=True, width="stretch")

# ============================================ COOLING / ENVIRONMENT / COMMUNITY
with tabs[4]:
    st.markdown("#### Cooling, environment and community")
    st.plotly_chart(charts.cooling_chart(df, cursor), width="stretch", key="cool")
    s = summarize(rows_to_cursor, CAT.costs)
    unknown_water = any(r["generation_water_unknown"] for r in rows_to_cursor)
    g = st.columns(3)
    g[0].metric("Cooling headroom now", f"{row['cooling_headroom_pct']:.0f}%")
    g[1].metric("Direct facility water (consumption)", f"{s['direct_water']:.1f} m³", help="Withdrawal: UNKNOWN (not modelled)")
    g[2].metric("Water in electricity generation (est.)", f"{s['generation_water']:.1f} m³" + (" + unknown" if unknown_water else ""),
                help="Hydro reservoir evaporation is unknown and excluded")
    g = st.columns(3)
    g[0].metric("Grid electricity emissions", f"{s['grid_co2']:.2f} t CO₂", help="Location-based, imported electricity only")
    g[1].metric("Generator emissions", f"{s['gen_co2']:.2f} t CO₂", help="On-site diesel combustion; not double counted with grid")
    g[2].metric("Generator run time", f"{s['gen_hours']:.2f} h")
    st.markdown("##### Comparison (run to now)")
    comp = pd.DataFrame([
        {"Quantity": "Greenhouse gas — grid electricity", "Value": f"{s['grid_co2']:.2f}", "Unit": "t CO₂", "Boundary": "Scope 2 location-based", "Class": "simulated"},
        {"Quantity": "Greenhouse gas — generators", "Value": f"{s['gen_co2']:.2f}", "Unit": "t CO₂", "Boundary": "Scope 1 on-site", "Class": "simulated"},
        {"Quantity": "Local air pollution — generator NOx", "Value": f"{s['gen_nox']:.1f}", "Unit": "kg", "Boundary": "On-site; assumed 6 g/kWh", "Class": "assumption"},
        {"Quantity": "Direct water consumption", "Value": f"{s['direct_water']:.1f}", "Unit": "m³", "Boundary": "On-site cooling", "Class": "assumption"},
        {"Quantity": "Direct water withdrawal", "Value": "Unknown", "Unit": "m³", "Boundary": "Not modelled", "Class": "unknown"},
        {"Quantity": "Generation water consumption", "Value": f"{s['generation_water']:.1f}" + (" (+ unknown hydro)" if unknown_water else ""), "Unit": "m³", "Boundary": "Upstream, off-site", "Class": "assumption"},
        {"Quantity": "Noise at site boundary", "Value": "Unknown", "Unit": "dB(A)", "Boundary": "No acoustic data", "Class": "unknown"},
        {"Quantity": "Local air quality (ambient)", "Value": "Unknown", "Unit": "µg/m³", "Boundary": "No monitoring data", "Class": "unknown"},
    ])
    st.dataframe(comp, hide_index=True, width="stretch")
    dr_min = sum(1 for r in rows_to_cursor if r["dr_active"]) * run.scenario.step_minutes
    st.markdown(f"**Community and grid effects (simulated):** the facility followed a grid demand-reduction instruction for "
                f"**{dr_min:.0f} min** of this run, releasing up to "
                f"**{REF.facility.grid_connection_mw - REF.facility.firm_import_mw:.0f} MW** to the regional system. "
                "Local air quality and noise effects are shown only as an assumption-labelled NOx estimate or as explicit unknowns.")

# ========================================================= COST & GOVERNANCE
with tabs[5]:
    st.markdown("#### Cost and governance")
    costs = CAT.costs
    s = summarize(rows_to_cursor, costs)
    k = st.columns(4)
    k[0].metric("Electricity (energy)", f"€{s['cost_electricity']:,.0f}")
    k[1].metric("Generator fuel", f"€{s['cost_fuel']:,.0f}")
    k[2].metric("Direct water", f"€{s['cost_water']:,.0f}")
    k[3].metric("Generator maintenance", f"€{s['cost_maintenance']:,.0f}")
    st.caption(f"Currency {costs.currency} · price basis {costs.price_basis} · location {costs.location} · period: run to now "
               f"({cursor + 1} min). Electricity: {costs.operating.electricity['basis']}. Diesel €{costs.operating.diesel_eur_per_l}/L, "
               f"water €{costs.operating.water_eur_per_m3}/m³, maintenance €{costs.operating.generator_maintenance_eur_per_run_hour}/run-hour "
               "— all scenario assumptions.")
    c1, c2 = st.columns([1.3, 1])
    c1.plotly_chart(charts.capital_chart([i.model_dump() for i in costs.capital.items]), width="stretch", key="capex")
    with c2:
        st.markdown("##### Annual fixed operating (assumption, EUR million / year)")
        st.dataframe(pd.DataFrame([i.model_dump() for i in costs.annual_fixed_operating.items]), hide_index=True, width="stretch")
        st.markdown("##### Policies → owners → controls")
        st.dataframe(pd.DataFrame([p.model_dump() for p in CAT.governance.policies]), hide_index=True, width="stretch")
    st.markdown("##### Decision log (operational decisions tied to owner, policy and evidence)")
    dec = [d for d in run.decisions if d["step"] <= cursor]
    if dec:
        st.dataframe(pd.DataFrame(dec)[["sim_time", "decision", "detail", "owner", "policy", "evidence_event"]],
                     hide_index=True, width="stretch")
    else:
        st.info("No operational decisions yet in this run.")
    with st.expander("Metric detail — cost and KPI calculations"):
        metric_panel("cost", "pue")

# ================================================== SECURITY & OBSERVABILITY
with tabs[6]:
    st.markdown("#### Security and observability")
    h1, h2 = st.columns([1, 1.2])
    with h1:
        st.markdown("##### Service health")
        st.dataframe(pd.DataFrame(service_health(row, load_reasons)), hide_index=True, width="stretch")
    with h2:
        st.markdown("##### Simulated control checks (demonstration evidence, not a compliance verdict)")
        st.dataframe(pd.DataFrame(control_checks(rows_to_cursor, all_events(), [d for d in run.decisions if d["step"] <= cursor])),
                     hide_index=True, width="stretch")
    st.markdown("##### Active alerts")
    active = row["active_alerts"]
    if active:
        open_alerts = {}
        for e in run.events_until(cursor):
            if e.event_type == "alert":
                open_alerts[(e.component, e.reason)] = e
            elif e.event_type == "recovery":
                open_alerts.pop((e.component, e.reason.replace("Recovered: ", "")), None)
        st.dataframe(pd.DataFrame([{"Since": e.sim_time, "Severity": e.severity.upper(), "Service": e.component, "Alert": e.reason,
                                    "Observed": f"{fmt(e.observed_value)} {e.unit}", "Threshold": fmt(e.threshold), "Response": e.action}
                                   for e in open_alerts.values()]), hide_index=True, width="stretch")
    else:
        st.success("No active alerts.")
    st.markdown("##### Event timeline")
    evs = all_events()
    f1, f2, f3, f4 = st.columns(4)
    sev = f1.multiselect("Severity", ["info", "notice", "warning", "critical"], default=["notice", "warning", "critical"])
    types = sorted({e.event_type for e in evs})
    et = f2.multiselect("Event type", types, default=[t for t in types if t != "source_status"])
    comp_f = f3.text_input("Component contains", "")
    req_f = f4.text_input("Request ID contains", "")
    filt = [e for e in evs if e.severity in sev and e.event_type in et and comp_f.lower() in e.component.lower()
            and (not req_f or (e.request_id and req_f in e.request_id))]
    edf = pd.DataFrame([e.as_dict() for e in filt]) if filt else pd.DataFrame(columns=["step"])
    st.dataframe(edf, hide_index=True, width="stretch", height=320)
    d1, d2, d3 = st.columns([1, 1, 3])
    d1.download_button("⬇ Export CSV", events_to_csv(filt), file_name=f"events-{run.run_id[:8]}.csv", mime="text/csv")
    d2.download_button("⬇ Export JSON", events_to_json(filt), file_name=f"events-{run.run_id[:8]}.json", mime="application/json")
    d3.caption(f"{len(filt)} of {len(evs)} events · repeated alert evaluations suppressed by de-duplication: "
               f"{sum(run.suppressed_alerts.values())} (whole run).")

# ===================================================== SCENARIO COMPARISON
with tabs[7]:
    st.markdown("#### Same request, three operating states")
    comp_runs = comparison_runs()
    probe = int(st.number_input("Compare at simulated minute (T+)", min_value=0, max_value=LAST,
                                value=list(SCENARIOS.values())[0].probe_step, step=1, key="probe"))
    cols = st.columns(len(comp_runs))
    summary_rows = []
    for col, (sid, r) in zip(cols, comp_runs.items()):
        pr = r.at(probe)
        tr = build_trace(pr, r.run_id, 0, r.load_reasons[probe])
        with col:
            st.markdown(f"##### {SCENARIOS[sid].label}")
            st.markdown(card("State at probe", f"{pr['state']} — {pr['phase']}", pr["sim_time"]), unsafe_allow_html=True)
            st.markdown(card("Power", f"{pr['available_supply_mw']:.1f} MW available",
                             "Grid" if pr["grid_connected"] else "Islanded"), unsafe_allow_html=True)
            st.markdown(card("User sees", QUALITY_BADGE[pr["svc_quality"]], f"{fmt(pr['svc_latency_s'], 1)} s · {pr['svc_model']}"),
                        unsafe_allow_html=True)
            st.text(tr.response_text)
        sm = summarize(r.steps, CAT.costs)
        summary_rows.append({
            "Scenario": SCENARIOS[sid].label, "Intent": {"normal": "Normal", "constrained": "Capacity constrained", "emergency": "Emergency"}[SCENARIOS[sid].state_intent],
            "Inference available (%)": sm["inference_availability"], "p95 latency (s)": sm["p95_latency"] or 0.0,
            "Energy (MWh)": sm["energy"], "Grid CO₂ (t)": sm["grid_co2"], "Generator CO₂ (t)": sm["gen_co2"],
            "Generator NOx (kg)": sm["gen_nox"], "Direct water (m³)": sm["direct_water"], "Generator run (h)": sm["gen_hours"],
            "Operating cost (EUR)": sm["cost_electricity"] + sm["cost_fuel"] + sm["cost_water"] + sm["cost_maintenance"],
        })
    sdf = pd.DataFrame(summary_rows)
    st.markdown("##### Whole-run comparison (240 simulated minutes)")
    st.dataframe(sdf.drop(columns=["Intent"]).round(2), hide_index=True, width="stretch")
    b = st.columns(3)
    b[0].plotly_chart(charts.comparison_bars(sdf, "Inference available (%)", "Inference available", "% of time"), width="stretch", key="cmp1")
    b[1].plotly_chart(charts.comparison_bars(sdf, "Operating cost (EUR)", "Operating cost", "EUR"), width="stretch", key="cmp2")
    b[2].plotly_chart(charts.comparison_bars(sdf, "Generator CO₂ (t)", "Generator emissions", "t CO₂"), width="stretch", key="cmp3")
    st.caption("Comparison runs use a fixed run ID and the same request and workload profile, so differences come from operating conditions only.")

# ================================================================ TIMELINE
with tabs[8]:
    st.markdown("#### 2023–2030 planning and evidence timeline")
    st.caption("This calendar is independent of the incident simulation clock. Facility milestones are hypothetical; "
               "regional and regulatory milestones cite published sources.")
    ms = [m.model_dump() for m in CAT.timeline.milestones]
    st.plotly_chart(charts.timeline_chart(ms), width="stretch", key="timeline")
    ev_filter = st.multiselect("Evidence class", ["observed", "announced", "hypothetical"], default=["observed", "announced", "hypothetical"])
    tdf = pd.DataFrame([{"Date": m["date"], "Evidence": {"observed": "● Observed", "announced": "◆ Announced", "hypothetical": "□ Hypothetical"}[m["evidence"]],
                         "Category": m["category"].replace("_", " "), "Milestone": m["title"], "Source": SRC[m["source_id"]].title,
                         "Verification": SRC[m["source_id"]].verification_status} for m in ms if m["evidence"] in ev_filter])
    st.dataframe(tdf, hide_index=True, width="stretch")

# ======================================================== EVIDENCE CATALOG
with tabs[9]:
    st.markdown("#### Evidence and governance catalog")
    st.caption("Entries record demonstration evidence only. Nothing here is a compliance verdict for a real facility or service.")
    st.markdown("##### Jurisdiction coverage")
    st.dataframe(pd.DataFrame(CAT.coverage()), hide_index=True, width="stretch")
    g1, g2, g3 = st.columns(3)
    jf = g1.multiselect("Jurisdiction", [j.code for j in CAT.governance.jurisdictions], default=[j.code for j in CAT.governance.jurisdictions])
    dm = g2.multiselect("Domain", ["facility", "ai_service"], default=["facility", "ai_service"])
    ls = g3.multiselect("Legal status", ["binding", "official_guidance", "voluntary_standard"], default=["binding", "official_guidance", "voluntary_standard"])
    gdf = pd.DataFrame([{
        "ID": e.id, "Jurisdiction": e.jurisdiction, "Instrument": e.instrument, "Section": e.section or "—",
        "Authority": e.issuing_authority, "Published": e.publication_date or "—", "Effective": e.effective_date or "—",
        "Domain": e.domain, "Legal status": e.legal_status, "Applicability": e.applicability, "Control": e.related_control,
        "Evidence expected": e.evidence_expected, "Verification": SRC[e.source_id].verification_status,
        "Verified on": SRC[e.source_id].verification_date or "—", "Uncertainty / gap": e.uncertainty or "—",
        "URL": SRC[e.source_id].url} for e in CAT.governance.entries
        if e.jurisdiction in jf and e.domain in dm and e.legal_status in ls])
    st.dataframe(gdf, hide_index=True, width="stretch", column_config={"URL": st.column_config.LinkColumn("URL")})
    st.markdown("##### Controls and how the demo evidences them")
    st.dataframe(pd.DataFrame([c.model_dump() for c in CAT.governance.controls]), hide_index=True, width="stretch")
    st.markdown("##### Datasets and references")
    cls = st.multiselect("Classification", ["published", "assumption", "simulated"], default=["published", "assumption", "simulated"])
    st.dataframe(pd.DataFrame([{"ID": s.id, "Title": s.title, "Publisher": s.publisher, "Class": s.classification,
                                "Verification": s.verification_status, "Edition": s.edition, "Used for": s.used_for,
                                "Excerpt / note": s.excerpt or "—", "URL": s.url} for s in CAT.sources.sources if s.classification in cls]),
                 hide_index=True, width="stretch", column_config={"URL": st.column_config.LinkColumn("URL")})
    pv = [{"Source": s.title, "Value": v.name, "Figure": v.value, "Unit": v.unit} for s in CAT.sources.sources for v in s.values]
    if pv:
        st.markdown("##### Published values used in the app")
        st.dataframe(pd.DataFrame(pv), hide_index=True, width="stretch")

# ============================================================== HELP CENTER
with tabs[10]:
    st.markdown("#### Help Center")
    with st.expander("The three operating states", expanded=True):
        st.markdown("""
- 🟢 **Normal** — grid-connected. The regional mix (wind, solar, hydro, gas, oil-derived) is dispatched by marginal cost; the facility runs every workload and the chat gets the large model.
- 🟠 **Capacity constrained** — a generation loss, demand spike or cooling fault leaves less power or cooling than the facility wants. The grid operator may cap import at the firm level. The facility sheds **training → batch → optional work** first, then reduces interactive concurrency, then routes chat to the configured **smaller model**.
- 🔴 **Emergency** — the grid is lost. The facility **islands**: the UPS bridges while standby generators start; generators and the battery carry a reduced load; when fuel and battery energy run low, inference is shut down in a controlled way (users get an explicit *unavailable* response) while core services keep running; when the grid returns, workloads are restored **in priority order**, one stage at a time.
""")
    with st.expander("Essential terms"):
        st.markdown("""
| Term | Meaning |
|---|---|
| **MW vs MWh** | MW is power (a rate); MWh is energy (an amount). A 6 MW battery with 6 MWh lasts one hour at full power. |
| **Islanding** | Running disconnected from the grid on local assets. Only sources that are electrically reachable *and* island-capable may supply the facility. |
| **UPS ride-through** | Short-duration battery inside the power chain that bridges the gap until generators are online. |
| **Critical-load budget** | Power needed for distribution, essential cooling, network & security, storage, serving nodes, minimum accelerators/GPU memory and minimum observability. Inference is only served when the whole chain is powered. |
| **Reserve SoC** | Battery energy held back for core services (not inference) during an island. |
| **Reason code** | Why a source is (not) supplying: DISPATCHED, AT_CAPACITY, STANDBY, UNAVAILABLE, EXHAUSTED, UNREACHABLE, NOT_ISLAND_CAPABLE, POLICY_RESTRICTED, STARTING, RESERVE_HELD. |
| **PUE / WUE** | Power and water usage effectiveness, computed with the EU 2024/1364 Annex III formulas over the run window. |
| **Correlation ID** | Identifier that follows one request through every stage and event; the prompt text is never logged. |
""")
    with st.expander("How to read the charts"):
        st.markdown("""
- **Power supplied by source** — stacked areas are MW delivered to the facility by each source; the dashed line is what the facility *wants*, the purple line is what is *available*. Shaded bands mark constrained or emergency periods (also labelled in text). The dotted vertical line is the current clock.
- **Regional mix** — the modelled grid in GW; grid shares are attributed to the facility's import while connected.
- **Stored energy and fuel** — percentages of rated energy (battery, UPS) and of the fuel tank; these are energy, not power.
- **AI service** — latency against the SLO, throughput vs demand vs capacity, queue depth and GPU utilization/memory.
- **Request trace** — one bar per stage on a log scale; ✔ ok, ⏱ slow, ✖ blocked, – not reached.
""")
    with st.expander("Replay a scenario"):
        st.markdown("""
1. Pick a scenario and press **▶ Run scenario** (fresh run ID, clock at T+0, playing).
2. **⏸ Pause**, **⏭ Step** one minute, or type a minute into the **simulation clock**.
3. Press **📨 Send request** at any moment to trace the same request through the current state.
4. **⟲ Replay** restarts the same run from T+0 — values are identical because the simulation is deterministic.
5. **↺ Reset** starts a new run ID and clears the request log for this browser session only; other visitors are unaffected.
""")
    with st.expander("Published reference data vs simulated telemetry"):
        st.markdown("""
- **Published** — figures or text from official sources (e.g. CSO's 21% data-centre share of Irish metered electricity in 2023; EU 2024/1364 KPI formulas). Each has a verification status and date in the Evidence catalog. Sources that could not be retrieved are marked as **gaps**, never filled in.
- **Scenario assumption** — every capacity, price, emission factor and threshold of the fictional ORF-1 facility and its regional grid (`data/reference_facility.yaml`, `data/scenarios/`, `data/catalog/costs.yaml`).
- **Simulated** — every time series, alert, decision and trace produced by the deterministic engine at run time.
- Control checks are **demonstration evidence**, not a compliance verdict.
""")
    with st.expander("Known limitations"):
        st.markdown("""
- One-minute steps; electrical transients, frequency and protection are not modelled.
- The regional grid is a scaled, five-source stand-in (no interconnectors, storage cycling or coal/peat).
- Water withdrawal, hydro evaporation, ambient air quality and noise are explicit unknowns.
- Costs exclude network, capacity and carbon charges.
""")

st.divider()
st.caption("Behind the Chat — AI Infrastructure Observatory · V1.0 · Developed and built by Oren Salami · "
           "Fictional facility, simulated telemetry; see the Evidence catalog for sources and gaps.")

# ---------------------------------------------------------------- playback
if ss.playing:
    if ss.cursor >= LAST:
        ss.playing = False
    else:
        time.sleep(0.5)
        ss.cursor = min(LAST, ss.cursor + ss.speed)
        st.rerun()
