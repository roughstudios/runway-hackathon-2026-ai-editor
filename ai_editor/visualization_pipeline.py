"""Visualization pipeline — Stage 2 orchestrator.

Reads the segments JSON emitted by `commentary_synthesizer.build_commentary`,
classifies each segment, runs the Visual Director on suggestions, extracts
the source segment via ffmpeg at the timecode, runs Aleph (gen4_aleph
video-to-video), and writes a manifest JSON for the player UI.

Output manifest shape (forward-compatible with the player extension):

{
  "report_path": "...",
  "source_video": "...",
  "segments_path": "...",
  "total_duration_sec": 1114.93,
  "voice_preset": "Vincent",
  "max_aleph": 6,
  "visualizations": [
    {
      "comment_id": "emotional.1",
      "start_seconds": 192.0,
      "duration_sec": 5,
      "narration_text": "...",
      "label": "SUGGESTION",
      "vfx_category": "atmospheric",
      "brief": { ...AlephBrief... },
      "transformed_video": "output/.../emotional.1.mp4",
      "source_segment": "output/.../emotional.1__src.mp4",
      "fingerprint": "...",
      "cached": true
    },
    ...observations omitted from this list — see `narrations` below
  ],
  "narrations": [
    { "comment_id": "emotional.0", "start_seconds": 31.67,
      "label": "OBSERVATION", "narration_text": "..." },
    ...
  ],
  "stats": { "n_suggestions": 4, "n_observations": 5, "aleph_calls": 4,
             "aleph_cached": 3 }
}
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from ai_editor.suggestion_classifier import (
    DEFAULT_CLASSIFIER_MODEL,
    classify_segments,
)
from ai_editor.visual_director import (
    DEFAULT_DIRECTOR_MODEL,
    AlephBrief,
    brief_to_json,
    find_frames_at_timecode,
    generate_brief,
)

LOG = logging.getLogger(__name__)

DEFAULT_CLIP_DURATION_SEC = 5
DEFAULT_FRAMES_DIR = Path(
    "R:/--CODE--/StudioOS-v1/data/learndocumentary/analysis_review/frames_canyons-100-miles-1-3"
)
DEFAULT_MAX_ALEPH = 6


def build_visualizations(
    *,
    segments_json_path: Path,
    source_video: Optional[Path],
    out_dir: Path,
    frames_dir: Optional[Path] = None,
    max_aleph: int = DEFAULT_MAX_ALEPH,
    classifier_model: str = DEFAULT_CLASSIFIER_MODEL,
    director_model: str = DEFAULT_DIRECTOR_MODEL,
    aleph_client=None,
    anthropic_client=None,
    dry_run: bool = False,
    skip_aleph: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """Run Stage 2 end-to-end. Returns the manifest dict (also written to
    disk at out_dir / 'visualizations.json').

    Modes:
      dry_run=True      No Anthropic, no Runway, no ffmpeg subprocess
                        (heuristic stub classifier+director, manifest
                        records placeholder paths). Used in tests.
      skip_aleph=True   Run classifier + director + ffmpeg extract, but
                        DON'T call Aleph. Useful for inspecting briefs
                        before burning credits.
      limit=N           Only consider the first N segments (cost cap).
      max_aleph=N       Hard cap on Aleph create calls per invocation
                        (cache hits don't count). Defends against runaway
                        credit burn if classifier over-labels.
    """
    segments_json_path = Path(segments_json_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(segments_json_path.read_text(encoding="utf-8"))
    segments = payload.get("segments") or []
    if limit is not None:
        segments = segments[:limit]
    if not segments:
        raise RuntimeError(f"No segments in {segments_json_path}")

    cache_dir_root = Path(".cache")
    classifier_cache = cache_dir_root / "classifier"
    director_cache = cache_dir_root / "director"
    aleph_cache = cache_dir_root / "aleph"
    src_seg_cache = out_dir / "source_segments"
    src_seg_cache.mkdir(parents=True, exist_ok=True)
    transformed_dir = out_dir / "transformed"
    transformed_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("Classifying %d segments", len(segments))
    classified = classify_segments(
        segments,
        cache_dir=classifier_cache,
        anthropic_client=anthropic_client,
        model=classifier_model,
        dry_run=dry_run,
    )
    by_comment_id = {c.comment_id: c for c in classified}

    visualizations: List[dict] = []
    narrations: List[dict] = []
    aleph_calls = 0
    aleph_cached = 0

    if frames_dir is None:
        frames_dir = DEFAULT_FRAMES_DIR

    if not skip_aleph and not dry_run and aleph_client is None:
        from ai_editor.aleph_client import AlephClient
        aleph_client = AlephClient()

    for seg in segments:
        comment_id = str(seg.get("comment_id") or seg.get("_id") or "")
        cls = by_comment_id.get(comment_id)
        narration_text = str(seg.get("narration_text") or "")
        start_seconds = float(seg.get("start_seconds") or 0.0)

        if cls is None or cls.label != "SUGGESTION" or not cls.needs_visualization:
            narrations.append({
                "comment_id": comment_id,
                "start_seconds": start_seconds,
                "label": cls.label if cls else "OBSERVATION",
                "narration_text": narration_text,
                "reasoning": cls.reasoning if cls else "",
            })
            continue

        seg_with_cat = {**seg, "vfx_category": cls.vfx_category}
        frames = find_frames_at_timecode(frames_dir, start_seconds, window=1)
        brief = generate_brief(
            seg_with_cat,
            frames=frames,
            cache_dir=director_cache,
            anthropic_client=anthropic_client,
            model=director_model,
            dry_run=dry_run,
        )

        transformed_video: Optional[str] = None
        source_segment: Optional[Path] = None
        fingerprint = ""
        cached_flag = False
        skipped_reason: Optional[str] = None

        if dry_run:
            transformed_video = str(transformed_dir / f"{comment_id}.dryrun.mp4")
            source_segment = src_seg_cache / f"{comment_id}.dryrun.mp4"
        elif source_video is None:
            skipped_reason = "no source_video provided"
        elif aleph_calls >= max_aleph:
            skipped_reason = f"max_aleph={max_aleph} reached"
        else:
            try:
                source_segment = _extract_source_segment(
                    source_video=Path(source_video),
                    start_seconds=start_seconds,
                    duration_sec=brief.duration_sec,
                    out_path=src_seg_cache / f"{comment_id}.mp4",
                )
            except _SourceOutOfRange as exc:
                skipped_reason = f"source out of range: {exc}"
                source_segment = None

            if source_segment is not None and not skip_aleph:
                try:
                    res = aleph_client.transform(
                        source_video_path=source_segment,
                        prompt_text=brief.prompt_text,
                        ratio=brief.ratio,
                        duration_sec=brief.duration_sec,
                        cache_dir=aleph_cache,
                    )
                except Exception as exc:  # noqa: BLE001 — surfaced into manifest
                    LOG.exception("Aleph failure for %s: %s", comment_id, exc)
                    skipped_reason = f"aleph error: {exc}"
                else:
                    transformed_video = str(res.video_path)
                    fingerprint = res.fingerprint
                    cached_flag = res.cached
                    aleph_calls += 1
                    if res.cached:
                        aleph_cached += 1
            elif source_segment is not None and skip_aleph:
                skipped_reason = "skip_aleph=True (brief-only run)"

        visualizations.append({
            "comment_id": comment_id,
            "start_seconds": start_seconds,
            "duration_sec": brief.duration_sec,
            "narration_text": narration_text,
            "label": cls.label,
            "vfx_category": cls.vfx_category,
            "brief": brief_to_json(brief),
            "transformed_video": transformed_video,
            "source_segment": str(source_segment) if source_segment else None,
            "fingerprint": fingerprint,
            "cached": cached_flag,
            "skipped_reason": skipped_reason,
            "frames_used": [str(f) for f in frames],
        })

    manifest = {
        "report_path": payload.get("report_path"),
        "source_video": str(source_video) if source_video else None,
        "segments_path": str(segments_json_path),
        "total_duration_sec": payload.get("total_duration_sec"),
        "voice_preset": payload.get("voice_preset"),
        "max_aleph": max_aleph,
        "frames_dir": str(frames_dir),
        "dry_run": dry_run,
        "skip_aleph": skip_aleph,
        "visualizations": visualizations,
        "narrations": narrations,
        "stats": {
            "n_suggestions": len(visualizations),
            "n_observations": len(narrations),
            "aleph_calls": aleph_calls,
            "aleph_cached": aleph_cached,
        },
    }
    manifest_path = out_dir / "visualizations.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    LOG.info(
        "Manifest written %s (suggestions=%d observations=%d aleph_calls=%d cached=%d)",
        manifest_path,
        manifest["stats"]["n_suggestions"],
        manifest["stats"]["n_observations"],
        aleph_calls, aleph_cached,
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


class _SourceOutOfRange(RuntimeError):
    pass


def _extract_source_segment(
    *,
    source_video: Path,
    start_seconds: float,
    duration_sec: int,
    out_path: Path,
) -> Path:
    """Extract a clip from the source video using ffmpeg.

    Re-encodes (libx264 + aac) so the upload mime matches and Runway
    accepts it. Stream-copy would be faster but copy-cuts can land on
    non-keyframes and break uploads.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not on PATH — install or add to env")
    duration = _probe_duration(source_video)
    if start_seconds + 0.5 >= duration:
        raise _SourceOutOfRange(
            f"start={start_seconds:.1f}s exceeds source duration {duration:.1f}s"
        )
    actual_dur = min(duration_sec, max(1.0, duration - start_seconds))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_seconds:.3f}",
        "-i", str(source_video),
        "-t", f"{actual_dur:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    LOG.info("ffmpeg extract %s [%.1fs +%.1fs] -> %s", source_video.name, start_seconds, actual_dur, out_path.name)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()}")
    return out_path


def _probe_duration(path: Path) -> float:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not on PATH — install or add to env")
    res = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr.strip()}")
    return float(res.stdout.strip())
