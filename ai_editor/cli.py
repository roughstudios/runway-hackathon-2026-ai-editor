"""ai_editor command-line interface.

Stage 1 (Friday): build-commentary — synthesize a time-aligned audio
commentary track from a Film Analyzer report.

Stage 2 (Saturday) will add: classify-suggestions, build-visualizations,
build-avatar.

Usage:
  python -m ai_editor.cli build-commentary --report <path> [--out <dir>]
                                            [--limit N] [--dry-run]
                                            [--voice Vincent] [--model ID]
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
