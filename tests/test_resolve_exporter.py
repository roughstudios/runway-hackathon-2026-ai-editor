"""Smoke tests for the Stage 5 Resolve exporter (Hotel-v3).

Dry mode by default — fixtures stand in for the four upstream
manifests, ``copy_media=False`` in tests writes empty placeholder asset
files so we can validate the bundle layout without dragging real
gigabyte-sized media around.

Run directly (``python tests/test_resolve_exporter.py``) — the StudioOS
.venv has a known pytest hang and every other Stage's tests follow the
same direct-invocation pattern.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

# Make the repo root importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_editor.resolve_exporter import (  # noqa: E402
    LUA_SCRIPT_NAME,
    bundle_for_resolve,
    discover_manifests,
)
from ai_editor.resolve_lua_template import (  # noqa: E402
    TRACK_ORDER,
    render_importer_lua,
    render_manifest_lua,
)


# --- Fixture builders ----------------------------------------------------


REPORT_ID = "canyons_test"


def _write_commentary(out_root: Path, source: Path, total: float) -> Path:
    """Stage 1 layout: ``output/commentary/commentary_segments.json``."""
    d = out_root / "commentary"
    d.mkdir(parents=True, exist_ok=True)
    track = d / "commentary.wav"
    track.write_bytes(b"RIFF-fake-wav")
    payload = {
        "report_path": str(out_root.parent / "reports" / f"{REPORT_ID}.json"),
        "track_path": str(track),
        "total_duration_sec": total,
        "voice_preset": "Vincent",
        "model": "eleven_multilingual_v2",
        "dry_run": True,
        "segments": [],
    }
    p = d / "commentary_segments.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def _write_visualizations(out_root: Path) -> Path:
    """Stage 2 layout: ``output/visualizations/visualizations.json``."""
    d = out_root / "visualizations"
    d.mkdir(parents=True, exist_ok=True)
    transformed_dir = d / "transformed"
    transformed_dir.mkdir(parents=True, exist_ok=True)
    v0 = transformed_dir / "emotional.0.mp4"
    v1 = transformed_dir / "emotional.1.mp4"
    v0.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    v1.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    payload = {
        "report_path": "fake.json",
        "source_video": None,
        "total_duration_sec": 600.0,
        "visualizations": [
            {
                "comment_id": "emotional.0",
                "start_seconds": 31.67,
                "duration_sec": 5,
                "narration_text": "...",
                "label": "SUGGESTION",
                "vfx_category": "atmospheric",
                "transformed_video": str(v0),
                "source_segment": None,
                "skipped_reason": None,
            },
            {
                "comment_id": "emotional.1",
                "start_seconds": 192.78,
                "duration_sec": 5,
                "narration_text": "...",
                "label": "SUGGESTION",
                "vfx_category": "volumetric_light",
                "transformed_video": str(v1),
                "source_segment": None,
                "skipped_reason": None,
            },
            # This one is brief-only / skipped — exporter must drop it.
            {
                "comment_id": "emotional.2",
                "start_seconds": 249.6,
                "duration_sec": 5,
                "narration_text": "...",
                "label": "SUGGESTION",
                "transformed_video": None,
                "skipped_reason": "skip_aleph=True",
            },
        ],
        "narrations": [],
        "stats": {"n_suggestions": 2, "n_observations": 0,
                   "aleph_calls": 0, "aleph_cached": 0},
    }
    p = d / "visualizations.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def _write_avatar(out_root: Path, report_id: str) -> Path:
    """Stage 3 layout: ``output/avatar_segments/<report_id>/manifest.json``."""
    d = out_root / "avatar_segments" / report_id
    d.mkdir(parents=True, exist_ok=True)
    a0 = d / "seg_00.mp4"
    a1 = d / "seg_01.mp4"
    a0.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    a1.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    payload = {
        "report_id": report_id,
        "report_path": "fake.json",
        "segments_json": "fake.json",
        "character_id": "char-x",
        "voice_preset": "vincent",
        "model": "gwm1_avatars",
        "dry_run": False,
        "stats": {"n_total": 2, "n_animated": 2,
                   "runway_calls": 2, "cached_calls": 0, "skipped": 0},
        "segments": [
            {
                "comment_id": "emotional.0",
                "start_seconds": 31.67,
                "end_seconds": 40.0,
                "duration_sec": 8.33,
                "narration_text": "...",
                "mp4_path": str(a0),
                "cached": False, "task_id": "t1", "fingerprint": "fp1",
                "skipped_reason": None,
            },
            {
                "comment_id": "emotional.1",
                "start_seconds": 192.78,
                "end_seconds": 204.0,
                "duration_sec": 11.22,
                "narration_text": "...",
                "mp4_path": str(a1),
                "cached": False, "task_id": "t2", "fingerprint": "fp2",
                "skipped_reason": None,
            },
        ],
    }
    p = d / "manifest.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def _write_sfx(out_root: Path, report_id: str) -> Path:
    """Stage 4 layout: ``output/sfx/<report_id>/manifest.json``.

    Includes one long atmosphere (loop=True, tile target = 75s) and one
    short atmosphere (loop=False) and one hard FX one-shot.
    """
    d = out_root / "sfx" / report_id
    d.mkdir(parents=True, exist_ok=True)
    atm_long = d / "scene_00_atmos.mp3"
    atm_short = d / "scene_01_atmos.mp3"
    fx = d / "scene_02_fx_01.mp3"
    for f in (atm_long, atm_short, fx):
        f.write_bytes(b"ID3-fake-mp3")
    payload = {
        "report_id": report_id,
        "report_path": "fake.json",
        "source_video": "fake.mov",
        "total_duration_sec": 600.0,
        "model": "eleven_text_to_sound_v2",
        "scenes": [
            {
                "scene_id": "scene_00",
                "scene_number": 1,
                "scene_name": "Long atmosphere scene",
                "scene_start_seconds": 0.0,
                "scene_end_seconds": 75.0,
                "audio_assets": [{
                    "asset_path": str(atm_long),
                    "fingerprint": "fp-atm-long",
                    "cached": False,
                    "rendered": True,
                    "skipped_reason": None,
                    "type": "atmosphere",
                    "track": "A3",
                    "start_offset_in_scene": 0.0,
                    "duration": 75.0,    # in-scene length
                    "loop": True,
                    "prompt": "low forest ambience",
                }],
            },
            {
                "scene_id": "scene_01",
                "scene_number": 2,
                "scene_name": "Short atmosphere scene",
                "scene_start_seconds": 75.0,
                "scene_end_seconds": 95.0,
                "audio_assets": [{
                    "asset_path": str(atm_short),
                    "fingerprint": "fp-atm-short",
                    "cached": False,
                    "rendered": True,
                    "skipped_reason": None,
                    "type": "atmosphere",
                    "track": "A3",
                    "start_offset_in_scene": 0.0,
                    "duration": 20.0,    # in-scene length, fits in 30s cap
                    "loop": False,
                    "prompt": "wind through pines",
                }],
            },
            {
                "scene_id": "scene_02",
                "scene_number": 3,
                "scene_name": "Hard FX scene",
                "scene_start_seconds": 95.0,
                "scene_end_seconds": 105.0,
                "audio_assets": [{
                    "asset_path": str(fx),
                    "fingerprint": "fp-fx",
                    "cached": False,
                    "rendered": True,
                    "skipped_reason": None,
                    "type": "hard_fx",
                    "track": "A4",
                    "start_offset_in_scene": 5.3,
                    "duration": 1.8,
                    "loop": False,
                    "prompt": "soft footstep on gravel",
                }],
            },
            {
                # Skipped scene — should not contribute any assets.
                "scene_id": "scene_03",
                "scene_number": 4,
                "scene_name": "Skipped",
                "scene_start_seconds": 105.0,
                "scene_end_seconds": 115.0,
                "audio_assets": [{
                    "asset_path": "",
                    "fingerprint": "",
                    "cached": False,
                    "rendered": False,
                    "skipped_reason": "max_sfx budget exhausted",
                    "type": "hard_fx",
                    "track": "A4",
                    "start_offset_in_scene": 0.0,
                    "duration": 1.0,
                    "loop": False,
                    "prompt": "",
                }],
            },
        ],
        "stats": {
            "n_scenes": 4, "n_atmospheres": 2, "n_hard_fx": 1,
            "sfx_calls": 3, "sfx_cached": 0, "skipped": 1,
        },
    }
    p = d / "manifest.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def _write_source(tmp: Path) -> Path:
    src = tmp / "raw" / "documentary.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00\x00\x00\x18ftypqt  fake-source-mov-bytes")
    return src


# --- Lua template tests --------------------------------------------------


def test_lua_value_renders_dict_with_in_keyword():
    from ai_editor.resolve_lua_template import _lua_value

    rendered = _lua_value({"in": 0.0, "out": 5.0, "label": "x"}, indent=0)
    # 'in' is a Lua keyword and must be string-keyed.
    assert "[\"in\"]" in rendered
    assert "= 0.0" in rendered
    # Plain identifiers stay bare.
    assert re.search(r"\blabel\s*=\s*\"x\"", rendered), rendered
    print("OK test_lua_value_renders_dict_with_in_keyword")


def test_lua_value_escapes_strings():
    from ai_editor.resolve_lua_template import _lua_value

    out = _lua_value('quoted "x" \\ and \n newline', indent=0)
    assert out.startswith("\"") and out.endswith("\"")
    assert "\\\"" in out
    assert "\\\\" in out
    assert "\\n" in out
    print("OK test_lua_value_escapes_strings")


def test_render_manifest_lua_balanced_braces():
    rendered = render_manifest_lua({
        "report_id": "x",
        "tracks": {"V1": [{"path": "p", "in": 0, "out": 10, "timeline_in": 0}]},
    })
    assert rendered.startswith("-- Generated") or rendered.startswith("--")
    assert "return {" in rendered
    # Balanced braces.
    assert rendered.count("{") == rendered.count("}")
    # Top-level table opens once and closes once at end.
    assert rendered.rstrip().endswith("}")
    print("OK test_render_manifest_lua_balanced_braces")


def test_render_importer_lua_has_required_hooks():
    lua = render_importer_lua(
        bundle_dir_posix="R:/runway-hackathon-2026-ai-editor/output/resolve_bundle/x",
        report_id="x",
    )
    must_have = [
        "bmd.scriptapp(\"Resolve\")",
        "GetMediaPool",
        "ImportMedia",
        "CreateEmptyTimeline",
        "AppendToTimeline",
        "AddTrack",
        "AddSubFolder",
        "STUDIOOS_AIEDIT_BUNDLE",
        "manifest.lua",
        "tile_to_seconds",
    ]
    for token in must_have:
        assert token in lua, f"importer Lua missing token: {token}"
    # Balanced braces.
    assert lua.count("{") == lua.count("}"), "importer Lua brace imbalance"
    print("OK test_render_importer_lua_has_required_hooks")


# --- discover_manifests --------------------------------------------------


def test_discover_manifests_handles_partial(tmp_path):
    # Only the SFX manifest exists; the other three are missing.
    out_root = tmp_path / "output"
    _write_sfx(out_root, REPORT_ID)
    found = discover_manifests(report_id=REPORT_ID, output_root=out_root)
    assert found.sfx is not None
    assert found.commentary is None
    assert found.visualizations is None
    assert found.avatar is None
    assert set(found.missing) == {"commentary", "visualizations", "avatar"}
    print("OK test_discover_manifests_handles_partial")


# --- bundle_for_resolve --------------------------------------------------


def test_bundle_full_layout(tmp_path):
    out_root = tmp_path / "output"
    _write_commentary(out_root, source=tmp_path / "src.mov", total=600.0)
    _write_visualizations(out_root)
    _write_avatar(out_root, REPORT_ID)
    _write_sfx(out_root, REPORT_ID)
    src = _write_source(tmp_path)

    bundle_root = tmp_path / "bundle_root"

    bundle = bundle_for_resolve(
        report_id=REPORT_ID,
        source_video_path=src,
        bundle_root=bundle_root,
        output_root=out_root,
    )

    # Layout — every artifact the brief requires.
    assert (bundle / "manifest.json").exists()
    assert (bundle / "manifest.lua").exists()
    assert (bundle / LUA_SCRIPT_NAME).exists()
    assert (bundle / "source.mov").exists() and (bundle / "source.mov").stat().st_size > 0
    assert (bundle / "commentary_track.wav").exists()
    assert (bundle / "visualizations" / "emotional.0.mp4").exists()
    assert (bundle / "visualizations" / "emotional.1.mp4").exists()
    assert (bundle / "avatar" / "seg_00.mp4").exists()
    assert (bundle / "avatar" / "seg_01.mp4").exists()
    assert (bundle / "sfx" / "scene_00_atmos.mp3").exists()
    assert (bundle / "sfx" / "scene_01_atmos.mp3").exists()
    assert (bundle / "sfx" / "scene_02_fx_01.mp3").exists()

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["report_id"] == REPORT_ID
    assert manifest["source_video"] == "source.mov"
    assert set(manifest["tracks"].keys()) == set(TRACK_ORDER)

    v1 = manifest["tracks"]["V1"]
    a1 = manifest["tracks"]["A1"]
    assert len(v1) == 1 and v1[0]["path"] == "source.mov"
    assert v1[0]["timeline_in"] == 0.0
    assert v1[0]["out"] == 600.0
    assert len(a1) == 1 and a1[0]["path"] == "source.mov"

    # V2: two visualizations (the third is brief-only and dropped).
    assert len(manifest["tracks"]["V2"]) == 2
    assert manifest["tracks"]["V2"][0]["timeline_in"] == 31.67

    # V3: two avatar segments.
    assert len(manifest["tracks"]["V3"]) == 2
    assert manifest["tracks"]["V3"][0]["timeline_in"] == 31.67

    # A2: commentary track.
    a2 = manifest["tracks"]["A2"]
    assert len(a2) == 1
    assert a2[0]["path"] == "commentary_track.wav"
    assert a2[0]["out"] == 600.0

    # A3: two atmospheres — long one looped, short one not.
    a3 = manifest["tracks"]["A3"]
    assert len(a3) == 2
    long_atm = next(a for a in a3 if a["loop"])
    short_atm = next(a for a in a3 if not a["loop"])
    # Long: rendered clip clipped at 30s, tile target = scene's 75s.
    assert long_atm["duration"] == 30.0
    assert long_atm["tile_to_seconds"] == 75.0
    assert long_atm["timeline_in"] == 0.0
    # Short: rendered clip = in-scene length, no tiling.
    assert short_atm["duration"] == 20.0
    assert short_atm["tile_to_seconds"] == 20.0
    assert short_atm["timeline_in"] == 75.0

    # A4: one hard FX, placed at scene_start + offset.
    a4 = manifest["tracks"]["A4"]
    assert len(a4) == 1
    assert abs(a4[0]["timeline_in"] - (95.0 + 5.3)) < 1e-6
    assert a4[0]["duration"] == 1.8

    # Stats sanity.
    stats = manifest["stats"]
    assert stats["n_visualizations"] == 2
    assert stats["n_avatar_segments"] == 2
    assert stats["n_atmospheres"] == 2
    assert stats["n_hard_fx"] == 1
    assert stats["missing_manifests"] == []

    # Lua manifest sidecar parses-y: balanced braces and the
    # ``in`` keyword key gets the string-key escape.
    lua_data = (bundle / "manifest.lua").read_text(encoding="utf-8")
    assert lua_data.count("{") == lua_data.count("}")
    assert "[\"in\"]" in lua_data
    # ``out`` isn't reserved so it stays bare.
    assert re.search(r"\bout\s*=\s*", lua_data)

    # Lua importer references the right asset paths and the bundle dir.
    importer = (bundle / LUA_SCRIPT_NAME).read_text(encoding="utf-8")
    bundle_posix = bundle.resolve().as_posix()
    assert bundle_posix in importer
    assert importer.count("{") == importer.count("}")
    print("OK test_bundle_full_layout")


def test_bundle_missing_manifests_dont_crash(tmp_path):
    """Hotel-v3 must produce at least source-only V1+A1 even when every
    upstream stage is missing. The Lua importer can still build a one-
    track timeline that round-trips the source cut.
    """
    out_root = tmp_path / "output"  # nothing inside
    src = _write_source(tmp_path)

    bundle = bundle_for_resolve(
        report_id="empty",
        source_video_path=src,
        bundle_root=tmp_path / "bundle_root",
        output_root=out_root,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    # Source still wired up.
    assert manifest["tracks"]["V1"][0]["path"] == "source.mov"
    assert manifest["tracks"]["A1"][0]["path"] == "source.mov"
    # Other tracks empty.
    for t in ("V2", "V3", "A2", "A3", "A4"):
        assert manifest["tracks"][t] == []
    # Stats record what was missing.
    miss = set(manifest["stats"]["missing_manifests"])
    assert miss == {"commentary", "visualizations", "avatar", "sfx"}
    # Bundle still has the importer Lua + manifest.lua sidecar.
    assert (bundle / LUA_SCRIPT_NAME).exists()
    assert (bundle / "manifest.lua").exists()
    print("OK test_bundle_missing_manifests_dont_crash")


def test_bundle_no_copy_mode_writes_placeholders(tmp_path):
    out_root = tmp_path / "output"
    _write_sfx(out_root, REPORT_ID)
    src = _write_source(tmp_path)

    bundle = bundle_for_resolve(
        report_id=REPORT_ID,
        source_video_path=src,
        bundle_root=tmp_path / "bundle_root",
        output_root=out_root,
        copy_media=False,
    )
    # Source is a 0-byte placeholder when copy_media=False.
    assert (bundle / "source.mov").exists()
    assert (bundle / "source.mov").stat().st_size == 0
    # Lua importer still references the bundle path.
    importer = (bundle / LUA_SCRIPT_NAME).read_text(encoding="utf-8")
    assert "AppendToTimeline" in importer
    print("OK test_bundle_no_copy_mode_writes_placeholders")


def test_bundle_overrides_explicit_manifest_paths(tmp_path):
    """If the upstream manifests live somewhere odd, --commentary etc.
    should win over auto-discovery.
    """
    odd = tmp_path / "weird_place"
    odd.mkdir()
    src = _write_source(tmp_path)

    # Build a hand-rolled commentary manifest in an off-tree location.
    track = odd / "narration.wav"
    track.write_bytes(b"RIFF-fake-wav")
    cs = odd / "segments.json"
    cs.write_text(json.dumps({
        "report_path": "n/a",
        "track_path": str(track),
        "total_duration_sec": 42.0,
        "voice_preset": "Vincent",
        "model": "eleven",
        "dry_run": True,
        "segments": [],
    }))

    bundle = bundle_for_resolve(
        report_id="override",
        source_video_path=src,
        bundle_root=tmp_path / "bundle_root",
        output_root=tmp_path / "no_such_output",  # would 404 without the override
        commentary_segments=cs,
    )
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["tracks"]["A2"]) == 1
    assert manifest["tracks"]["A2"][0]["path"] == "commentary_track.wav"
    assert manifest["total_duration_sec"] == 42.0
    print("OK test_bundle_overrides_explicit_manifest_paths")


# --- Direct-invocation runner --------------------------------------------


def _run_all():
    test_lua_value_renders_dict_with_in_keyword()
    test_lua_value_escapes_strings()
    test_render_manifest_lua_balanced_braces()
    test_render_importer_lua_has_required_hooks()

    fixtures = [
        test_discover_manifests_handles_partial,
        test_bundle_full_layout,
        test_bundle_missing_manifests_dont_crash,
        test_bundle_no_copy_mode_writes_placeholders,
        test_bundle_overrides_explicit_manifest_paths,
    ]
    import shutil
    for fn in fixtures:
        td = Path(tempfile.mkdtemp(prefix="hotel_"))
        try:
            fn(td)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    print("\nALL resolve exporter tests passed.")


if __name__ == "__main__":
    _run_all()
