"""ai_editor command-line interface.

Stage 1 (Friday): build-commentary — synthesize a time-aligned audio
commentary track from a Film Analyzer report.

Stage 2 (Saturday): build-visualizations — classify each commentary
segment as observation vs suggestion, run the multimodal Visual Director
on each suggestion, extract source segments via ffmpeg, run Runway Aleph
(gen4_aleph video-to-video) to apply the VFX layer to existing footage,
and emit a manifest JSON for the player UI.

Stage 3 (Sunday): build-avatar-track — for each commentary segment,
animate the AI Jonny avatar (custom Runway avatar created once via
``avatars.create``) by passing the segment's TTS audio to
``avatar_videos.create``. Output: per-segment talking-head MP4s plus
a manifest JSON consumed by the AvatarOverlay player component.

Stage 4 (Sunday): build-sfx — for each scene in the analyzer report,
the multimodal Audio Director picks atmosphere + hard FX briefs and
``sound_effect.create`` (eleven_text_to_sound_v2) renders each. The
manifest the Hotel-v3 Resolve exporter consumes is per-scene with
audio_assets keyed to A3 (atmosphere) and A4 (hard FX) tracks.

Usage:
  python -m ai_editor.cli build-commentary --report <path> [--out <dir>]
                                            [--limit N] [--dry-run]
                                            [--voice Vincent] [--model ID]

  python -m ai_editor.cli build-visualizations --segments <path>
                                                [--source-video <mov>]
                                                [--frames-dir <dir>]
                                                [--out <dir>] [--limit N]
                                                [--max-aleph N]
                                                [--skip-aleph] [--dry-run]

  python -m ai_editor.cli build-avatar-track --segments <path>
                                              [--out <dir>] [--limit N]
                                              [--max-segments N]
                                              [--reference-image <path>]
                                              [--dry-run]

  python -m ai_editor.cli build-sfx --report <path>
                                     [--out <dir>] [--frames-dir <dir>]
                                     [--limit N] [--max-sfx N]
                                     [--skip-sfx] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ai_editor.audio_pipeline import (
    DEFAULT_MAX_SFX,
    build_audio_track,
)
from ai_editor.avatar_animator import build_avatar_track
from ai_editor.avatar_creator import DEFAULT_REFERENCE_IMAGE
from ai_editor.commentary_synthesizer import (
    DEFAULT_TTS_MODEL,
    DEFAULT_VOICE_PRESET,
    build_commentary,
)
from ai_editor.visualization_pipeline import (
    DEFAULT_MAX_ALEPH,
    build_visualizations,
)


def _build_commentary(args: argparse.Namespace) -> int:
    result = build_commentary(
        report_path=Path(args.report),
        out_dir=Path(args.out),
        voice_preset=args.voice,
        model=args.model,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(f"track:    {result.track_path}")
    print(f"segments: {result.segments_path}")
    print(f"count:    {len(result.segments)}")
    print(f"duration: {result.total_duration_sec:.1f}s")
    cached = sum(1 for s in result.segments if s.cached)
    if cached:
        print(f"cached:   {cached}/{len(result.segments)} (no credits burned)")
    return 0


def _build_visualizations(args: argparse.Namespace) -> int:
    manifest = build_visualizations(
        segments_json_path=Path(args.segments),
        source_video=Path(args.source_video) if args.source_video else None,
        out_dir=Path(args.out),
        frames_dir=Path(args.frames_dir) if args.frames_dir else None,
        max_aleph=args.max_aleph,
        dry_run=args.dry_run,
        skip_aleph=args.skip_aleph,
        limit=args.limit,
    )
    stats = manifest["stats"]
    print(f"manifest:      {manifest['manifest_path']}")
    print(f"suggestions:   {stats['n_suggestions']}")
    print(f"observations:  {stats['n_observations']}")
    print(f"aleph calls:   {stats['aleph_calls']} (cached {stats['aleph_cached']})")
    skipped = sum(1 for v in manifest["visualizations"] if v.get("skipped_reason"))
    if skipped:
        print(f"skipped:       {skipped} (see manifest skipped_reason fields)")
    return 0


def _build_sfx(args: argparse.Namespace) -> int:
    manifest = build_audio_track(
        report_path=Path(args.report),
        out_dir=Path(args.out) if args.out else None,
        frames_dir=Path(args.frames_dir) if args.frames_dir else None,
        limit=args.limit,
        max_sfx=args.max_sfx,
        skip_sfx=args.skip_sfx,
        dry_run=args.dry_run,
    )
    stats = manifest["stats"]
    print(f"manifest:      {manifest['manifest_path']}")
    print(f"scenes:        {stats['n_scenes']}")
    print(f"atmospheres:   {stats['n_atmospheres']}")
    print(f"hard fx:       {stats['n_hard_fx']}")
    print(f"sfx calls:     {stats['sfx_calls']} (cached {stats['sfx_cached']})")
    if stats["skipped"]:
        print(f"skipped:       {stats['skipped']} (see manifest skipped_reason fields)")
    return 0


def _build_avatar_track(args: argparse.Namespace) -> int:
    manifest = build_avatar_track(
        segments_json_path=Path(args.segments),
        out_dir=Path(args.out) if args.out else None,
        reference_image=(
            Path(args.reference_image) if args.reference_image
            else DEFAULT_REFERENCE_IMAGE
        ),
        limit=args.limit,
        max_segments=args.max_segments,
        dry_run=args.dry_run,
    )
    stats = manifest["stats"]
    print(f"manifest:     {manifest['manifest_path']}")
    print(f"character:    {manifest.get('character_id') or '<dry-run>'}")
    print(f"animated:     {stats['n_animated']}/{stats['n_total']}")
    print(
        f"runway calls: {stats['runway_calls']} "
        f"(cached {stats['cached_calls']})"
    )
    if stats["skipped"]:
        print(
            f"skipped:      {stats['skipped']} "
            "(see manifest skipped_reason fields)"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="ai_editor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bc = sub.add_parser(
        "build-commentary",
        help="Synthesize a time-aligned commentary track from an analyzer report.",
    )
    bc.add_argument("--report", required=True, help="Path to analyzer report JSON.")
    bc.add_argument("--out", default="output/commentary", help="Output directory.")
    bc.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap moments processed (for cheap testing).",
    )
    bc.add_argument(
        "--voice",
        default=DEFAULT_VOICE_PRESET,
        help=f"Runway voice preset (default: {DEFAULT_VOICE_PRESET}).",
    )
    bc.add_argument(
        "--model",
        default=DEFAULT_TTS_MODEL,
        help=f"TTS model (default: {DEFAULT_TTS_MODEL}).",
    )
    bc.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip TTS calls; produce empty track + segments JSON for plumbing tests.",
    )
    bc.set_defaults(func=_build_commentary)

    bv = sub.add_parser(
        "build-visualizations",
        help=(
            "Stage 2: classify commentary segments, brief Aleph for each "
            "suggestion, run gen4_aleph video-to-video, write manifest."
        ),
    )
    bv.add_argument(
        "--segments",
        required=True,
        help="Path to commentary_segments.json (Stage 1 output).",
    )
    bv.add_argument(
        "--source-video",
        default=None,
        help=(
            "Source documentary cut to extract segments from. If omitted, "
            "Aleph calls are skipped (manifest still records briefs)."
        ),
    )
    bv.add_argument(
        "--frames-dir",
        default=None,
        help=(
            "Directory of pre-extracted reference frames "
            "(e.g. analysis_review/frames_<id>/). Defaults to the Canyons "
            "set under StudioOS-v1."
        ),
    )
    bv.add_argument(
        "--out",
        default="output/visualizations",
        help="Output directory for source segments + transformed clips + manifest.",
    )
    bv.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap segments processed (cheap testing).",
    )
    bv.add_argument(
        "--max-aleph",
        type=int,
        default=DEFAULT_MAX_ALEPH,
        help=f"Hard cap on Aleph calls per run (default: {DEFAULT_MAX_ALEPH}).",
    )
    bv.add_argument(
        "--skip-aleph",
        action="store_true",
        help="Run classifier + director + ffmpeg only; do not call Aleph.",
    )
    bv.add_argument(
        "--dry-run",
        action="store_true",
        help="Stub classifier+director, no Anthropic, no Runway, no ffmpeg.",
    )
    bv.set_defaults(func=_build_visualizations)

    ba = sub.add_parser(
        "build-avatar-track",
        help=(
            "Stage 3: animate the AI Jonny avatar against each commentary "
            "segment's TTS audio via Runway avatar_videos; emit per-segment "
            "MP4s + manifest for the AvatarOverlay player component."
        ),
    )
    ba.add_argument(
        "--segments",
        required=True,
        help="Path to commentary_segments.json (Stage 1 output).",
    )
    ba.add_argument(
        "--out",
        default=None,
        help=(
            "Output directory. Defaults to "
            "output/avatar_segments/<report_id>/ derived from the "
            "segments JSON's report_path."
        ),
    )
    ba.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap segments processed (cheap testing).",
    )
    ba.add_argument(
        "--max-segments",
        type=int,
        default=None,
        help=(
            "Hard cap on Runway calls per run. Cached calls don't count. "
            "Useful when the segments JSON has many entries but you only "
            "want to burn credits on a subset."
        ),
    )
    ba.add_argument(
        "--reference-image",
        default=None,
        help=(
            "Override the reference photo for avatar creation. Defaults "
            "to data/avatars/jonny_reference_frames/jonny_main.jpg."
        ),
    )
    ba.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Skip Runway entirely. Manifest is still written; per-segment "
            "MP4 files are zero-byte placeholders."
        ),
    )
    ba.set_defaults(func=_build_avatar_track)

    bsfx = sub.add_parser(
        "build-sfx",
        help=(
            "Stage 4: walk analyzer scenes, run the Audio Director per "
            "scene, render atmosphere + hard FX via Runway sound_effect, "
            "emit manifest for the Hotel-v3 Resolve exporter."
        ),
    )
    bsfx.add_argument(
        "--report",
        required=True,
        help="Path to analyzer report JSON (must include scenes[]).",
    )
    bsfx.add_argument(
        "--out",
        default=None,
        help=(
            "Output directory. Defaults to output/sfx/<report_id>/ "
            "derived from the report filename."
        ),
    )
    bsfx.add_argument(
        "--frames-dir",
        default=None,
        help=(
            "Directory of pre-extracted reference frames for the multimodal "
            "Audio Director. Defaults to the Canyons set under StudioOS-v1."
        ),
    )
    bsfx.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap scenes processed (cheap testing).",
    )
    bsfx.add_argument(
        "--max-sfx",
        type=int,
        default=DEFAULT_MAX_SFX,
        help=(
            f"Hard cap on SFX create calls per run (default: "
            f"{DEFAULT_MAX_SFX}). Cached calls don't count."
        ),
    )
    bsfx.add_argument(
        "--skip-sfx",
        action="store_true",
        help="Run Audio Director only; do not call sound_effect.",
    )
    bsfx.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Stub Audio Director, no Anthropic, no Runway. Asset MP3 "
            "files are zero-byte placeholders."
        ),
    )
    bsfx.set_defaults(func=_build_sfx)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
