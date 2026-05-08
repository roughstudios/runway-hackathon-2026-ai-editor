"""ai_editor command-line interface.

Stage 1 (Friday): build-commentary — synthesize a time-aligned audio
commentary track from a Film Analyzer report.

Stage 2 (Saturday): build-visualizations — classify each commentary
segment as observation vs suggestion, run the multimodal Visual Director
on each suggestion, extract source segments via ffmpeg, run Runway Aleph
(gen4_aleph video-to-video) to apply the VFX layer to existing footage,
and emit a manifest JSON for the player UI.

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
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
