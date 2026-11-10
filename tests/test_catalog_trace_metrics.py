from __future__ import annotations

import json

import pytest

from observatory.catalog import load_catalogs
from observatory.config import load_reference, load_scenarios
from observatory.engine import run_scenario
from observatory.events import EventRecorder, events_to_csv, events_to_json
from observatory.metrics import METRICS, explain, summarize
from observatory.trace import DEFAULT_PROMPT, build_trace, record_request


@pytest.fixture(scope="module")
def cat():
    return load_catalogs()


@pytest.fixture(scope="module")
def emergency():
    ref = load_reference()
    return run_scenario(ref, load_scenarios()["emergency"], run_id="trace-test-run")


def test_initial_jurisdictions_have_entries_or_marked_gaps(cat):
    cov = {c["code"]: c for c in cat.coverage()}
    for code in ("EU", "US", "AU", "JP", "IL"):
        assert cov[code]["entries"] > 0, code
        assert cov[code]["status"] != "No entries"
    src = cat.sources.by_id()
    for e in cat.governance.entries:
        if src[e.source_id].is_gap:
            assert e.uncertainty and "GAP" in e.uncertainty, e.id


def test_every_dataset_is_classified(cat):
    for s in cat.sources.sources:
        assert s.classification in ("published", "assumption", "simulated")
    for m in cat.timeline.milestones:
        assert m.evidence in ("observed", "announced", "hypothetical")
        assert 2023 <= int(m.date[:4]) <= 2030
    for m in METRICS.values():
        assert m.classification in ("published", "assumption", "simulated")
        assert m.source_ids


def test_metric_lineage(cat, emergency):
    rows = emergency.steps
    s = summarize(rows, cat.costs)
    assert s["pue"] >= 1.0
    assert s["gen_co2"] > 0 and s["grid_co2"] > 0
    ex = explain("pue", rows, cat.costs)
    assert ex["inputs"] and ex["value"] == pytest.approx(s["pue"])
    for m in METRICS.values():
        for sid in m.source_ids:
            assert sid in cat.sources.by_id(), sid


def test_trace_unavailable_is_explicit(emergency):
    row = next(r for r in emergency.steps if r["phase"] == "Controlled shutdown")
    t = build_trace(row, emergency.run_id, 1, emergency.load_reasons[row["step"]])
    assert t.outcome == "UNAVAILABLE"
    assert any(s.status == "blocked" for s in t.stages)
    assert "unavailable" in t.response_text.lower()


def test_trace_full_service(emergency):
    row = emergency.steps[0]
    t = build_trace(row, emergency.run_id, 1, emergency.load_reasons[0])
    assert t.outcome == "FULL"
    assert t.request_id.startswith("req-trace-")
    assert all(s.status in ("ok", "slow") for s in t.stages)


def test_request_logging_excludes_prompt(emergency):
    rec = EventRecorder("r")
    row = emergency.steps[0]
    t = build_trace(row, emergency.run_id, 1, emergency.load_reasons[0])
    record_request(rec, t, len(DEFAULT_PROMPT))
    blob = events_to_json(rec.events) + events_to_csv(rec.events)
    assert DEFAULT_PROMPT not in blob
    assert all(e.request_id == t.request_id for e in rec.events)
    assert json.loads(events_to_json(rec.events))[0]["request_id"] == t.request_id
