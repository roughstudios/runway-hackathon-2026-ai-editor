"""Smoke tests for the Stage 4 audio pipeline.

The dry-run path requires no network and burns no credits — that's the
default gate. The live path is exercised manually with --limit and a
real --report.

Tests use mock objects rather than real Runway calls. The mocks mirror
the SDK's relevant return shapes precisely enough that the production
code paths execute end-to-end against them.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Make the repo root importable when this file is run directly. pytest
# in the StudioOS .venv hangs (per Charlie/Echo handoffs); these tests
# are designed to be invoked directly via ``python -c "..."`` too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_editor.audio_director import (  # noqa: E402
    AudioBrief,
    DEFAULT_FX_DURATION_SEC,
    SFX_MAX_DURATION_SEC,
    SFX_MIN_DURATION_SEC,
    _normalize_payload,
    _parse_first_json_object,
    _parse_timecode,
    _scene_id_for,
    _stub_brief,
    brief_to_json,
    generate_brief,
)
from ai_editor.audio_pipeline import (  # noqa: E402
    ATMOSPHERE_TRACK,
    HARD_FX_TRACK,
    build_audio_track,
)
from ai_editor.sfx_client import (  # noqa: E402
    SFX_MODEL,
    SFXClient,
    _clamp_duration,
    _fingerprint as _sfx_fingerprint,
)


# --- Fakes mirror the runwayml SDK response surface ----------------------


@dataclass
class _FakeTaskResult:
    output: List[str]


class _FakeTask:
    def __init__(self, task_id: str, output_url: str):
        self.id = task_id
        self._output_url = output_url

    def wait_for_task_output(self, timeout=None):
        return _FakeTaskResult(output=[self._output_url])


class _FakeSoundEffectResource:
    def __init__(self, log: List[tuple]):
        self._log = log
        self._counter = 0

    def create(self, *, model, prompt_text, duration, loop, **kwargs):
        self._counter += 1
        self._log.append((
            "sound_effect.create", model, prompt_text, float(duration), bool(loop),
        ))
        return _FakeTask(
            task_id=f"task-{self._counter}",
            output_url=f"https://example.com/fake-sfx-{self._counter}.mp3",
        )


class _FakeRunway:
    def __init__(self):
        self.log: List[tuple] = []
        self.sound_effect = _FakeSoundEffectResource(self.log)


def _fixture_scenes():
    return [
        {
            "number": 1,
            "name": "Pre-Race Setting",
            "timecode_in": "00:00:00",
            "timecode_out": "00:01:15",
            "description": "Tommy arrives at the chateau in the morning.",
            "story_purpose": "Establish the luxurious pre-race location.",
            "story_beat": "hook",
            "act": 1,
            "start_seconds": 0.0,
            "end_seconds": 75.0,
        },
        {
            "number": 2,
            "name": "Forest Approach",
            "timecode_in": "00:01:15",
            "timecode_out": "00:02:30",
            "description": "Pre-race forest path at dawn.",
            "story_purpose": "Build atmosphere ahead of the start line.",
            "story_beat": "rising_action",
            "act": 1,
            "start_seconds": 75.0,
            "end_seconds": 150.0,
        },
        {
            "number": 3,
            "name": "Race Start",
            "timecode_in": "00:02:30",
            "timecode_out": "00:03:30",
            "description": "Runners gather, first footstep on gravel.",
            "story_purpose": "The race begins.",
            "story_beat": "inciting_incident",
            "act": 2,
            "start_seconds": 150.0,
            "end_seconds": 210.0,
        },
    ]


def _fixture_report(tmp_path: Path) -> Path:
    report = {
        "video_path": str(tmp_path / "fake.mov"),
        "duration": 210.0,
        "transcript": "What's up dude. Welcome to the chateau. Let's run.",
        "scenes": _fixture_scenes(),
    }
    p = tmp_path / "report.json"
    p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return p


# --- audio_director ------------------------------------------------------


def test_scene_id_zero_indexed_two_digits():
    assert _scene_id_for({"number": 1}) == "scene_00"
    assert _scene_id_for({"number": 9}) == "scene_08"
    assert _scene_id_for({"index": 5}) == "scene_05"
    print("OK test_scene_id_zero_indexed_two_digits")


def test_parse_timecode_handles_hh_mm_ss():
    assert _parse_timecode("00:00:31") == 31.0
    assert _parse_timecode("00:01:15") == 75.0
    assert _parse_timecode("01:00:00") == 3600.0
    assert _parse_timecode("") is None
    assert _parse_timecode("garbage") is None
    print("OK test_parse_timecode_handles_hh_mm_ss")


def test_stub_brief_atmosphere_for_forest_scene():
    payload = _stub_brief(scene={
        "name": "Forest Approach",
        "description": "Pre-race forest path at dawn",
        "story_purpose": "atmosphere",
    })
    assert payload["needs_atmosphere"] is True
    assert payload["atmosphere_brief"]["prompt"]
    print("OK test_stub_brief_atmosphere_for_forest_scene")


def test_stub_brief_no_match_returns_empty():
    payload = _stub_brief(scene={
        "name": "Sterile",
        "description": "Generic plot beat with no atmospheric cue",
        "story_purpose": "story progression",
    })
    assert payload["needs_atmosphere"] is False
    assert payload["atmosphere_brief"] is None
    assert payload["hard_fx_briefs"] == []
    print("OK test_stub_brief_no_match_returns_empty")


def test_normalize_payload_clamps_and_trims():
    raw = {
        "needs_atmosphere": True,
        "atmosphere_brief": {"prompt": "X" * 500, "reasoning": "..."},
        "hard_fx_briefs": [
            # over-cap duration
            {"start_offset_in_scene": 5.0, "duration": 9999, "prompt": "boom"},
            # under-cap duration
            {"start_offset_in_scene": 0.1, "duration": 0.01, "prompt": "tap"},
            # negative offset
            {"start_offset_in_scene": -3.0, "duration": 1.0, "prompt": "click"},
            # empty prompt — dropped
            {"start_offset_in_scene": 1.0, "duration": 1.0, "prompt": ""},
        ],
        "confidence": "0.7",
    }
    norm = _normalize_payload(raw, scene={"start_seconds": 0.0, "end_seconds": 30.0})
    assert norm["needs_atmosphere"] is True
    assert len(norm["atmosphere_brief"]["prompt"]) <= 220
    fx = norm["hard_fx_briefs"]
    assert len(fx) == 3, "empty-prompt entry should be dropped"
    assert all(SFX_MIN_DURATION_SEC <= f["duration"] <= SFX_MAX_DURATION_SEC for f in fx)
    assert all(f["start_offset_in_scene"] >= 0.0 for f in fx)
    assert norm["confidence"] == 0.7
    print("OK test_normalize_payload_clamps_and_trims")


def test_normalize_payload_drops_empty_atmosphere():
    raw = {
        "needs_atmosphere": True,
        "atmosphere_brief": {"prompt": "  ", "reasoning": ""},
        "hard_fx_briefs": [],
    }
    norm = _normalize_payload(raw, scene={"start_seconds": 0, "end_seconds": 10})
    assert norm["needs_atmosphere"] is False
    assert norm["atmosphere_brief"] is None
    print("OK test_normalize_payload_drops_empty_atmosphere")


def test_parse_first_json_object_handles_fences():
    out = _parse_first_json_object('```json\n{"a": 1}\n```')
    assert out == {"a": 1}
    out = _parse_first_json_object('preamble {\n"a": 2\n} trailing')
    assert out == {"a": 2}
    print("OK test_parse_first_json_object_handles_fences")


def test_generate_brief_dry_run_caches(tmp_path):
    scene = {
        "scene_id": "scene_01",
        "number": 2,
        "name": "Forest Approach",
        "description": "dawn forest",
        "story_purpose": "atmosphere",
        "story_beat": "rising_action",
        "act": 1,
        "timecode_in": "00:01:15",
        "timecode_out": "00:02:30",
        "start_seconds": 75.0,
        "end_seconds": 150.0,
    }
    b1 = generate_brief(
        scene, transcript="dialogue", frames=[],
        cache_dir=tmp_path / "ad", dry_run=True,
    )
    assert isinstance(b1, AudioBrief)
    assert b1.cached is False
    b2 = generate_brief(
        scene, transcript="dialogue", frames=[],
        cache_dir=tmp_path / "ad", dry_run=True,
    )
    assert b2.cached is True
    assert (b2.atmosphere_brief is None) == (b1.atmosphere_brief is None)
    print("OK test_generate_brief_dry_run_caches")


def test_brief_to_json_is_serializable(tmp_path):
    b = generate_brief(
        {
            "scene_id": "scene_00",
            "name": "Forest Approach",
            "description": "forest at dawn",
            "story_purpose": "build atmosphere",
            "start_seconds": 0,
            "end_seconds": 75,
        },
        transcript="",
        frames=[],
        cache_dir=tmp_path / "ad",
        dry_run=True,
    )
    j = brief_to_json(b)
    json.dumps(j)  # round-trips
    assert j["scene_id"] == "scene_00"
    print("OK test_brief_to_json_is_serializable")


# --- sfx_client ----------------------------------------------------------


def test_clamp_duration_respects_runway_window():
    assert _clamp_duration(0.0) == SFX_MIN_DURATION_SEC
    assert _clamp_duration(0.1) == SFX_MIN_DURATION_SEC
    assert _clamp_duration(2.5) == 2.5
    assert _clamp_duration(31.0) == SFX_MAX_DURATION_SEC
    assert _clamp_duration(9999) == SFX_MAX_DURATION_SEC
    print("OK test_clamp_duration_respects_runway_window")


def test_sfx_fingerprint_is_content_keyed():
    a = _sfx_fingerprint(prompt="rain", duration=1.0, loop=False, model=SFX_MODEL)
    b = _sfx_fingerprint(prompt="rain", duration=1.0, loop=False, model=SFX_MODEL)
    c = _sfx_fingerprint(prompt="rain", duration=1.0, loop=True, model=SFX_MODEL)
    d = _sfx_fingerprint(prompt="rain heavy", duration=1.0, loop=False, model=SFX_MODEL)
    e = _sfx_fingerprint(prompt="rain", duration=2.0, loop=False, model=SFX_MODEL)
    assert a == b
    assert a != c
    assert a != d
    assert a != e
    print("OK test_sfx_fingerprint_is_content_keyed")


def test_sfx_client_caches_per_brief(tmp_path, monkeypatch):
    fake = _FakeRunway()
    cache_dir = tmp_path / "sfx"

    import ai_editor.sfx_client as sfx_mod

    def _fake_download(url, dest):
        dest.write_bytes(b"ID3-fake-mp3-bytes")

    monkeypatch.setattr(sfx_mod, "_download", _fake_download)

    client = SFXClient(runway_client=fake, cache_dir=cache_dir)

    first = client.generate_atmosphere("low forest ambience", 5.0, loop=True)
    assert first.cached is False
    assert first.audio_path.exists()
    assert first.audio_path.stat().st_size > 0
    assert first.duration_requested == 5.0
    assert first.loop is True

    # Second call with identical brief → cache hit, no API call.
    second = client.generate_atmosphere("low forest ambience", 5.0, loop=True)
    assert second.cached is True
    assert second.audio_path == first.audio_path

    # Different loop flag → distinct fingerprint.
    third = client.generate_atmosphere("low forest ambience", 5.0, loop=False)
    assert third.cached is False
    assert third.audio_path != first.audio_path

    create_calls = [e for e in fake.log if e[0] == "sound_effect.create"]
    assert len(create_calls) == 2
    # Verify the SDK shape we're calling — model + duration + loop pass through.
    assert create_calls[0][1] == SFX_MODEL
    assert create_calls[0][3] == 5.0
    assert create_calls[0][4] is True
    print("OK test_sfx_client_caches_per_brief")


def test_sfx_client_rejects_empty_prompt(tmp_path):
    fake = _FakeRunway()
    client = SFXClient(runway_client=fake, cache_dir=tmp_path / "sfx")
    try:
        client.generate_fx("   ", 1.0)
    except ValueError:
        print("OK test_sfx_client_rejects_empty_prompt")
        return
    raise AssertionError("expected ValueError on empty prompt")


# --- audio_pipeline ------------------------------------------------------


def test_pipeline_dry_run_emits_frozen_manifest_shape(tmp_path):
    report_path = _fixture_report(tmp_path)
    out_dir = tmp_path / "sfx"

    manifest = build_audio_track(
        report_path=report_path,
        out_dir=out_dir,
        frames_dir=tmp_path / "no-frames-here",
        dry_run=True,
        max_sfx=20,
    )

    assert manifest["dry_run"] is True
    assert manifest["report_id"] == "report"
    assert manifest["model"] == SFX_MODEL
    assert manifest["stats"]["n_scenes"] == 3
    assert manifest["stats"]["sfx_calls"] == 0  # dry_run

    # Frozen manifest contract: scenes[].audio_assets[] with required fields.
    seen_atmos = 0
    seen_fx = 0
    for scene in manifest["scenes"]:
        assert scene["scene_id"].startswith("scene_")
        assert "scene_start_seconds" in scene
        assert "scene_end_seconds" in scene
        for asset in scene["audio_assets"]:
            assert asset["type"] in ("atmosphere", "hard_fx")
            assert asset["track"] in (ATMOSPHERE_TRACK, HARD_FX_TRACK)
            assert "asset_path" in asset
            assert "start_offset_in_scene" in asset
            assert "duration" in asset
            assert "loop" in asset
            assert "prompt" in asset
            if asset["type"] == "atmosphere":
                seen_atmos += 1
                assert asset["track"] == ATMOSPHERE_TRACK
            else:
                seen_fx += 1
                assert asset["track"] == HARD_FX_TRACK
                assert asset["loop"] is False
            # Dry-run asset paths exist (zero-byte placeholders).
            p = Path(asset["asset_path"])
            assert p.exists()

    assert seen_atmos >= 1, "Forest scene stub heuristic should emit atmosphere"
    assert manifest["stats"]["n_atmospheres"] == seen_atmos
    assert manifest["stats"]["n_hard_fx"] == seen_fx

    # Manifest written to disk, parses cleanly.
    parsed = json.loads(Path(manifest["manifest_path"]).read_text(encoding="utf-8"))
    assert parsed["report_id"] == "report"
    print("OK test_pipeline_dry_run_emits_frozen_manifest_shape")


def test_pipeline_long_scene_uses_loop_atmosphere(tmp_path):
    """Scenes longer than the 30s SFX cap should be assigned loop=True."""
    report = {
        "video_path": str(tmp_path / "fake.mov"),
        "duration": 200.0,
        "transcript": "",
        "scenes": [{
            "number": 1,
            "name": "Forest Approach",  # triggers stub atmosphere
            "timecode_in": "00:00:00",
            "timecode_out": "00:03:20",
            "description": "long forest walk",
            "story_purpose": "atmosphere",
            "story_beat": "rising_action",
            "act": 1,
            "start_seconds": 0.0,
            "end_seconds": 200.0,
        }],
    }
    report_path = tmp_path / "long.json"
    report_path.write_text(json.dumps(report))
    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        dry_run=True,
    )
    atm_assets = [
        a for s in manifest["scenes"] for a in s["audio_assets"]
        if a["type"] == "atmosphere"
    ]
    assert len(atm_assets) == 1
    a = atm_assets[0]
    assert a["loop"] is True
    # In-scene duration is the full scene length (Hotel-v3 will tile).
    assert abs(a["duration"] - 200.0) < 0.01
    print("OK test_pipeline_long_scene_uses_loop_atmosphere")


def test_pipeline_short_scene_no_loop(tmp_path):
    """A scene shorter than the 30s SFX cap doesn't need loop=True."""
    report = {
        "video_path": str(tmp_path / "fake.mov"),
        "duration": 20.0,
        "transcript": "",
        "scenes": [{
            "number": 1,
            "name": "Forest Approach",
            "timecode_in": "00:00:00",
            "timecode_out": "00:00:20",
            "description": "short forest moment",
            "story_purpose": "atmosphere",
            "start_seconds": 0.0,
            "end_seconds": 20.0,
        }],
    }
    report_path = tmp_path / "short.json"
    report_path.write_text(json.dumps(report))
    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        dry_run=True,
    )
    atm_assets = [
        a for s in manifest["scenes"] for a in s["audio_assets"]
        if a["type"] == "atmosphere"
    ]
    assert len(atm_assets) == 1
    a = atm_assets[0]
    assert a["loop"] is False
    assert abs(a["duration"] - 20.0) < 0.01
    print("OK test_pipeline_short_scene_no_loop")


def test_pipeline_skip_sfx_keeps_briefs(tmp_path):
    report_path = _fixture_report(tmp_path)
    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        skip_sfx=True,
        dry_run=True,
    )
    # In dry_run mode we still emit placeholder asset paths even when
    # skip_sfx=True is set, so this is really a smoke check that the
    # manifest still records the brief details.
    for s in manifest["scenes"]:
        assert "brief" in s
        assert "audio_assets" in s
    print("OK test_pipeline_skip_sfx_keeps_briefs")


def test_pipeline_live_path_with_director_monkeypatched(tmp_path, monkeypatch):
    """Live SFX path with the director monkeypatched to a stub.

    This is the meaningful end-to-end check: each scene's brief comes
    from a deterministic stub, but the SFX client really gets called
    against a fake Runway and emits per-scene MP3 asset files. The
    manifest carries real fingerprints + cached flags.
    """
    import ai_editor.audio_pipeline as ap_mod
    import ai_editor.sfx_client as sfx_mod

    def _stub_director(scene, *, transcript, frames, cache_dir,
                       anthropic_client, model, dry_run):
        scene_id = scene.get("scene_id") or "scene_xx"
        # Atmosphere only on scene 0; scene 1 gets one hard FX; scene 2
        # gets nothing.
        if scene.get("number") == 1:
            from ai_editor.audio_director import AtmosphereBrief
            return AudioBrief(
                scene_id=scene_id,
                needs_atmosphere=True,
                atmosphere_brief=AtmosphereBrief(
                    prompt="warm rural ambience, distant cicadas",
                    reasoning="stub reasoning",
                ),
                hard_fx_briefs=[],
                confidence=0.6,
                frames_used=[],
                cached=False,
            )
        if scene.get("number") == 2:
            from ai_editor.audio_director import HardFxBrief
            return AudioBrief(
                scene_id=scene_id,
                needs_atmosphere=False,
                atmosphere_brief=None,
                hard_fx_briefs=[
                    HardFxBrief(
                        start_offset_in_scene=2.5,
                        duration=1.5,
                        prompt="single match strike, close mic",
                        reasoning="stub",
                    ),
                ],
                confidence=0.7,
                frames_used=[],
                cached=False,
            )
        return AudioBrief(
            scene_id=scene_id, needs_atmosphere=False,
            atmosphere_brief=None, hard_fx_briefs=[],
            confidence=0.4, frames_used=[], cached=False,
        )

    monkeypatch.setattr(ap_mod, "generate_brief", _stub_director)

    def _fake_download(url, dest):
        dest.write_bytes(b"ID3-mp3-payload")

    monkeypatch.setattr(sfx_mod, "_download", _fake_download)

    fake = _FakeRunway()
    sfx_client = SFXClient(runway_client=fake, cache_dir=tmp_path / "sfxcache")

    report_path = _fixture_report(tmp_path)
    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        director_cache_dir=tmp_path / "ad",
        sfx_client=sfx_client,
        dry_run=False,
    )

    assert manifest["stats"]["n_scenes"] == 3
    assert manifest["stats"]["n_atmospheres"] == 1
    assert manifest["stats"]["n_hard_fx"] == 1
    assert manifest["stats"]["sfx_calls"] == 2  # neither cached on first run
    assert manifest["stats"]["sfx_cached"] == 0

    # Each rendered asset is a non-empty MP3 in out_dir.
    for s in manifest["scenes"]:
        for a in s["audio_assets"]:
            p = Path(a["asset_path"])
            assert p.exists() and p.stat().st_size > 0, p

    # Real fingerprints landed in the manifest.
    fps = [
        a["fingerprint"]
        for s in manifest["scenes"] for a in s["audio_assets"]
        if a["fingerprint"]
    ]
    assert fps and all(len(fp) == 16 for fp in fps)

    # SDK call shape — model + duration + loop pass through.
    create_calls = [e for e in fake.log if e[0] == "sound_effect.create"]
    assert len(create_calls) == 2
    models = {c[1] for c in create_calls}
    assert models == {SFX_MODEL}
    print("OK test_pipeline_live_path_with_director_monkeypatched")


def test_pipeline_dedupes_identical_briefs_across_scenes(tmp_path, monkeypatch):
    """When two scenes produce the same brief, the second is a cache hit."""
    import ai_editor.audio_pipeline as ap_mod
    import ai_editor.sfx_client as sfx_mod

    from ai_editor.audio_director import AtmosphereBrief

    def _identical_director(scene, *, transcript, frames, cache_dir,
                            anthropic_client, model, dry_run):
        # Every scene asks for the same atmosphere — same prompt, same dur.
        return AudioBrief(
            scene_id=scene.get("scene_id") or "scene_xx",
            needs_atmosphere=True,
            atmosphere_brief=AtmosphereBrief(
                prompt="low forest ambience, dawn, distant water",
                reasoning="dedupe test",
            ),
            hard_fx_briefs=[],
            confidence=0.5,
            frames_used=[],
            cached=False,
        )

    monkeypatch.setattr(ap_mod, "generate_brief", _identical_director)

    def _fake_download(url, dest):
        dest.write_bytes(b"ID3-mp3-payload")

    monkeypatch.setattr(sfx_mod, "_download", _fake_download)

    fake = _FakeRunway()
    sfx_client = SFXClient(runway_client=fake, cache_dir=tmp_path / "sfxcache")

    # Make all three scenes identical length so identical briefs really
    # do collide on (prompt, duration, loop).
    report = {
        "video_path": str(tmp_path / "fake.mov"),
        "duration": 60.0,
        "transcript": "",
        "scenes": [
            {"number": i + 1, "name": f"Scene {i}",
             "timecode_in": "00:00:00", "timecode_out": "00:00:20",
             "description": "", "story_purpose": "",
             "start_seconds": 0.0, "end_seconds": 20.0}
            for i in range(3)
        ],
    }
    report_path = tmp_path / "dup.json"
    report_path.write_text(json.dumps(report))

    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        director_cache_dir=tmp_path / "ad",
        sfx_client=sfx_client,
        dry_run=False,
    )

    # 3 atmosphere assets in the manifest, but only 1 underlying SFX call.
    assert manifest["stats"]["n_atmospheres"] == 3
    assert manifest["stats"]["sfx_calls"] == 1
    assert manifest["stats"]["sfx_cached"] == 2
    create_calls = [e for e in fake.log if e[0] == "sound_effect.create"]
    assert len(create_calls) == 1
    print("OK test_pipeline_dedupes_identical_briefs_across_scenes")


def test_pipeline_max_sfx_caps_calls(tmp_path, monkeypatch):
    """max_sfx hard cap stops further calls; cached calls don't count."""
    import ai_editor.audio_pipeline as ap_mod
    import ai_editor.sfx_client as sfx_mod

    from ai_editor.audio_director import HardFxBrief

    counter = {"i": 0}

    def _changing_director(scene, *, transcript, frames, cache_dir,
                           anthropic_client, model, dry_run):
        # Each scene has a UNIQUE prompt to defeat content cache.
        counter["i"] += 1
        return AudioBrief(
            scene_id=scene.get("scene_id") or "scene_xx",
            needs_atmosphere=False,
            atmosphere_brief=None,
            hard_fx_briefs=[
                HardFxBrief(
                    start_offset_in_scene=1.0,
                    duration=1.0,
                    prompt=f"unique fx prompt #{counter['i']}",
                    reasoning="",
                ),
            ],
            confidence=0.5,
            frames_used=[],
            cached=False,
        )

    monkeypatch.setattr(ap_mod, "generate_brief", _changing_director)
    monkeypatch.setattr(
        sfx_mod, "_download",
        lambda url, dest: dest.write_bytes(b"mp3"),
    )
    fake = _FakeRunway()
    sfx_client = SFXClient(runway_client=fake, cache_dir=tmp_path / "sfxcache")

    report_path = _fixture_report(tmp_path)
    manifest = build_audio_track(
        report_path=report_path,
        out_dir=tmp_path / "sfx",
        director_cache_dir=tmp_path / "ad",
        sfx_client=sfx_client,
        max_sfx=2,
        dry_run=False,
    )
    assert manifest["stats"]["sfx_calls"] == 2
    # Third scene's hard FX should be skipped.
    skipped_assets = [
        a for s in manifest["scenes"] for a in s["audio_assets"]
        if a.get("skipped_reason")
    ]
    assert any(
        "max_sfx" in (a["skipped_reason"] or "") for a in skipped_assets
    )
    print("OK test_pipeline_max_sfx_caps_calls")


# --- Direct-invocation smoke runner --------------------------------------


def _run_all():
    """Invoke each test via tmp_path/monkeypatch shims so direct-mode runs.

    pytest in the StudioOS .venv hangs (per Charlie/Echo handoffs); this
    lets us run the suite without it.
    """
    import shutil
    import tempfile

    class _MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value, raising=True):
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def finalize(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)

    # No-fixture tests.
    test_scene_id_zero_indexed_two_digits()
    test_parse_timecode_handles_hh_mm_ss()
    test_stub_brief_atmosphere_for_forest_scene()
    test_stub_brief_no_match_returns_empty()
    test_normalize_payload_clamps_and_trims()
    test_normalize_payload_drops_empty_atmosphere()
    test_parse_first_json_object_handles_fences()
    test_clamp_duration_respects_runway_window()
    test_sfx_fingerprint_is_content_keyed()

    # tmp_path-only tests.
    tmp_only = [
        test_generate_brief_dry_run_caches,
        test_brief_to_json_is_serializable,
        test_sfx_client_rejects_empty_prompt,
        test_pipeline_dry_run_emits_frozen_manifest_shape,
        test_pipeline_long_scene_uses_loop_atmosphere,
        test_pipeline_short_scene_no_loop,
        test_pipeline_skip_sfx_keeps_briefs,
    ]
    for fn in tmp_only:
        td = Path(tempfile.mkdtemp(prefix="ap_"))
        try:
            fn(td)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    # tmp_path + monkeypatch tests.
    monkey_fixtures = [
        test_sfx_client_caches_per_brief,
        test_pipeline_live_path_with_director_monkeypatched,
        test_pipeline_dedupes_identical_briefs_across_scenes,
        test_pipeline_max_sfx_caps_calls,
    ]
    for fn in monkey_fixtures:
        td = Path(tempfile.mkdtemp(prefix="ap_"))
        mp = _MonkeyPatch()
        try:
            fn(td, mp)
        finally:
            mp.finalize()
            shutil.rmtree(td, ignore_errors=True)

    print("\nALL audio pipeline tests passed.")


if __name__ == "__main__":
    _run_all()
