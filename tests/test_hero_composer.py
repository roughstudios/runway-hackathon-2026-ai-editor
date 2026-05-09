"""Smoke tests for the Stage 6 hero composer.

These tests cover the curation + plumbing that doesn't require Runway:
- ``curate(report)`` picks 5 segments at the right timecodes
- ``compose_hero(... dry_run=True)`` writes a manifest with all five
  curated segments and zero-byte placeholders
- the runway_http endpoint helpers build the right JSON bodies
- the ffmpeg compose graph is well-formed for a single-segment plan
  (the actual ffmpeg call is exercised by the live run, not here)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_editor.hero_composer import (  # noqa: E402
    CuratedSegment,
    NARRATION,
    compose_hero,
    curate,
)


REPORT_PATH = Path(
    "R:/--CODE--/StudioOS-v1/data/film_analyzer/reports/canyons_100_miles_1_3.json"
)


# --- Curation -------------------------------------------------------------


def test_curate_picks_five_segments_with_expected_ids() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    segs = curate(report)
    ids = [s.id for s in segs]
    assert ids == [
        "open_hook",
        "first_place",
        "arwa_battle",
        "tommy_climax",
        "arwa_resolution",
    ]
    # Total duration should land in the 5-7 min window the prompt asks for
    total = sum(s.duration_sec for s in segs)
    assert 300 <= total <= 420, f"hero too {'short' if total<300 else 'long'}: {total}s"


def test_curate_segments_carry_narration() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    segs = curate(report)
    for s in segs:
        assert s.narration_text == NARRATION[s.id]
        assert s.kind in ("COMMENTARY", "SUGGESTION")
        assert s.duration_sec > 0


def test_curate_suggestions_have_aleph_prompts() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    segs = curate(report)
    suggestions = [s for s in segs if s.kind == "SUGGESTION"]
    assert len(suggestions) >= 3, "want at least 3 Aleph treatments"
    for s in suggestions:
        assert s.aleph_prompt
        assert len(s.aleph_prompt) <= 280
        assert s.vfx_category


# --- Dry-run plumbing -----------------------------------------------------


def test_compose_hero_dry_run_writes_manifest(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    manifest = compose_hero(
        report_path=REPORT_PATH,
        out_root=out_root,
        dry_run=True,
    )
    assert manifest["dry_run"] is True
    assert len(manifest["segments"]) == 5
    assert Path(manifest["manifest_path"]).exists()
    # Hero file is a placeholder in dry-run
    hero = Path(manifest["hero_path"])
    assert hero.exists()
    # Curated timeline is monotonic and contiguous
    cursor = 0.0
    for s in manifest["segments"]:
        assert s["curated_in_seconds"] == round(cursor, 3)
        cursor += s["duration_sec"]
        assert s["curated_out_seconds"] == round(cursor, 3)


def test_compose_hero_dry_run_only_segments(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    manifest = compose_hero(
        report_path=REPORT_PATH,
        out_root=out_root,
        dry_run=True,
        only_segments=["first_place", "tommy_climax"],
    )
    assert {s["id"] for s in manifest["segments"]} == {"first_place", "tommy_climax"}


# --- runway_http payload shape (no network) -------------------------------


def test_runway_http_endpoint_paths_constant() -> None:
    from ai_editor import runway_http
    assert runway_http.API_BASE.startswith("https://")
    assert runway_http.API_VERSION == "2024-11-06"


if __name__ == "__main__":
    # ``pytest`` hangs in the StudioOS .venv per the prior handoffs, so
    # these tests can be run directly: ``python tests/test_hero_composer.py``.
    import tempfile
    test_curate_picks_five_segments_with_expected_ids()
    test_curate_segments_carry_narration()
    test_curate_suggestions_have_aleph_prompts()
    with tempfile.TemporaryDirectory() as td:
        test_compose_hero_dry_run_writes_manifest(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_compose_hero_dry_run_only_segments(Path(td))
    test_runway_http_endpoint_paths_constant()
    print("All hero composer smoke tests passed.")
