"""Headless UI tests with Streamlit's AppTest: the dashboard shows engine values."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

from observatory.config import load_reference, load_scenarios  # noqa: E402
from observatory.engine import run_scenario  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")


def _app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _md(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_loads_normal_and_matches_engine():
    at = _app()
    row = at.session_state.run.at(0)
    expected = run_scenario(load_reference(), load_scenarios()["normal"], run_id="x" * 32).at(0)
    assert row == expected
    assert f"{row['available_supply_mw']:.1f} MW" in _md(at)
    # RTL guard: st.slider renders mirrored in right-to-left locales, so the clock must stay a number input
    assert len(at.slider) == 0
    assert at.number_input(key="clock_input").value == 0


def test_emergency_walkthrough_and_trace():
    at = _app()
    at.selectbox[0].set_value("emergency").run()
    assert at.session_state.scenario_id == "emergency"
    at.number_input(key="clock_input").set_value(186).run()
    assert at.session_state.cursor == 186
    assert "Controlled shutdown" in _md(at)
    next(b for b in at.button if "Send request" in b.label).click().run()
    trace = at.session_state.traces[-1]
    assert trace.outcome == "UNAVAILABLE"
    events = at.session_state.req_recorder.events
    assert [e.event_type for e in events] == ["request_received", "request_completed"]
    assert all("Summarise" not in (e.reason + e.outcome) for e in events)
    assert not at.exception


def test_step_replay_reset():
    at = _app()
    run_id = at.session_state.run.run_id
    next(b for b in at.button if "Step" in b.label).click().run()
    assert at.session_state.cursor == 1
    next(b for b in at.button if "Reset" in b.label).click().run()
    assert at.session_state.cursor == 0 and at.session_state.run.run_id != run_id
    assert not at.exception


def test_deep_link_is_validated():
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["scenario"] = "constrained"
    at.query_params["t"] = "100"
    at.run()
    assert at.session_state.scenario_id == "constrained" and at.session_state.cursor == 100
    bad = AppTest.from_file(APP, default_timeout=60)
    bad.query_params["scenario"] = "nope"
    bad.query_params["t"] = "abc"
    bad.run()
    assert bad.session_state.scenario_id == "normal" and bad.session_state.cursor == 0
    assert not bad.exception
