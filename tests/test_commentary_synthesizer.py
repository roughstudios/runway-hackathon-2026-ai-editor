"""Smoke tests for the Stage 1 commentary synthesizer.

The dry-run path requires no network and burns no credits. Use it as the
default CI gate; the real-TTS path is exercised manually with --limit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_editor.commentary_synthesizer import (
    build_commentary,
    compose_narration,
    extract_moments,
)

CANYONS_REPORT = Path(
    r"R:/--CODE--/StudioOS-v1/data/film_analyzer/reports/canyons_100_miles_1_3.json"
)


def test_compose_narration_includes_type_label():
    n = compose_narration(
        {"type": "TURNING_POINT", "suggestion": "Hold on Tommy's reaction."}
    )
    assert n.startswith("Turning point.")
    assert "Hold on Tommy's reaction." in n


def test_compose_narration_handles_missing_label():
    n = compose_narration({"type": "WHATEVER", "suggestion": "Tighten the cut."})
    assert n == "Tighten the cut."


def test_compose_narration_returns_empty_when_no_suggestion():
    assert compose_narration({"type": "TENSION", "suggestion": ""}) == ""
    assert compose_narration({"type": "TENSION"}) == ""


def test_extract_moments_skips_entries_without_start_seconds():
    report = {
        "emotional_analysis": {
            "moments": [
                {"type": "TENSION", "start_seconds": 10.0, "suggestion": "x"},
                {"type": "TENSION", "suggestion": "no timing"},
                {"type": "VULNERABILITY", "start_seconds": 30.0, "suggestion": "y"},
            ]
        }
    }
    out = extract_moments(report)
    assert len(out) == 2
    assert {m["_id"] for m in out} == {"emotional.0", "emotional.2"}


@pytest.mark.skipif(
    not CANYONS_REPORT.exists(), reason="Canyons report not present"
)
def test_extract_moments_canyons():
    report = json.loads(CANYONS_REPORT.read_text(encoding="utf-8"))
    moments = extract_moments(report)
    assert len(moments) > 0
    for m in moments:
        assert "start_seconds" in m
        assert m["start_seconds"] >= 0
        assert "_id" in m


@pytest.mark.skipif(
    not CANYONS_REPORT.exists(), reason="Canyons report not present"
)
def test_build_commentary_dry_run_canyons(tmp_path):
    result = build_commentary(
        report_path=CANYONS_REPORT,
        out_dir=tmp_path / "out",
        limit=2,
        dry_run=True,
    )
    assert result.segments_path.exists()
    assert result.total_duration_sec > 0
    assert 0 < len(result.segments) <= 2
    payload = json.loads(result.segments_path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["voice_preset"] == "Vincent"
    seg0 = payload["segments"][0]
    assert seg0["narration_text"]
    assert seg0["start_seconds"] >= 0
    assert seg0["duration_sec"] > 0
