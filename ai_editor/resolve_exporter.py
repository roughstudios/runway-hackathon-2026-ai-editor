"""Stage 5 (Hotel-v3) — Resolve rough-cut bundler.

Reads the four upstream manifests (commentary, visualizations, avatar,
sfx) and produces a self-contained Resolve import bundle:

    output/resolve_bundle/<report_id>/
      source.mov
      commentary_track.wav
      visualizations/<comment_id>.mp4
      avatar/seg_<NN>.mp4
      sfx/scene_<NN>_atmos.mp3
      sfx/scene_<NN>_fx_<NN>.mp3
      manifest.json          <- canonical unified manifest
      manifest.lua           <- same data as a Lua return-table sidecar
      StudioOS_Import_AIEdit.lua  <- Resolve Scripts-menu importer

The unified manifest groups every asset by its target Resolve track:

    V1: source video         (full source clip, in 0..total_duration)
    V2: visualizations       (gen4_aleph transforms placed at start_seconds)
    V3: avatar               (talking-head MP4 placed at start_seconds)
    A1: source dialogue      (the audio side of source.mov)
    A2: commentary           (the time-aligned narration WAV)
    A3: atmosphere SFX       (per-scene ambience, looped + tiled if needed)
    A4: hard FX              (per-event one-shots)

Missing manifests are tolerated — Hotel-v3 can produce a partial bundle
(e.g. SFX-only, or commentary+avatar without visualizations) and the
``stats.missing_manifests`` field records what wasn't there. The Lua
importer skips empty tracks gracefully.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_editor.resolve_lua_template import (
    TRACK_ORDER,
    render_importer_lua,
    render_manifest_lua,
)

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE_ROOT = REPO_ROOT / "output" / "resolve_bundle"

# Same SFX cap as ai_editor.audio_pipeline / sfx_client. Duplicated here
# so the exporter can derive rendered-clip duration without importing
# the audio pipeline (which pulls in pydub / requests transitively).
SFX_MAX_DURATION_SEC = 30.0

# Default fps when the source manifest doesn't carry one; the Lua
# importer prefers the live Resolve project setting and only falls
# back to this value.
DEFAULT_FPS = 25.0

LUA_SCRIPT_NAME = "StudioOS_Import_AIEdit.lua"

# Default search roots for upstream manifests (the four stage outputs).
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output"


# --- Result / discovery --------------------------------------------------


@dataclass
class _ManifestSet:
    commentary: Optional[Dict[str, Any]] = None
    visualizations: Optional[Dict[str, Any]] = None
    avatar: Optional[Dict[str, Any]] = None
    sfx: Optional[Dict[str, Any]] = None
    missing: List[str] = field(default_factory=list)


def _load_manifest(path: Optional[Path], kind: str, missing: List[str]) -> Optional[Dict[str, Any]]:
    if path is None:
        missing.append(kind)
        LOG.warning("No %s manifest found — track will be empty.", kind)
        return None
    if not path.exists():
        missing.append(kind)
        LOG.warning("Missing %s manifest at %s — track will be empty.", kind, path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface in stats
        missing.append(f"{kind} (parse error)")
        LOG.warning("Failed to parse %s manifest at %s: %s", kind, path, exc)
        return None


def discover_manifests(
    *,
    report_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    commentary_segments: Optional[Path] = None,
    visualizations_manifest: Optional[Path] = None,
    avatar_manifest: Optional[Path] = None,
    sfx_manifest: Optional[Path] = None,
) -> _ManifestSet:
    """Locate the four upstream manifests by convention.

    Each Stage's CLI default writes to a known path; if the user passes
    explicit overrides we trust those and skip discovery.
    """
    out = _ManifestSet()
    output_root = Path(output_root)

    # Commentary lives at ``output/commentary/commentary_segments.json``
    # (Stage 1's flat layout) — there is no per-report subdir today.
    cs = commentary_segments
    if cs is None:
        candidate = output_root / "commentary" / "commentary_segments.json"
        cs = candidate if candidate.exists() else None
    out.commentary = _load_manifest(cs, "commentary", out.missing)

    # Visualizations at ``output/visualizations/visualizations.json``.
    vp = visualizations_manifest
    if vp is None:
        candidate = output_root / "visualizations" / "visualizations.json"
        vp = candidate if candidate.exists() else None
    out.visualizations = _load_manifest(vp, "visualizations", out.missing)

    # Avatar at ``output/avatar_segments/<report_id>/manifest.json``.
    ap = avatar_manifest
    if ap is None:
        candidate = output_root / "avatar_segments" / report_id / "manifest.json"
        ap = candidate if candidate.exists() else None
    out.avatar = _load_manifest(ap, "avatar", out.missing)

    # SFX at ``output/sfx/<report_id>/manifest.json`` — Golf-v3's layout.
    sp = sfx_manifest
    if sp is None:
        candidate = output_root / "sfx" / report_id / "manifest.json"
        sp = candidate if candidate.exists() else None
    out.sfx = _load_manifest(sp, "sfx", out.missing)

    return out


# --- Public entrypoint ---------------------------------------------------


def bundle_for_resolve(
    report_id: str,
    source_video_path: Path,
    *,
    bundle_root: Path = DEFAULT_BUNDLE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    commentary_segments: Optional[Path] = None,
    visualizations_manifest: Optional[Path] = None,
    avatar_manifest: Optional[Path] = None,
    sfx_manifest: Optional[Path] = None,
    fps: float = DEFAULT_FPS,
    copy_media: bool = True,
) -> Path:
    """Assemble a Resolve-import bundle for ``report_id``.

    Args:
      report_id: stem identifying the analyzer report — drives manifest
        discovery and the bundle directory name.
      source_video_path: absolute path to the documentary master cut.
        Copied to ``<bundle>/source.mov`` (extension preserved).
      bundle_root: where to materialize the bundle; the per-report dir
        is created underneath. Defaults to ``output/resolve_bundle/``.
      output_root: where to look for the upstream manifests. Defaults to
        ``output/``.
      commentary_segments / *_manifest: explicit overrides if the
        manifests live somewhere non-default.
      fps: fallback fps used if the Resolve project doesn't report one.
      copy_media: if False, the bundle references absolute upstream
        paths instead of copying media. Useful for tests.

    Returns:
      The bundle directory path.
    """
    source_video_path = Path(source_video_path)
    bundle_root = Path(bundle_root)
    output_root = Path(output_root)

    bundle_dir = bundle_root / report_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "visualizations").mkdir(exist_ok=True)
    (bundle_dir / "avatar").mkdir(exist_ok=True)
    (bundle_dir / "sfx").mkdir(exist_ok=True)

    manifests = discover_manifests(
        report_id=report_id,
        output_root=output_root,
        commentary_segments=commentary_segments,
        visualizations_manifest=visualizations_manifest,
        avatar_manifest=avatar_manifest,
        sfx_manifest=sfx_manifest,
    )

    total_duration = _resolve_total_duration(manifests)

    # 1) Source video.
    source_relpath = "source" + (source_video_path.suffix or ".mov")
    source_dest = bundle_dir / source_relpath
    _materialize(source_video_path, source_dest, copy_media=copy_media)

    # 2) Commentary track.
    commentary_relpath: Optional[str] = None
    if manifests.commentary is not None:
        commentary_track = _resolve_path(manifests.commentary.get("track_path"))
        if commentary_track is not None and commentary_track.exists():
            commentary_relpath = "commentary_track.wav"
            _materialize(commentary_track, bundle_dir / commentary_relpath, copy_media=copy_media)
        else:
            LOG.warning(
                "Commentary track_path missing or unreadable (%s). "
                "Track A2 will be empty.",
                commentary_track,
            )

    # 3) Visualizations.
    viz_entries: List[Dict[str, Any]] = []
    if manifests.visualizations is not None:
        for v in manifests.visualizations.get("visualizations") or []:
            entry = _stage_visualization(v, bundle_dir, copy_media=copy_media)
            if entry is not None:
                viz_entries.append(entry)

    # 4) Avatar segments.
    avatar_entries: List[Dict[str, Any]] = []
    if manifests.avatar is not None:
        for i, seg in enumerate(manifests.avatar.get("segments") or []):
            entry = _stage_avatar_segment(seg, i, bundle_dir, copy_media=copy_media)
            if entry is not None:
                avatar_entries.append(entry)

    # 5) SFX (atmosphere + hard FX).
    atmos_entries: List[Dict[str, Any]] = []
    fx_entries: List[Dict[str, Any]] = []
    if manifests.sfx is not None:
        for scene in manifests.sfx.get("scenes") or []:
            staged_atmos, staged_fx = _stage_sfx_scene(scene, bundle_dir, copy_media=copy_media)
            atmos_entries.extend(staged_atmos)
            fx_entries.extend(staged_fx)

    # 6) Build unified manifest tracks. V1/A1 = full source.
    tracks: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TRACK_ORDER}
    tracks["V1"].append({
        "path": source_relpath,
        "in": 0.0,
        "out": total_duration,
        "timeline_in": 0.0,
        "label": "source",
    })
    tracks["A1"].append({
        "path": source_relpath,
        "in": 0.0,
        "out": total_duration,
        "timeline_in": 0.0,
        "label": "source dialogue",
    })
    tracks["V2"].extend(viz_entries)
    tracks["V3"].extend(avatar_entries)
    if commentary_relpath:
        tracks["A2"].append({
            "path": commentary_relpath,
            "in": 0.0,
            "out": total_duration,
            "timeline_in": 0.0,
            "label": "commentary",
        })
    tracks["A3"].extend(atmos_entries)
    tracks["A4"].extend(fx_entries)

    unified = {
        "report_id": report_id,
        "source_video": source_relpath,
        "fps": fps,
        "total_duration_sec": total_duration,
        "tracks": tracks,
        "stats": {
            "n_visualizations": len(viz_entries),
            "n_avatar_segments": len(avatar_entries),
            "n_atmospheres": len(atmos_entries),
            "n_hard_fx": len(fx_entries),
            "missing_manifests": list(manifests.missing),
        },
        "lua_script": LUA_SCRIPT_NAME,
        "source_manifests": _record_source_manifest_paths(manifests),
    }

    # 7) Write artifacts.
    (bundle_dir / "manifest.json").write_text(
        json.dumps(unified, indent=2), encoding="utf-8",
    )
    (bundle_dir / "manifest.lua").write_text(
        render_manifest_lua(unified), encoding="utf-8",
    )
    (bundle_dir / LUA_SCRIPT_NAME).write_text(
        render_importer_lua(
            bundle_dir_posix=bundle_dir.resolve().as_posix(),
            report_id=report_id,
        ),
        encoding="utf-8",
    )

    LOG.info(
        "Resolve bundle %s — V2=%d V3=%d A3=%d A4=%d (missing=%s)",
        bundle_dir, len(viz_entries), len(avatar_entries),
        len(atmos_entries), len(fx_entries), manifests.missing,
    )
    return bundle_dir


# --- Manifest shape adapters --------------------------------------------


def _stage_visualization(
    v: Dict[str, Any], bundle_dir: Path, *, copy_media: bool,
) -> Optional[Dict[str, Any]]:
    transformed = v.get("transformed_video")
    if not transformed:
        # Brief-only / skipped visualization. Skip silently.
        return None
    src = _resolve_path(transformed)
    if src is None or not src.exists():
        LOG.warning(
            "Visualization %s transformed_video missing on disk (%s); skipping.",
            v.get("comment_id"), transformed,
        )
        return None
    comment_id = str(v.get("comment_id") or "viz")
    safe = _safe_stem(comment_id)
    dest_rel = f"visualizations/{safe}{src.suffix or '.mp4'}"
    _materialize(src, bundle_dir / dest_rel, copy_media=copy_media)
    duration = _coerce_float(v.get("duration_sec"), default=5.0)
    start = _coerce_float(v.get("start_seconds"), default=0.0)
    return {
        "path": dest_rel,
        "in": 0.0,
        "out": duration,
        "duration": duration,
        "timeline_in": start,
        "label": f"viz: {comment_id}",
    }


def _stage_avatar_segment(
    seg: Dict[str, Any], index: int, bundle_dir: Path, *, copy_media: bool,
) -> Optional[Dict[str, Any]]:
    mp4_path = seg.get("mp4_path")
    if not mp4_path or seg.get("skipped_reason"):
        return None
    src = _resolve_path(mp4_path)
    if src is None or not src.exists():
        LOG.warning(
            "Avatar segment %s mp4_path missing on disk (%s); skipping.",
            seg.get("comment_id"), mp4_path,
        )
        return None
    dest_rel = f"avatar/seg_{index:02d}{src.suffix or '.mp4'}"
    _materialize(src, bundle_dir / dest_rel, copy_media=copy_media)
    duration = _coerce_float(seg.get("duration_sec"), default=0.0)
    if duration <= 0:
        # Fall back to start/end if duration_sec is bogus.
        start = _coerce_float(seg.get("start_seconds"), default=0.0)
        end = _coerce_float(seg.get("end_seconds"), default=start)
        duration = max(0.5, end - start)
    return {
        "path": dest_rel,
        "in": 0.0,
        "out": duration,
        "duration": duration,
        "timeline_in": _coerce_float(seg.get("start_seconds"), default=0.0),
        "label": f"avatar: {seg.get('comment_id')}",
    }


def _stage_sfx_scene(
    scene: Dict[str, Any], bundle_dir: Path, *, copy_media: bool,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Stage a scene's audio assets, returning (atmosphere_entries, fx_entries)."""
    atmos: List[Dict[str, Any]] = []
    fx: List[Dict[str, Any]] = []
    scene_id = str(scene.get("scene_id") or "scene")
    scene_start = _coerce_float(scene.get("scene_start_seconds"), default=0.0)
    for asset in scene.get("audio_assets") or []:
        if asset.get("skipped_reason"):
            continue
        asset_path_raw = asset.get("asset_path")
        if not asset_path_raw:
            continue
        src = _resolve_path(asset_path_raw)
        if src is None or not src.exists():
            LOG.warning(
                "SFX asset %s missing on disk (%s); skipping.",
                scene_id, asset_path_raw,
            )
            continue
        rel = f"sfx/{Path(asset_path_raw).name}"
        _materialize(src, bundle_dir / rel, copy_media=copy_media)

        in_scene_dur = _coerce_float(asset.get("duration"), default=0.0)
        offset = _coerce_float(asset.get("start_offset_in_scene"), default=0.0)
        timeline_in = scene_start + offset
        kind = asset.get("type")
        loop = bool(asset.get("loop"))

        if kind == "atmosphere":
            # Mirror audio_pipeline's request_duration logic so the
            # importer knows the rendered clip length on disk vs the
            # in-scene tile target.
            rendered = (
                min(in_scene_dur, SFX_MAX_DURATION_SEC)
                if in_scene_dur > 0 else SFX_MAX_DURATION_SEC
            )
            atmos.append({
                "path": rel,
                "in": 0.0,
                "out": rendered,
                "duration": rendered,
                "timeline_in": timeline_in,
                "loop": loop,
                "tile_to_seconds": in_scene_dur if loop else rendered,
                "label": f"atmos: {scene_id}",
                "prompt": asset.get("prompt"),
            })
        else:
            fx.append({
                "path": rel,
                "in": 0.0,
                "out": in_scene_dur if in_scene_dur > 0 else 1.0,
                "duration": in_scene_dur if in_scene_dur > 0 else 1.0,
                "timeline_in": timeline_in,
                "loop": False,
                "label": f"fx: {scene_id} +{offset:.1f}s",
                "prompt": asset.get("prompt"),
            })
    return atmos, fx


# --- Helpers -------------------------------------------------------------


def _resolve_total_duration(manifests: _ManifestSet) -> float:
    """Pull total_duration_sec from whichever upstream manifest carries it.

    Each stage's manifest copies it from the analyzer report, so any of
    them is authoritative. Prefer commentary (Stage 1) since that's
    the upstream-most source.
    """
    for m in (manifests.commentary, manifests.sfx, manifests.visualizations):
        if not m:
            continue
        d = m.get("total_duration_sec")
        if d:
            return float(d)
    return 0.0


def _record_source_manifest_paths(m: _ManifestSet) -> Dict[str, Optional[str]]:
    def path_or_none(d: Optional[Dict[str, Any]], key: str) -> Optional[str]:
        if not d:
            return None
        v = d.get(key)
        return str(v) if v else None

    return {
        "commentary_segments": path_or_none(m.commentary, "report_path"),
        "visualizations": path_or_none(m.visualizations, "manifest_path") or path_or_none(m.visualizations, "report_path"),
        "avatar": path_or_none(m.avatar, "manifest_path") or path_or_none(m.avatar, "report_path"),
        "sfx": path_or_none(m.sfx, "manifest_path") or path_or_none(m.sfx, "report_path"),
    }


def _resolve_path(value: Any) -> Optional[Path]:
    """Turn a manifest path string into a usable absolute Path.

    Manifests sometimes carry forward-slash relative paths (Linux-style)
    and sometimes Windows-style backslashes. Anchor relative entries to
    the repo root so the exporter can be invoked from any cwd.
    """
    if not value:
        return None
    p = Path(str(value).replace("\\", "/"))
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def _materialize(src: Path, dest: Path, *, copy_media: bool) -> None:
    """Copy ``src`` into the bundle, or just touch a marker if media
    copying is disabled (test mode)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not copy_media:
        # Test mode — write a tiny marker so existence checks downstream pass.
        if not dest.exists():
            dest.write_bytes(b"")
        return
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        # Already there at the same size; assume cached and skip the copy.
        return
    shutil.copyfile(src, dest)


def _coerce_float(v: Any, *, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_stem(value: str) -> str:
    """Make a path-safe stem from arbitrary identifier text."""
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in value)
    return cleaned or "asset"
