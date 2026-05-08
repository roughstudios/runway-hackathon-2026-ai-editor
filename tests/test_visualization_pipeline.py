"""Smoke tests for the Stage 2 visualization pipeline.

The dry-run path requires no network, no ffmpeg, and burns no credits.
That's the default CI gate. The live path is exercised manually with
--limit and a real --source-video.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_editor.suggestion_classifier import (
    VFX_CATEGORIES,
    classify_segments,
)
from ai_editor.visual_director import (
    ALEPH_DEFAULT_DURATION_SEC,
    ALEPH_DEFAULT_RATIO,
    ALEPH_MODEL,
    ALEPH_VALID_DURATIONS,
    ALEPH_VALID_RATIOS,
    _normalize_payload,
    _stub_brief,
    find_frames_at_timecode,
    generate_brief,
)
from ai_editor.visualization_pipeline import build_visualizations


CANYONS_REPORT = Path(
    "R:/--CODE--/StudioOS-v1/data/film_analyzer/reports/canyons_100_miles_1_3.json"
)
CANYONS_FRAMES = Path(
    "R:/--CODE--/StudioOS-v1/data/learndocumentary/analysis_review/frames_canyons-100-miles-1-3"
)


def _fixture_segments():
    return [
        {
            "comment_id": "emotional.0",
            "type": "TENSION",
            "timecode": "00:00:31",
            "start_seconds": 31.67,
            "end_seconds": 34.0,
            "impact": "medium",
            "narration_text": (
                "Tension here. Let the speaker's face hold for an extra "
                "second after this line, allowing the weight of the "
                "statement to land."
            ),
        },
        {
            "comment_id": "emotional.1",
            "type": "VULNERABILITY",
            "timecode": "00:03:12",
            "start_seconds": 192.0,
            "end_seconds": 221.0,
            "impact": "high",
            "narration_text": (
                "Vulnerable beat. The cold and dusk should feel real here. "
                "Allow the hesitation to play out."
            ),
        },
        {
            "comment_id": "emotional.2",
            "type": "TURNING_POINT",
            "timecode": "00:09:00",
            "start_seconds": 540.0,
            "end_seconds": 545.0,
            "impact": "high",
            "narration_text": (
                "Turning point. Hold on Tommy's reaction so the silence does "
                "the work."
            ),
        },
    ]


def test_classifier_dry_run_labels_all_three(tmp_path):
    results = classify_segments(
        _fixture_segments(),
        cache_dir=tmp_path / "cls",
        dry_run=True,
    )
    assert len(results) == 3
    by_id = {r.comment_id: r for r in results}
    # vulnerability segment hits "cold" + "dusk" cues
    assert by_id["emotional.1"].label == "SUGGESTION"
    assert by_id["emotional.1"].needs_visualization is True
    assert by_id["emotional.1"].vfx_category in VFX_CATEGORIES
    # second pass should be cached
    again = classify_segments(
        _fixture_segments(),
        cache_dir=tmp_path / "cls",
        dry_run=True,
    )
    assert all(r.cached for r in again)


def test_classifier_skips_empty_narration(tmp_path):
    segs = [
        {"comment_id": "x", "narration_text": ""},
        {"comment_id": "y", "narration_text": "  "},
    ]
    results = classify_segments(segs, cache_dir=tmp_path / "cls", dry_run=True)
    assert results == []


def test_visual_director_stub_matches_locked_endpoint():
    payload = _stub_brief(narration="atmospheric haze", vfx_hint="atmospheric", brand={})
    norm = _normalize_payload(payload)
    assert norm["model"] == ALEPH_MODEL
    assert norm["ratio"] in ALEPH_VALID_RATIOS
    assert norm["duration_sec"] in ALEPH_VALID_DURATIONS
    assert len(norm["prompt_text"]) <= 220


def test_visual_director_normalizes_invalid_inputs():
    raw = {
        "model": "ignored",
        "prompt_text": "X" * 500,
        "ratio": "9999:9999",
        "duration_sec": 11,
        "vfx_category": "atmospheric",
    }
    norm = _normalize_payload(raw)
    assert norm["model"] == ALEPH_MODEL
    assert norm["ratio"] == ALEPH_DEFAULT_RATIO
    assert norm["duration_sec"] == ALEPH_DEFAULT_DURATION_SEC
    assert len(norm["prompt_text"]) <= 220


def test_generate_brief_dry_run_caches(tmp_path):
    seg = {
        "comment_id": "emotional.1",
        "narration_text": "Cold dusk should feel real.",
        "vfx_category": "atmospheric",
        "start_seconds": 192.0,
    }
    b1 = generate_brief(
        seg, frames=[], cache_dir=tmp_path / "dir", dry_run=True
    )
    assert b1.model == ALEPH_MODEL
    assert b1.cached is False
    b2 = generate_brief(
        seg, frames=[], cache_dir=tmp_path / "dir", dry_run=True
    )
    assert b2.cached is True
    assert b2.prompt_text == b1.prompt_text


@pytest.mark.skipif(
    not CANYONS_FRAMES.exists(), reason="Canyons frames not present"
)
def test_find_frames_picks_neighbours_at_timecode():
    # 192s is between frame_05_185s.jpg and frame_06_222s.jpg
    frames = find_frames_at_timecode(CANYONS_FRAMES, 192.0, window=1)
    assert len(frames) == 3
    # ordering is chronological after nearest-pick
    seconds = [int(p.stem.split("_")[-1].rstrip("s")) for p in frames]
    assert seconds == sorted(seconds)


@pytest.mark.skipif(
    not CANYONS_REPORT.exists(), reason="Canyons report not present"
)
def test_pipeline_dry_run_against_canyons_segments(tmp_path):
    # Build a minimal Stage 1 segments JSON (we don't need real audio
    # to exercise the Stage 2 plumbing).
    seg_path = tmp_path / "commentary_segments.json"
    seg_path.write_text(
        json.dumps({
            "report_path": str(CANYONS_REPORT),
            "track_path": str(tmp_path / "commentary.wav"),
            "total_duration_sec": 1114.93,
            "voice_preset": "Vincent",
            "model": "eleven_multilingual_v2",
            "dry_run": True,
            "segments": _fixture_segments(),
        }),
        encoding="utf-8",
    )

    manifest = build_visualizations(
        segments_json_path=seg_path,
        source_video=None,
        out_dir=tmp_path / "viz",
        frames_dir=CANYONS_FRAMES if CANYONS_FRAMES.exists() else None,
        dry_run=True,
        max_aleph=10,
    )
    assert manifest["stats"]["n_suggestions"] >= 1
    assert manifest["stats"]["n_observations"] >= 1
    assert manifest["stats"]["aleph_calls"] == 0  # dry_run
    out_path = Path(manifest["manifest_path"])
    assert out_path.exists()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["dry_run"] is True
    for v in saved["visualizations"]:
        b = v["brief"]
        assert b["model"] == ALEPH_MODEL
        assert b["ratio"] in ALEPH_VALID_RATIOS
        assert b["duration_sec"] in ALEPH_VALID_DURATIONS
        assert v["narration_text"]


def test_pipeline_observations_carry_into_narrations_block(tmp_path):
    seg_path = tmp_path / "commentary_segments.json"
    seg_path.write_text(
        json.dumps({
            "report_path": "fake",
            "track_path": "fake",
            "total_duration_sec": 100.0,
            "voice_preset": "Vincent",
            "model": "eleven_multilingual_v2",
            "dry_run": True,
            "segments": [{
                "comment_id": "emotional.0",
                "type": "TURNING_POINT",
                "timecode": "00:00:10",
                "start_seconds": 10.0,
                "end_seconds": 12.0,
                "impact": "low",
                # Plain narration, no atmospheric cue → OBSERVATION
                "narration_text": "Trim the pause; tighten the cut.",
            }],
        }),
        encoding="utf-8",
    )
    manifest = build_visualizations(
        segments_json_path=seg_path,
        source_video=None,
        out_dir=tmp_path / "viz",
        dry_run=True,
    )
    assert manifest["stats"]["n_suggestions"] == 0
    assert manifest["stats"]["n_observations"] == 1
    assert manifest["narrations"][0]["label"] == "OBSERVATION"
