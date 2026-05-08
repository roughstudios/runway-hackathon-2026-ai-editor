"""Audio pipeline — Stage 4 orchestrator.

Reads a Film Analyzer report, walks the ``scenes`` array, runs the
:mod:`ai_editor.audio_director` per scene to author atmosphere + hard
FX briefs, and calls :class:`ai_editor.sfx_client.SFXClient` to render
each brief into an MP3. The result is a manifest JSON that the
Hotel-v3 Resolve exporter consumes — schema is FROZEN (see below).

Manifest schema (frozen contract with Hotel-v3):

    {
      "report_id": "<report stem>",
      "report_path": "<input report .json>",
      "source_video": "<from report['video_path']>",
      "total_duration_sec": <from report['duration']>,
      "model": "eleven_text_to_sound_v2",
      "dry_run": false,
      "scenes": [
        {
          "scene_id": "scene_00",
          "scene_number": 1,
          "scene_name": "Pre-Race Setting",
          "scene_start_seconds": 0.0,
          "scene_end_seconds": 75.0,
          "audio_assets": [
            {
              "type": "atmosphere",
              "track": "A3",
              "asset_path": "output/sfx/<report_id>/scene_00_atmos.mp3",
              "start_offset_in_scene": 0.0,
              "duration": 75.0,
              "loop": true,
              "prompt": "low forest ambience...",
              "fingerprint": "abc123...",
              "cached": false
            },
            {
              "type": "hard_fx",
              "track": "A4",
              "asset_path": "output/sfx/<report_id>/scene_00_fx_01.mp3",
              "start_offset_in_scene": 5.3,
              "duration": 1.8,
              "loop": false,
              "prompt": "soft footstep on gravel",
              "fingerprint": "def456...",
              "cached": false
            }
          ],
          "skipped_reason": null,
          "frames_used": ["..."]
        }
      ],
      "stats": { "n_scenes": 9, "n_atmospheres": 4, "n_hard_fx": 6,
                 "sfx_calls": 8, "sfx_cached": 2, "skipped": 0 }
    }

Atmosphere semantics:
  - Track A3 by Resolve convention (dialogue A1-A2, atmosphere A3,
    hard FX A4).
  - duration in the manifest = the in-scene playback length the
    asset should cover. For scenes longer than the Runway 30s cap,
    we generate a 30s loop=True clip and let Hotel-v3 tile it.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from ai_editor.audio_director import (
    DEFAULT_DIRECTOR_MODEL,
    AudioBrief,
    SFX_MAX_DURATION_SEC,
    brief_to_json,
    find_frames_at_timecode,
    generate_brief,
)

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "output" / "sfx"
DEFAULT_CACHE_DIR = Path(".cache") / "audio_director"
DEFAULT_FRAMES_DIR = Path(
    "R:/--CODE--/StudioOS-v1/data/learndocumentary/analysis_review/frames_canyons-100-miles-1-3"
)

DEFAULT_MAX_SFX = 24

ATMOSPHERE_TRACK = "A3"
HARD_FX_TRACK = "A4"


def build_audio_track(
    *,
    report_path: Path,
    out_dir: Optional[Path] = None,
    frames_dir: Optional[Path] = None,
    director_model: str = DEFAULT_DIRECTOR_MODEL,
    director_cache_dir: Path = DEFAULT_CACHE_DIR,
    sfx_client=None,
    anthropic_client=None,
    limit: Optional[int] = None,
    max_sfx: int = DEFAULT_MAX_SFX,
    skip_sfx: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run Stage 4 end-to-end. Returns the manifest dict (also written
    to disk at out_dir/'manifest.json').

    Modes:
      dry_run=True     No Anthropic, no Runway. Audio Director uses the
                       deterministic stub; SFX assets are zero-byte
                       placeholders. Used in tests.
      skip_sfx=True    Audio Director runs but SFX calls are skipped.
                       Useful for inspecting briefs before burning
                       Runway credits.
      limit=N          Only process the first N scenes (cheap testing).
      max_sfx=N        Hard cap on SFX create calls per invocation.
                       Cache hits don't count.
    """
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    scenes_raw: List[dict] = report.get("scenes") or []
    if not scenes_raw:
        raise RuntimeError(f"No scenes in {report_path}")
    if limit is not None:
        scenes_raw = scenes_raw[:limit]

    transcript = str(report.get("transcript") or "")
    total_duration = float(report.get("duration") or 0.0)
    report_id = report_path.stem

    if out_dir is None:
        out_dir = DEFAULT_OUT_DIR / report_id
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames_dir = Path(frames_dir) if frames_dir else DEFAULT_FRAMES_DIR

    if not dry_run and not skip_sfx and sfx_client is None:
        from ai_editor.sfx_client import SFXClient
        sfx_client = SFXClient()

    scenes_out: List[dict] = []
    sfx_calls = 0
    sfx_cached = 0
    skipped_count = 0
    n_atmospheres = 0
    n_hard_fx = 0

    for index, scene_raw in enumerate(scenes_raw):
        scene = _enrich_scene(
            scene_raw, index=index, total_duration=total_duration,
        )
        scene_id = scene["scene_id"]
        scene_dur = max(0.0, scene["end_seconds"] - scene["start_seconds"])

        # Find reference frames at the scene's midpoint.
        midpoint = scene["start_seconds"] + scene_dur / 2.0
        frames = []
        if frames_dir.exists():
            frames = find_frames_at_timecode(frames_dir, midpoint, window=1)

        try:
            brief = generate_brief(
                {**scene, "scene_id": scene_id},
                transcript=transcript,
                frames=frames,
                cache_dir=director_cache_dir,
                anthropic_client=anthropic_client,
                model=director_model,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced into manifest
            LOG.exception("Audio director failed for %s: %s", scene_id, exc)
            scenes_out.append({
                "scene_id": scene_id,
                "scene_number": scene.get("number"),
                "scene_name": scene.get("name"),
                "scene_start_seconds": scene["start_seconds"],
                "scene_end_seconds": scene["end_seconds"],
                "audio_assets": [],
                "skipped_reason": f"director error: {exc}",
                "frames_used": [str(f) for f in frames],
                "brief": None,
                "confidence": 0.0,
            })
            skipped_count += 1
            continue

        audio_assets: List[dict] = []
        scene_skipped: Optional[str] = None

        # --- Atmosphere ---------------------------------------------------
        if brief.needs_atmosphere and brief.atmosphere_brief:
            atm_request_dur = (
                min(scene_dur, SFX_MAX_DURATION_SEC)
                if scene_dur > 0 else SFX_MAX_DURATION_SEC
            )
            atm_loop = scene_dur > SFX_MAX_DURATION_SEC
            asset_path = out_dir / f"{scene_id}_atmos.mp3"
            asset_record = _maybe_render_asset(
                kind="atmosphere",
                prompt=brief.atmosphere_brief.prompt,
                request_duration=atm_request_dur,
                loop=atm_loop,
                asset_path=asset_path,
                sfx_client=sfx_client,
                dry_run=dry_run,
                skip_sfx=skip_sfx,
                budget_left=max_sfx - sfx_calls,
            )
            asset_record.update({
                "type": "atmosphere",
                "track": ATMOSPHERE_TRACK,
                "start_offset_in_scene": 0.0,
                # In-scene playback length, not the rendered clip length.
                # Hotel-v3 tiles when loop=True and clip < duration.
                "duration": round(scene_dur if scene_dur > 0 else atm_request_dur, 3),
                "loop": atm_loop,
                "prompt": brief.atmosphere_brief.prompt,
                "reasoning": brief.atmosphere_brief.reasoning,
            })
            audio_assets.append(asset_record)
            n_atmospheres += 1
            if asset_record.get("rendered") and not dry_run:
                if asset_record.get("cached"):
                    sfx_cached += 1
                else:
                    sfx_calls += 1

        # --- Hard FX ------------------------------------------------------
        for i, fx in enumerate(brief.hard_fx_briefs, start=1):
            asset_path = out_dir / f"{scene_id}_fx_{i:02d}.mp3"
            asset_record = _maybe_render_asset(
                kind="hard_fx",
                prompt=fx.prompt,
                request_duration=fx.duration,
                loop=False,
                asset_path=asset_path,
                sfx_client=sfx_client,
                dry_run=dry_run,
                skip_sfx=skip_sfx,
                budget_left=max_sfx - sfx_calls,
            )
            asset_record.update({
                "type": "hard_fx",
                "track": HARD_FX_TRACK,
                "start_offset_in_scene": round(fx.start_offset_in_scene, 3),
                "duration": round(fx.duration, 3),
                "loop": False,
                "prompt": fx.prompt,
                "reasoning": fx.reasoning,
            })
            audio_assets.append(asset_record)
            n_hard_fx += 1
            if asset_record.get("rendered") and not dry_run:
                if asset_record.get("cached"):
                    sfx_cached += 1
                else:
                    sfx_calls += 1

        scenes_out.append({
            "scene_id": scene_id,
            "scene_number": scene.get("number"),
            "scene_name": scene.get("name"),
            "scene_start_seconds": round(scene["start_seconds"], 3),
            "scene_end_seconds": round(scene["end_seconds"], 3),
            "audio_assets": _order_assets_for_manifest(audio_assets),
            "skipped_reason": scene_skipped,
            "frames_used": [str(f) for f in frames],
            "brief": brief_to_json(brief),
            "confidence": brief.confidence,
        })

    manifest = {
        "report_id": report_id,
        "report_path": str(report_path),
        "source_video": report.get("video_path"),
        "total_duration_sec": total_duration,
        "model": "eleven_text_to_sound_v2",
        "director_model": director_model,
        "frames_dir": str(frames_dir),
        "dry_run": dry_run,
        "skip_sfx": skip_sfx,
        "max_sfx": max_sfx,
        "scenes": scenes_out,
        "stats": {
            "n_scenes": len(scenes_out),
            "n_atmospheres": n_atmospheres,
            "n_hard_fx": n_hard_fx,
            "sfx_calls": sfx_calls,
            "sfx_cached": sfx_cached,
            "skipped": skipped_count,
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    LOG.info(
        "Audio manifest %s scenes=%d atmos=%d fx=%d sfx_calls=%d cached=%d",
        manifest_path, len(scenes_out), n_atmospheres, n_hard_fx,
        sfx_calls, sfx_cached,
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


# --- internals -----------------------------------------------------------


def _enrich_scene(scene: dict, *, index: int, total_duration: float) -> dict:
    """Add ``scene_id``, ``index``, ``start_seconds``, ``end_seconds``,
    and ``_total_duration_seconds`` if the report doesn't already carry
    seconds — newer Film Analyzer reports do; older ones only have
    timecode strings.
    """
    out = dict(scene)
    out["index"] = index
    out["scene_id"] = f"scene_{index:02d}"
    if "start_seconds" not in out:
        s = _parse_timecode(out.get("timecode_in") or "")
        if s is not None:
            out["start_seconds"] = s
    if "end_seconds" not in out:
        e = _parse_timecode(out.get("timecode_out") or "")
        if e is not None:
            out["end_seconds"] = e
    out.setdefault("start_seconds", 0.0)
    out.setdefault("end_seconds", out["start_seconds"])
    if total_duration:
        out["_total_duration_seconds"] = total_duration
    return out


def _parse_timecode(tc: str) -> Optional[float]:
    if not tc:
        return None
    parts = tc.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        return None
    return None


def _maybe_render_asset(
    *,
    kind: str,
    prompt: str,
    request_duration: float,
    loop: bool,
    asset_path: Path,
    sfx_client,
    dry_run: bool,
    skip_sfx: bool,
    budget_left: int,
) -> dict:
    """Render one asset to disk via SFXClient, with skip/cache handling.

    Returns the asset record stub. The orchestrator merges in the
    descriptive fields (type, track, prompt, etc.) afterwards.
    """
    asset_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        if not asset_path.exists():
            asset_path.write_bytes(b"")
        return {
            "asset_path": str(asset_path),
            "fingerprint": "<dry-run>",
            "cached": False,
            "rendered": True,
            "skipped_reason": None,
        }

    if skip_sfx:
        return {
            "asset_path": "",
            "fingerprint": "",
            "cached": False,
            "rendered": False,
            "skipped_reason": "skip_sfx=True",
        }

    if budget_left <= 0:
        return {
            "asset_path": "",
            "fingerprint": "",
            "cached": False,
            "rendered": False,
            "skipped_reason": "max_sfx budget exhausted",
        }

    try:
        if kind == "atmosphere":
            res = sfx_client.generate_atmosphere(
                prompt, request_duration, loop=loop,
            )
        else:
            res = sfx_client.generate_fx(
                prompt, request_duration, loop=loop,
            )
    except Exception as exc:  # noqa: BLE001 — surfaced into manifest
        LOG.exception("SFX render failed for %s: %s", asset_path.name, exc)
        return {
            "asset_path": "",
            "fingerprint": "",
            "cached": False,
            "rendered": False,
            "skipped_reason": f"sfx error: {exc}",
        }

    # Copy the cache hit into the per-report output dir so the manifest
    # is self-contained for Hotel-v3.
    shutil.copyfile(res.audio_path, asset_path)
    return {
        "asset_path": str(asset_path),
        "fingerprint": res.fingerprint,
        "cached": res.cached,
        "rendered": True,
        "skipped_reason": None,
        "duration_requested": res.duration_requested,
    }


def _order_assets_for_manifest(assets: List[dict]) -> List[dict]:
    """Atmosphere first, then hard_fx by start_offset.

    Resolve readers prefer a stable order; the original insertion order
    already does this, but if we ever interleave we want a defined
    sort.
    """
    return sorted(
        assets,
        key=lambda a: (
            0 if a.get("type") == "atmosphere" else 1,
            float(a.get("start_offset_in_scene") or 0.0),
        ),
    )
