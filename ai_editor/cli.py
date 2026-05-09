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

  python -m ai_editor.cli export-resolve --report <id>
                                          --source <video>
                                          [--bundle-root <dir>]
                                          [--output-root <dir>]
                                          [--commentary <path>]
                                          [--visualizations <path>]
                                          [--avatar <path>]
                                          [--sfx <path>]
                                          [--fps N] [--no-copy]
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
from ai_editor.hero_composer import (
    DEFAULT_FRAMES_DIR as HERO_DEFAULT_FRAMES_DIR,
    DEFAULT_OUT_ROOT as HERO_DEFAULT_OUT_ROOT,
    compose_hero,
)
from ai_editor.resolve_exporter import (
    DEFAULT_BUNDLE_ROOT,
    DEFAULT_FPS,
    DEFAULT_OUTPUT_ROOT,
    bundle_for_resolve,
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


def _export_resolve(args: argparse.Namespace) -> int:
    bundle_dir = bundle_for_resolve(
        report_id=args.report,
        source_video_path=Path(args.source),
        bundle_root=Path(args.bundle_root),
        output_root=Path(args.output_root),
        commentary_segments=Path(args.commentary) if args.commentary else None,
        visualizations_manifest=Path(args.visualizations) if args.visualizations else None,
        avatar_manifest=Path(args.avatar) if args.avatar else None,
        sfx_manifest=Path(args.sfx) if args.sfx else None,
        fps=args.fps,
        copy_media=not args.no_copy,
    )
    import json as _json
    manifest = _json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    stats = manifest["stats"]
    print(f"bundle:           {bundle_dir}")
    print(f"manifest:         {bundle_dir / 'manifest.json'}")
    print(f"lua importer:     {bundle_dir / manifest['lua_script']}")
    print(f"visualizations:   {stats['n_visualizations']}")
    print(f"avatar segments:  {stats['n_avatar_segments']}")
    print(f"atmospheres (A3): {stats['n_atmospheres']}")
    print(f"hard fx (A4):     {stats['n_hard_fx']}")
    if stats["missing_manifests"]:
        print(f"missing:          {', '.join(stats['missing_manifests'])}")
    print()
    print("Next: install + run the Resolve importer —")
    print("  python R:/--CODE--/StudioOS-v1/scripts/install_resolve_scripts.py")
    print(
        "  Resolve > Workspace > Scripts > "
        f"{Path(manifest['lua_script']).stem}"
    )
    return 0


def _compose_hero(args: argparse.Namespace) -> int:
    only = None
    if args.only_segments:
        only = [s.strip() for s in args.only_segments.split(",") if s.strip()]
    manifest = compose_hero(
        report_path=Path(args.report),
        out_root=Path(args.out_root) if args.out_root else HERO_DEFAULT_OUT_ROOT,
        frames_dir=Path(args.frames_dir) if args.frames_dir else HERO_DEFAULT_FRAMES_DIR,
        max_credits=args.max_credits,
        dry_run=args.dry_run,
        skip_runway=args.skip_runway,
        skip_aleph=args.skip_aleph,
        skip_avatar=args.skip_avatar,
        skip_sfx=args.skip_sfx,
        only_segments=only,
    )
    print(f"hero:        {manifest['hero_path']}")
    print(f"manifest:    {manifest['manifest_path']}")
    print(f"duration:    {manifest['duration_sec']:.1f}s ({manifest['duration_minutes']} min)")
    print(f"segments:    {len(manifest['segments'])}")
    print(f"credits:     {manifest['spent_estimated_credits']} (est) / {manifest['max_credits']}")
    print(f"breakdown:   {manifest['spent_breakdown']}")
    skipped_total = sum(len(s.get('skipped') or []) for s in manifest['segments'])
    if skipped_total:
        print(f"skipped:     {skipped_total} sub-tasks (see manifest)")
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

    er = sub.add_parser(
        "export-resolve",
        help=(
            "Stage 5: bundle commentary + visualizations + avatar + SFX "
            "into a Resolve-importable directory with a unified manifest "
            "and a Scripts-menu Lua importer."
        ),
    )
    er.add_argument(
        "--report",
        required=True,
        help=(
            "Report id (matches the analyzer report stem, e.g. "
            "'canyons_100_miles_1_3'). Drives manifest discovery + "
            "bundle directory name."
        ),
    )
    er.add_argument(
        "--source",
        required=True,
        help="Path to the source documentary master cut (copied into the bundle).",
    )
    er.add_argument(
        "--bundle-root",
        default=str(DEFAULT_BUNDLE_ROOT),
        help=(
            "Where to materialize the bundle (a per-report subdir is "
            f"created underneath). Default: {DEFAULT_BUNDLE_ROOT}."
        ),
    )
    er.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=(
            "Where to look for upstream stage manifests if no explicit "
            f"override is given. Default: {DEFAULT_OUTPUT_ROOT}."
        ),
    )
    er.add_argument(
        "--commentary",
        default=None,
        help="Override path to commentary_segments.json (Stage 1).",
    )
    er.add_argument(
        "--visualizations",
        default=None,
        help="Override path to visualizations.json (Stage 2).",
    )
    er.add_argument(
        "--avatar",
        default=None,
        help="Override path to avatar_segments/<id>/manifest.json (Stage 3).",
    )
    er.add_argument(
        "--sfx",
        default=None,
        help="Override path to sfx/<id>/manifest.json (Stage 4).",
    )
    er.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help=(
            "Fallback fps used by the Lua importer if the Resolve "
            f"project setting can't be read. Default: {DEFAULT_FPS}."
        ),
    )
    er.add_argument(
        "--no-copy",
        action="store_true",
        help=(
            "Skip copying media into the bundle (faster + smaller for "
            "tests). The bundle will still contain manifest + Lua but "
            "asset files will be empty placeholders."
        ),
    )
    er.set_defaults(func=_export_resolve)

    ch = sub.add_parser(
        "compose-hero",
        help=(
            "Stage 6 (overnight India-v3): curate 5-7 min of the analyzer "
            "report, run TTS + avatar_videos + Aleph + sound_effect via "
            "direct httpx (the runwayml SDK hangs on this machine), and "
            "compose a single hero MP4 + manifest for the LIVE PAGE."
        ),
    )
    ch.add_argument(
        "--report",
        required=True,
        help="Path to analyzer report JSON (e.g. canyons_100_miles_1_3.json).",
    )
    ch.add_argument(
        "--out-root",
        default=None,
        help="Output root (per-report subdir is created underneath). "
             f"Default: {HERO_DEFAULT_OUT_ROOT}.",
    )
    ch.add_argument(
        "--frames-dir",
        default=None,
        help="Reference frames directory used to build the slideshow source. "
             f"Default: {HERO_DEFAULT_FRAMES_DIR}.",
    )
    ch.add_argument(
        "--max-credits",
        type=int,
        default=5000,
        help="Hard cap on estimated Runway credit burn (default: 5000).",
    )
    ch.add_argument(
        "--only-segments",
        default=None,
        help="Comma-separated list of segment ids to process "
             "(e.g. 'open_hook,first_place'). Skip the rest.",
    )
    ch.add_argument(
        "--dry-run",
        action="store_true",
        help="No Runway, no ElevenLabs. Manifest only; assets are placeholders.",
    )
    ch.add_argument(
        "--skip-runway",
        action="store_true",
        help="ElevenLabs runs but every Runway endpoint is skipped.",
    )
    ch.add_argument("--skip-aleph", action="store_true", help="Skip Aleph only.")
    ch.add_argument("--skip-avatar", action="store_true", help="Skip avatar_videos only.")
    ch.add_argument("--skip-sfx", action="store_true", help="Skip sound_effect only.")
    ch.set_defaults(func=_compose_hero)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
