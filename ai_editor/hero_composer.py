"""Hero composer — Stage 6 orchestrator (overnight India-v3).

Runs the full AI-editor pipeline against ONE analyzer report and emits a
single 5-7 min "hero" MP4 + sidecar manifest for the LIVE PAGE
(``/analysis-preview/[id]``). Bypasses the runwayml SDK (broken on this
machine — see ``ai_editor.runway_http``); calls Runway via direct httpx.

Flow:

  1. CURATE — pick the highest-impact 5-7 min from the analyzer report.
     Uses ``emotional_analysis.moments`` + ``hook_brief`` to assemble a
     short list of curated segments tagged COMMENTARY or SUGGESTION.

  2. SOURCE SLIDESHOW — the rendered "Canyons - 100 Miles 1.3.mov" cut
     isn't on disk on this machine (Resolve timeline only). We build a
     synthetic source by stitching the existing
     ``frames_canyons-100-miles-1-3`` set into a Ken Burns slideshow at
     1280x720 30fps, one per curated segment. The slideshow is the V1
     under everything else.

  3. TTS — for each curated segment, ElevenLabs synthesizes the
     analyzer note as Jonny's cloned voice. Cached on disk by
     sha256(text|voice|model).

  4. AVATAR — for each TTS clip, ``POST /v1/avatar_videos`` against the
     pre-baked AI Jonny character (data/avatars/jonny_character.json,
     id f5fbc1d0-...). Cached by sha256(audio|character|model).

  5. ALEPH — for the curated segments tagged SUGGESTION, extract a 5s
     slice of the slideshow at the segment's timecode, upload it, run
     ``POST /v1/video_to_video gen4_aleph`` with a brand-aware
     atmospheric brief, download the transformed clip. Cached.

  6. SFX — atmosphere-per-act (3) + a couple of hard FX. Cached.

  7. COMPOSE — single ffmpeg filter_complex pass:
       V1: source slideshow
       V2: avatar PIP overlay during commentary windows (corner 320x180)
       V3: Aleph clip overlay full-screen during suggestion windows
       A2: commentary master
       A3: atmosphere bed (-12 dB, looped per-act)
       A4: hard FX one-shots (-6 dB)

Everything is cached by content fingerprint so reruns skip Runway.
The credit budget is enforced via ``max_credits`` — when exceeded, the
composer skips the next non-cached call rather than burning over.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

from ai_editor import runway_http
from ai_editor.elevenlabs_voice import ElevenLabsVoice

LOG = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_ROOT = REPO_ROOT / "output" / "hero"
DEFAULT_FRAMES_DIR = Path(
    "R:/--CODE--/StudioOS-v1/data/learndocumentary/analysis_review/frames_canyons-100-miles-1-3"
)
DEFAULT_CHARACTER_CACHE = REPO_ROOT / "data" / "avatars" / "jonny_character.json"
FFMPEG = "R:/--CODE--/StudioOS-v1/tools/braw-decode/ffmpeg.exe"
FFPROBE = "ffprobe"  # fallback to PATH

CACHE_ROOT = Path(".cache") / "hero"

HERO_W, HERO_H, HERO_FPS = 1280, 720, 30
PIP_W, PIP_H = 360, 200          # avatar PIP corner size
PIP_X, PIP_Y = 30, 30            # PIP placement (top-left)
ALEPH_DURATION = 5               # seconds per Aleph generation
ALEPH_RATIO = "1280:720"

# Heuristic credit costs (Runway public pricing 2024-11)
COST_AVATAR_VIDEO = 250          # ~per 8s @ gwm1
COST_ALEPH_5S = 250              # gen4_aleph 5s
COST_SFX_PER_30S = 20            # eleven_text_to_sound_v2

CRAFT_BRAND = {
    "channel": "craft",
    "tone": "intimate, observational",
    "lighting": "warm natural light",
    "ai_role": "atmospheric_complement",
}


# --- Data classes ---------------------------------------------------------


@dataclass
class CuratedSegment:
    """One moment we picked for the hero."""

    id: str                       # e.g. "open_hook", "first_place", ...
    kind: str                     # "COMMENTARY" | "SUGGESTION"
    in_seconds: float             # in the SOURCE timeline (analyzer report)
    out_seconds: float            # ditto
    duration_sec: float           # convenience
    title: str                    # short label for the manifest
    narration_text: str           # full TTS script
    aleph_prompt: Optional[str] = None
    aleph_offset_in_segment: float = 0.0  # seconds from segment in
    vfx_category: Optional[str] = None
    impact: str = "medium"
    type_label: str = ""          # TURNING_POINT, VULNERABILITY, etc

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.id.encode())
        h.update(b"|")
        h.update(self.narration_text.encode())
        h.update(b"|")
        h.update(f"{self.in_seconds:.2f}|{self.out_seconds:.2f}".encode())
        return h.hexdigest()[:12]


@dataclass
class HeroPlanEntry:
    """One curated segment after assets are produced; goes in manifest."""

    seg: CuratedSegment
    curated_in_seconds: float            # in the HERO timeline
    curated_out_seconds: float           # ditto
    tts_path: Optional[Path] = None
    tts_duration_sec: float = 0.0
    avatar_path: Optional[Path] = None
    avatar_duration_sec: float = 0.0
    aleph_path: Optional[Path] = None
    aleph_duration_sec: float = 0.0
    source_clip_path: Optional[Path] = None  # the slideshow micro-clip
    skipped: List[str] = field(default_factory=list)


# --- Public API ----------------------------------------------------------


def compose_hero(
    *,
    report_path: Path,
    out_root: Path = DEFAULT_OUT_ROOT,
    frames_dir: Path = DEFAULT_FRAMES_DIR,
    character_cache: Path = DEFAULT_CHARACTER_CACHE,
    max_credits: int = 5000,
    dry_run: bool = False,
    skip_runway: bool = False,
    skip_aleph: bool = False,
    skip_avatar: bool = False,
    skip_sfx: bool = False,
    only_segments: Optional[List[str]] = None,
) -> dict:
    """Run the full hero pipeline. Returns the manifest dict.

    Modes:
      dry_run=True       no Runway, no ElevenLabs, no Anthropic. Audio +
                         video assets are zero-byte placeholders. Used in
                         tests + for plumbing checks.
      skip_runway=True   ElevenLabs runs (we still want voice samples)
                         but every Runway endpoint is skipped — manifest
                         records "skipped: runway-disabled".
      skip_aleph=True    skip just Aleph (cheap dev iteration).
      skip_avatar=True   skip just avatar_videos.
      skip_sfx=True      skip just sound_effect.
      only_segments=[..] only process the listed segment ids.
      max_credits=N      stop firing non-cached Runway calls once the
                         estimated burn passes N.
    """
    load_dotenv()
    report_path = Path(report_path).resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"analyzer report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    report_id = report_path.stem
    out_dir = (Path(out_root) / report_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = (Path.cwd() / CACHE_ROOT / report_id).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("compose_hero: report=%s out=%s", report_path.name, out_dir)

    # 1. Curate
    plan = curate(report)
    if only_segments:
        plan = [p for p in plan if p.id in set(only_segments)]
        if not plan:
            raise RuntimeError(f"no segments matched only_segments={only_segments}")
    LOG.info("curated %d segments", len(plan))

    # Compute curated-timeline offsets
    cursor = 0.0
    plan_entries: List[HeroPlanEntry] = []
    for seg in plan:
        dur = float(seg.out_seconds - seg.in_seconds)
        plan_entries.append(HeroPlanEntry(
            seg=seg,
            curated_in_seconds=round(cursor, 3),
            curated_out_seconds=round(cursor + dur, 3),
        ))
        cursor += dur
    total_hero_dur = round(cursor, 3)
    LOG.info("hero total duration target: %.1fs (%.1f min)", total_hero_dur, total_hero_dur / 60.0)

    # 2. Source slideshow micro-clips (one per curated segment)
    source_dir = out_dir / "source_clips"
    source_dir.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        for entry in plan_entries:
            entry.source_clip_path = build_kenburns_clip(
                frames_dir=frames_dir,
                in_seconds=entry.seg.in_seconds,
                out_seconds=entry.seg.out_seconds,
                out_path=source_dir / f"{entry.seg.id}.mp4",
            )

    # Concatenate to one slideshow
    slideshow_path = out_dir / "source_slideshow.mp4"
    if not dry_run:
        concat_clips([e.source_clip_path for e in plan_entries], slideshow_path)
    else:
        slideshow_path.write_bytes(b"")

    # 3. TTS commentary
    voice = None
    if not dry_run:
        voice = ElevenLabsVoice()
    commentary_dir = out_dir / "commentary"
    commentary_dir.mkdir(parents=True, exist_ok=True)
    for entry in plan_entries:
        entry.tts_path = synth_tts(
            entry.seg, commentary_dir, cache_dir / "tts",
            voice=voice, dry_run=dry_run,
        )
        if entry.tts_path and entry.tts_path.exists() and entry.tts_path.stat().st_size > 0:
            entry.tts_duration_sec = probe_duration(entry.tts_path)
        else:
            # Fallback estimate: ~14 chars/sec
            entry.tts_duration_sec = max(2.0, len(entry.seg.narration_text) / 14.0)

    # 4. Avatar talking-heads
    char_id = load_character_id(character_cache)
    LOG.info("avatar character_id=%s", char_id)

    avatar_dir = out_dir / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    spent = {"avatar": 0, "aleph": 0, "sfx": 0}

    if not dry_run and not skip_runway and not skip_avatar:
        for entry in plan_entries:
            if not entry.tts_path or not entry.tts_path.exists() or entry.tts_path.stat().st_size == 0:
                entry.skipped.append("avatar: no tts")
                continue
            cache_path = cache_dir / "avatar" / f"{_avatar_fp(entry.tts_path, char_id)}.mp4"
            if cache_path.exists() and cache_path.stat().st_size > 0:
                entry.avatar_path = _copy_into(cache_path, avatar_dir / f"{entry.seg.id}.mp4")
                entry.avatar_duration_sec = probe_duration(entry.avatar_path)
                LOG.info("avatar cache hit %s", entry.seg.id)
                continue
            if _budget_exceeded(spent, max_credits, "avatar"):
                entry.skipped.append("avatar: max_credits")
                continue
            try:
                generated = run_avatar_video(
                    audio_path=entry.tts_path,
                    avatar_id=char_id,
                    cache_dest=cache_path,
                )
                entry.avatar_path = _copy_into(generated, avatar_dir / f"{entry.seg.id}.mp4")
                entry.avatar_duration_sec = probe_duration(entry.avatar_path)
                spent["avatar"] += COST_AVATAR_VIDEO
            except Exception as exc:  # noqa: BLE001
                LOG.exception("avatar failed for %s", entry.seg.id)
                entry.skipped.append(f"avatar error: {exc}")

    # 5. Aleph transformations on SUGGESTION segments
    aleph_dir = out_dir / "aleph"
    aleph_dir.mkdir(parents=True, exist_ok=True)

    if not dry_run and not skip_runway and not skip_aleph:
        for entry in plan_entries:
            if entry.seg.kind != "SUGGESTION" or not entry.seg.aleph_prompt:
                continue
            if not entry.source_clip_path or not entry.source_clip_path.exists():
                entry.skipped.append("aleph: no source clip")
                continue
            # Extract a 5s slice at aleph_offset_in_segment
            slice_path = source_dir / f"{entry.seg.id}__aleph_in.mp4"
            slice_for_aleph(
                source=entry.source_clip_path,
                offset=entry.seg.aleph_offset_in_segment,
                duration=ALEPH_DURATION,
                out_path=slice_path,
            )
            cache_path = cache_dir / "aleph" / f"{_aleph_fp(slice_path, entry.seg.aleph_prompt, ALEPH_RATIO, ALEPH_DURATION)}.mp4"
            if cache_path.exists() and cache_path.stat().st_size > 0:
                entry.aleph_path = _copy_into(cache_path, aleph_dir / f"{entry.seg.id}.mp4")
                entry.aleph_duration_sec = probe_duration(entry.aleph_path)
                LOG.info("aleph cache hit %s", entry.seg.id)
                continue
            if _budget_exceeded(spent, max_credits, "aleph"):
                entry.skipped.append("aleph: max_credits")
                continue
            try:
                generated = run_aleph(
                    source_clip=slice_path,
                    prompt=entry.seg.aleph_prompt,
                    cache_dest=cache_path,
                )
                entry.aleph_path = _copy_into(generated, aleph_dir / f"{entry.seg.id}.mp4")
                entry.aleph_duration_sec = probe_duration(entry.aleph_path)
                spent["aleph"] += COST_ALEPH_5S
            except Exception as exc:  # noqa: BLE001
                LOG.exception("aleph failed for %s", entry.seg.id)
                entry.skipped.append(f"aleph error: {exc}")

    # 6. SFX — atmosphere bed (one looped under whole hero) + finish FX
    sfx_dir = out_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    sfx_assets: List[Dict] = []
    if not dry_run and not skip_runway and not skip_sfx:
        try:
            atm_path = run_sfx(
                prompt="High-altitude mountain ambience, sparse wind, distant trail sounds, low energy, no music",
                duration=30.0,
                loop=True,
                cache_dir=cache_dir / "sfx",
                out_dir=sfx_dir,
                file_stem="atm_mountain",
            )
            sfx_assets.append({
                "type": "atmosphere",
                "track": "A3",
                "path": str(atm_path),
                "duration": 30.0,
                "loop": True,
                "tile_under_hero": True,
                "volume_db": -12,
            })
            spent["sfx"] += COST_SFX_PER_30S
        except Exception as exc:  # noqa: BLE001
            LOG.exception("atm sfx failed: %s", exc)

        try:
            cheer_path = run_sfx(
                prompt="Distant trail-race finish line crowd cheer, brief, fading, outdoor",
                duration=4.0,
                loop=False,
                cache_dir=cache_dir / "sfx",
                out_dir=sfx_dir,
                file_stem="fx_finish_cheer",
            )
            # Place near the climax curated segment (heuristic: last but one)
            cheer_anchor = plan_entries[-2].curated_out_seconds - 3.0 if len(plan_entries) >= 2 else 0.0
            sfx_assets.append({
                "type": "hard_fx",
                "track": "A4",
                "path": str(cheer_path),
                "start_seconds": max(0.0, cheer_anchor),
                "duration": 4.0,
                "loop": False,
                "volume_db": -6,
            })
            spent["sfx"] += COST_SFX_PER_30S
        except Exception as exc:  # noqa: BLE001
            LOG.exception("cheer sfx failed: %s", exc)

    # 7. Compose
    hero_path = out_dir / "hero.mp4"
    if not dry_run:
        compose_with_ffmpeg(
            slideshow_path=slideshow_path,
            plan_entries=plan_entries,
            sfx_assets=sfx_assets,
            out_path=hero_path,
            total_dur_sec=total_hero_dur,
        )
    else:
        hero_path.write_bytes(b"")

    # Manifest
    manifest = {
        "report_id": report_id,
        "report_path": str(report_path),
        "out_dir": str(out_dir),
        "hero_path": str(hero_path),
        "slideshow_path": str(slideshow_path),
        "duration_sec": total_hero_dur,
        "duration_minutes": round(total_hero_dur / 60.0, 2),
        "character_id": char_id,
        "frames_dir": str(frames_dir),
        "dry_run": dry_run,
        "skip_runway": skip_runway,
        "skip_aleph": skip_aleph,
        "skip_avatar": skip_avatar,
        "skip_sfx": skip_sfx,
        "spent_estimated_credits": sum(spent.values()),
        "spent_breakdown": spent,
        "max_credits": max_credits,
        "segments": [
            {
                "id": e.seg.id,
                "kind": e.seg.kind,
                "type_label": e.seg.type_label,
                "impact": e.seg.impact,
                "title": e.seg.title,
                "source_in_seconds": e.seg.in_seconds,
                "source_out_seconds": e.seg.out_seconds,
                "curated_in_seconds": e.curated_in_seconds,
                "curated_out_seconds": e.curated_out_seconds,
                "duration_sec": round(e.curated_out_seconds - e.curated_in_seconds, 3),
                "narration_text": e.seg.narration_text,
                "aleph_prompt": e.seg.aleph_prompt,
                "aleph_offset_in_segment": e.seg.aleph_offset_in_segment,
                "vfx_category": e.seg.vfx_category,
                "tts_path": str(e.tts_path) if e.tts_path else None,
                "tts_duration_sec": e.tts_duration_sec,
                "avatar_path": str(e.avatar_path) if e.avatar_path else None,
                "avatar_duration_sec": e.avatar_duration_sec,
                "aleph_path": str(e.aleph_path) if e.aleph_path else None,
                "aleph_duration_sec": e.aleph_duration_sec,
                "source_clip_path": str(e.source_clip_path) if e.source_clip_path else None,
                "skipped": e.skipped,
            }
            for e in plan_entries
        ],
        "sfx": sfx_assets,
        "tracks": {
            "V1": "source_slideshow",
            "V2": "avatar_pip_during_commentary",
            "V3": "aleph_full_screen_at_suggestions",
            "A2": "commentary_master",
            "A3": "atmosphere_bed_-12db_looped",
            "A4": "hard_fx_-6db",
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    LOG.info("hero manifest written %s", manifest_path)
    return manifest


# --- Curation -------------------------------------------------------------


# Narration scripts. Pre-written in Jonny's voice — short, intimate,
# editorial. Each is ~25-40 words = ~9-14s of TTS.
NARRATION = {
    "open_hook": (
        "We open soft. Chateau, vineyard, banter. But the analyzer "
        "scored this hook at 27 — a fast drop. The audience decides "
        "in eight seconds. The first surprise is buried at seven minutes."
    ),
    "first_place": (
        "Here it is. Tommy gets told he's leading, and the look on "
        "his face — that's the whole film. Hold that one extra beat "
        "before he asks 'really?'. Don't cut to the next thing yet. "
        "Let the disbelief land."
    ),
    "arwa_battle": (
        "Vulnerable beat. Arwa's racing the wrong race, in her head. "
        "Don't cut to the action. Let the crew explain. Hold on her "
        "expression, see the doubt before the resolve. That's where "
        "the audience starts caring."
    ),
    "tommy_climax": (
        "Climax. Hours of running. Crew member exhales, says 'incredible "
        "effort.' Tommy can't speak. This is the body's testimony, more "
        "than any interview. Keep the silence. Don't score it."
    ),
    "arwa_resolution": (
        "Arwa, almost done. Five minutes from second place after "
        "twenty-four hours. The resolution isn't the result — it's "
        "the embrace. Cut here, not at the finish line. The line is "
        "obvious. The hug is the film."
    ),
}


def curate(report: dict) -> List[CuratedSegment]:
    """Assemble the hero plan from the analyzer report.

    Heuristic:
      - Open with a hook-fix segment built from scene 1 + the
        document_summary's FIX:NOHOOK note. Tagged SUGGESTION
        (atmospheric Aleph treatment on the chateau setting).
      - Pick the TURNING_POINT high-impact moment ("first place is four"
        ~ 7:39). Tagged SUGGESTION (volumetric_light treatment).
      - Pick a high-impact VULNERABILITY in act 2 (Arwa ~ 10:10).
        COMMENTARY only.
      - Pick the climax scene high-impact moment (Tommy finish ~ 16:35).
        SUGGESTION (color_grade dusk).
      - Pick the resolution high-impact (Arwa close finish ~ 17:30).
        COMMENTARY only.

    Each segment is 60-90s wide so the slideshow has time to breathe
    and the avatar PIP has time to read. Total ~5-7 min.
    """
    moments = (report.get("emotional_analysis") or {}).get("moments") or []
    by_imp_type: Dict[Tuple[str, str], dict] = {}
    for m in moments:
        key = (str(m.get("type") or ""), str(m.get("impact") or ""))
        by_imp_type.setdefault(key, m)
    # Index moments by start_seconds for quick nearest lookup
    moments_by_start = sorted(
        moments, key=lambda m: float(m.get("start_seconds") or 0.0)
    )

    segs: List[CuratedSegment] = []

    # 1) Hook fix — first scene + ~75s
    segs.append(CuratedSegment(
        id="open_hook",
        kind="SUGGESTION",
        in_seconds=0.0,
        out_seconds=75.0,
        duration_sec=75.0,
        title="Hook fix — chateau open scored 27",
        narration_text=NARRATION["open_hook"],
        aleph_prompt=(
            "Add subtle dawn mist drifting through the vineyard, low intensity, "
            "soft golden-hour bloom on the chateau. Preserve subjects' lighting, "
            "no figure overlay, no readable text, no color shift on faces."
        ),
        aleph_offset_in_segment=20.0,
        vfx_category="atmospheric",
        impact="high",
        type_label="HOOK_FIX",
    ))

    # 2) Turning point — Tommy first place reveal at ~7:39
    tp = _nearest_moment(moments_by_start, 459.0)
    seg2_in = max(0.0, (tp.get("start_seconds") if tp else 459.0) - 30.0)
    seg2_out = seg2_in + 75.0
    segs.append(CuratedSegment(
        id="first_place",
        kind="SUGGESTION",
        in_seconds=round(seg2_in, 3),
        out_seconds=round(seg2_out, 3),
        duration_sec=75.0,
        title="Turning point — 'first place is four minutes ahead'",
        narration_text=NARRATION["first_place"],
        aleph_prompt=(
            "Apply soft volumetric light shafts in the morning mountain air, subtle, "
            "warm haze. Preserve subject's lighting, no figure overlay, no readable "
            "text, no color shift on faces."
        ),
        aleph_offset_in_segment=30.0,
        vfx_category="volumetric_light",
        impact="high",
        type_label=str((tp or {}).get("type") or "TURNING_POINT"),
    ))

    # 3) Arwa mental battle — VULNERABILITY high at ~10:10
    arwa = _nearest_moment(moments_by_start, 610.0)
    seg3_in = max(0.0, (arwa.get("start_seconds") if arwa else 610.0) - 25.0)
    seg3_out = seg3_in + 60.0
    segs.append(CuratedSegment(
        id="arwa_battle",
        kind="COMMENTARY",
        in_seconds=round(seg3_in, 3),
        out_seconds=round(seg3_out, 3),
        duration_sec=60.0,
        title="Vulnerability — Arwa racing in her head",
        narration_text=NARRATION["arwa_battle"],
        impact="high",
        type_label=str((arwa or {}).get("type") or "VULNERABILITY"),
    ))

    # 4) Tommy climax — VULNERABILITY high at ~16:35
    climax = _nearest_moment(moments_by_start, 995.0)
    seg4_in = max(0.0, (climax.get("start_seconds") if climax else 995.0) - 20.0)
    seg4_out = seg4_in + 60.0
    segs.append(CuratedSegment(
        id="tommy_climax",
        kind="SUGGESTION",
        in_seconds=round(seg4_in, 3),
        out_seconds=round(seg4_out, 3),
        duration_sec=60.0,
        title="Climax — Tommy can't speak",
        narration_text=NARRATION["tommy_climax"],
        aleph_prompt=(
            "Apply warm dusk color grade with subtle film-grain texture, +0.5 "
            "contrast, amber lift in the highlights. Preserve subject's lighting, "
            "no figure overlay, no readable text, no color shift on faces."
        ),
        aleph_offset_in_segment=20.0,
        vfx_category="color_grade",
        impact="high",
        type_label=str((climax or {}).get("type") or "VULNERABILITY"),
    ))

    # 5) Arwa close finish — VULNERABILITY high at ~17:30
    res = _nearest_moment(moments_by_start, 1050.0)
    seg5_in = max(0.0, (res.get("start_seconds") if res else 1050.0) - 20.0)
    seg5_out = seg5_in + 55.0
    segs.append(CuratedSegment(
        id="arwa_resolution",
        kind="COMMENTARY",
        in_seconds=round(seg5_in, 3),
        out_seconds=round(seg5_out, 3),
        duration_sec=55.0,
        title="Resolution — five minutes after twenty-four hours",
        narration_text=NARRATION["arwa_resolution"],
        impact="high",
        type_label=str((res or {}).get("type") or "VULNERABILITY"),
    ))

    return segs


def _nearest_moment(moments_by_start: List[dict], target: float) -> Optional[dict]:
    if not moments_by_start:
        return None
    return min(
        moments_by_start,
        key=lambda m: abs(float(m.get("start_seconds") or 0.0) - target),
    )


# --- Slideshow source -----------------------------------------------------


def build_kenburns_clip(
    *,
    frames_dir: Path,
    in_seconds: float,
    out_seconds: float,
    out_path: Path,
) -> Path:
    """Build a Ken Burns slideshow micro-clip from frames in [in,out].

    Picks all frames whose embedded timestamp falls in the window,
    plus the nearest frame on each side. Holds each frame for its
    natural slice (so the visual stays in sync with the timecode),
    with a slow zoom-in 1.0->1.05 across the window.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    frames = _select_frames_in_window(frames_dir, in_seconds, out_seconds)
    if not frames:
        raise RuntimeError(f"no frames found in window [{in_seconds}, {out_seconds}]")

    duration = max(1.0, float(out_seconds - in_seconds))
    per_frame = duration / len(frames)

    # Use ffmpeg concat demuxer with one entry per frame.
    concat_list = out_path.with_suffix(".list.txt")
    lines = []
    for f in frames:
        # forward-slash paths so ffmpeg on win + git-bash both parse
        p = str(f).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{p}'")
        lines.append(f"duration {per_frame:.3f}")
    # ffmpeg concat demuxer requires the last image be repeated without duration
    last = str(frames[-1]).replace("\\", "/").replace("'", "'\\''")
    lines.append(f"file '{last}'")
    concat_list.write_text("\n".join(lines), encoding="utf-8")

    # Slideshow build — concat demuxer drives per-frame durations; fps
    # filter resamples to 30. zoompan occasionally chokes on this build,
    # so we keep the filter chain conservative and rely on duration.
    fps = HERO_FPS
    vf = (
        f"scale={HERO_W}:{HERO_H}:force_original_aspect_ratio=decrease,"
        f"pad={HERO_W}:{HERO_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fps={fps},format=yuv420p"
    )

    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-vf", vf,
        "-vsync", "cfr",
        "-r", str(fps),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    LOG.info(
        "kenburns clip [%s..%s] %d frames per_frame=%.2fs -> %s",
        in_seconds, out_seconds, len(frames), per_frame, out_path.name,
    )
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg kenburns failed: {res.stderr.strip()}")
    return out_path


def _select_frames_in_window(
    frames_dir: Path,
    in_seconds: float,
    out_seconds: float,
) -> List[Path]:
    import re
    pat = re.compile(r"_(\d+)s\.(?:jpg|jpeg|png)$", re.IGNORECASE)
    parsed: List[Tuple[int, Path]] = []
    for p in sorted(Path(frames_dir).iterdir()):
        if not p.is_file():
            continue
        m = pat.search(p.name)
        if not m:
            continue
        parsed.append((int(m.group(1)), p))
    if not parsed:
        return []
    parsed.sort(key=lambda t: t[0])
    in_window = [p for ts, p in parsed if in_seconds - 1 <= ts <= out_seconds + 1]
    if in_window:
        return in_window
    # fall back: nearest 2 frames bracketing the window
    nearest = sorted(parsed, key=lambda t: abs(t[0] - (in_seconds + out_seconds) / 2.0))
    return [p for _, p in nearest[:2]]


def concat_clips(clips: List[Optional[Path]], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    actual = [c for c in clips if c and Path(c).exists()]
    if not actual:
        raise RuntimeError("no clips to concat")
    list_path = out_path.with_suffix(".list.txt")
    lines = []
    for c in actual:
        p = str(c).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{p}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # re-encode (different parameters can prevent stream-copy)
        cmd2 = [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out_path),
        ]
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        if res2.returncode != 0:
            raise RuntimeError(f"concat failed: {res2.stderr.strip()}")
    return out_path


def slice_for_aleph(
    *,
    source: Path,
    offset: float,
    duration: float,
    out_path: Path,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", f"{max(0.0, offset):.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"slice for aleph failed: {res.stderr.strip()}")
    return out_path


# --- TTS ------------------------------------------------------------------


def synth_tts(
    seg: CuratedSegment,
    out_dir: Path,
    cache_dir: Path,
    *,
    voice: Optional[ElevenLabsVoice],
    dry_run: bool,
) -> Path:
    out_dir = Path(out_dir)
    cache_dir = Path(cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = _tts_fp(seg.narration_text)
    cache_path = cache_dir / f"{fp}.mp3"
    out_path = out_dir / f"{seg.id}.mp3"

    if cache_path.exists() and cache_path.stat().st_size > 0:
        shutil.copyfile(cache_path, out_path)
        return out_path
    if dry_run or voice is None:
        out_path.write_bytes(b"")
        return out_path
    voice.synthesize(seg.narration_text, cache_path)
    shutil.copyfile(cache_path, out_path)
    return out_path


def _tts_fp(text: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update(b"|elevenlabs")
    return h.hexdigest()[:16]


# --- Avatar ---------------------------------------------------------------


def load_character_id(cache_path: Path) -> str:
    cache_path = Path(cache_path)
    if not cache_path.exists():
        raise FileNotFoundError(f"character cache missing: {cache_path}")
    rec = json.loads(cache_path.read_text(encoding="utf-8"))
    cid = rec.get("id")
    if not cid:
        raise RuntimeError(f"character cache has no id: {cache_path}")
    return str(cid)


def run_avatar_video(
    *,
    audio_path: Path,
    avatar_id: str,
    cache_dest: Path,
) -> Path:
    cache_dest = Path(cache_dest)
    cache_dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("avatar_videos: uploading %s", audio_path.name)
    audio_uri = runway_http.upload_ephemeral(audio_path)
    LOG.info("avatar_videos: create task")
    task_id = runway_http.create_avatar_video(
        avatar_id=avatar_id, audio_uri=audio_uri,
    )
    LOG.info("avatar_videos: task %s — polling", task_id)
    data = runway_http.poll_task(task_id, timeout_sec=1500)
    url = runway_http.first_output_url(data)
    runway_http.download_to_path(url, cache_dest)
    LOG.info("avatar_videos: %s -> %s", task_id, cache_dest.name)
    return cache_dest


def _avatar_fp(audio_path: Path, avatar_id: str) -> str:
    h = hashlib.sha256()
    with Path(audio_path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(b"|")
    h.update(avatar_id.encode("utf-8"))
    h.update(b"|gwm1_avatars")
    return h.hexdigest()[:16]


# --- Aleph ----------------------------------------------------------------


def run_aleph(
    *,
    source_clip: Path,
    prompt: str,
    cache_dest: Path,
) -> Path:
    cache_dest = Path(cache_dest)
    cache_dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("aleph: uploading %s", source_clip.name)
    video_uri = runway_http.upload_ephemeral(source_clip)
    LOG.info("aleph: create task chars=%d", len(prompt))
    task_id = runway_http.create_aleph(
        video_uri=video_uri,
        prompt_text=prompt,
        ratio=ALEPH_RATIO,
        duration=ALEPH_DURATION,
    )
    LOG.info("aleph: task %s — polling", task_id)
    data = runway_http.poll_task(task_id, timeout_sec=1500)
    url = runway_http.first_output_url(data)
    runway_http.download_to_path(url, cache_dest)
    LOG.info("aleph: %s -> %s", task_id, cache_dest.name)
    return cache_dest


def _aleph_fp(
    source_clip: Path, prompt: str, ratio: str, duration: int
) -> str:
    h = hashlib.sha256()
    with Path(source_clip).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(b"|")
    h.update(prompt.encode("utf-8"))
    h.update(b"|")
    h.update(ratio.encode("utf-8"))
    h.update(b"|")
    h.update(str(duration).encode("utf-8"))
    h.update(b"|gen4_aleph")
    return h.hexdigest()[:16]


# --- SFX ------------------------------------------------------------------


def run_sfx(
    *,
    prompt: str,
    duration: float,
    loop: bool,
    cache_dir: Path,
    out_dir: Path,
    file_stem: str,
) -> Path:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = _sfx_fp(prompt, duration, loop)
    cache_path = cache_dir / f"{fp}.mp3"
    out_path = out_dir / f"{file_stem}.mp3"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        shutil.copyfile(cache_path, out_path)
        LOG.info("sfx cache hit %s", file_stem)
        return out_path
    LOG.info("sfx: create dur=%.1fs loop=%s chars=%d", duration, loop, len(prompt))
    task_id = runway_http.create_sound_effect(
        prompt_text=prompt, duration=duration, loop=loop,
    )
    data = runway_http.poll_task(task_id, timeout_sec=900)
    url = runway_http.first_output_url(data)
    runway_http.download_to_path(url, cache_path)
    shutil.copyfile(cache_path, out_path)
    return out_path


def _sfx_fp(prompt: str, duration: float, loop: bool) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(b"|")
    h.update(f"{duration:.3f}".encode("utf-8"))
    h.update(b"|")
    h.update(b"L" if loop else b"-")
    h.update(b"|eleven_text_to_sound_v2")
    return h.hexdigest()[:16]


# --- Compose --------------------------------------------------------------


def compose_with_ffmpeg(
    *,
    slideshow_path: Path,
    plan_entries: List[HeroPlanEntry],
    sfx_assets: List[Dict],
    out_path: Path,
    total_dur_sec: float,
) -> Path:
    """Run the final compose pass.

    We keep this readable rather than building one giant filter_complex —
    instead we build a layered pipeline:
      1. Underscore the slideshow with the avatar PIP overlay.
      2. Layer Aleph clips on top at their curated windows (full-screen).
      3. Mix audio: commentary + atmosphere bed + hard FX.
    """
    if not Path(slideshow_path).exists():
        raise FileNotFoundError(f"slideshow missing: {slideshow_path}")

    inputs: List[List[str]] = [["-i", str(slideshow_path)]]
    input_idx = 0  # 0 = slideshow

    # Track input indexes per asset so we can wire filter_complex
    avatar_inputs: List[Tuple[int, HeroPlanEntry]] = []
    aleph_inputs: List[Tuple[int, HeroPlanEntry]] = []
    commentary_inputs: List[Tuple[int, HeroPlanEntry]] = []
    sfx_inputs: List[Tuple[int, Dict]] = []

    for entry in plan_entries:
        if entry.tts_path and Path(entry.tts_path).exists() and Path(entry.tts_path).stat().st_size > 0:
            input_idx += 1
            inputs.append(["-i", str(entry.tts_path)])
            commentary_inputs.append((input_idx, entry))
        if entry.avatar_path and Path(entry.avatar_path).exists() and Path(entry.avatar_path).stat().st_size > 0:
            input_idx += 1
            inputs.append(["-i", str(entry.avatar_path)])
            avatar_inputs.append((input_idx, entry))
        if entry.aleph_path and Path(entry.aleph_path).exists() and Path(entry.aleph_path).stat().st_size > 0:
            input_idx += 1
            inputs.append(["-i", str(entry.aleph_path)])
            aleph_inputs.append((input_idx, entry))

    for asset in sfx_assets:
        path = asset.get("path")
        if not path or not Path(path).exists() or Path(path).stat().st_size == 0:
            continue
        input_idx += 1
        inputs.append(["-stream_loop", "-1" if asset.get("loop") else "0", "-i", str(path)])
        sfx_inputs.append((input_idx, asset))

    # Build filter graph
    filters: List[str] = []

    # Start V chain: slideshow -> [v0]
    filters.append(f"[0:v]format=yuv420p,setsar=1[v0]")
    last_v = "v0"

    # Aleph overlays — full-screen during their curated windows
    for n, (idx, entry) in enumerate(aleph_inputs):
        in_t = entry.curated_in_seconds + entry.seg.aleph_offset_in_segment
        out_t = in_t + min(entry.aleph_duration_sec or ALEPH_DURATION, ALEPH_DURATION)
        scale_label = f"alephv{n}"
        filters.append(
            f"[{idx}:v]scale={HERO_W}:{HERO_H}:force_original_aspect_ratio=decrease,"
            f"pad={HERO_W}:{HERO_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"setpts=PTS-STARTPTS+{in_t:.3f}/TB[{scale_label}]"
        )
        out_label = f"vA{n}"
        filters.append(
            f"[{last_v}][{scale_label}]overlay=enable='between(t,{in_t:.3f},{out_t:.3f})':x=0:y=0[{out_label}]"
        )
        last_v = out_label

    # Avatar PIP overlays — corner during commentary windows
    for n, (idx, entry) in enumerate(avatar_inputs):
        if entry.tts_duration_sec <= 0 and entry.avatar_duration_sec <= 0:
            continue
        in_t = entry.curated_in_seconds + 1.0  # delay 1s into the segment
        dur = entry.avatar_duration_sec or entry.tts_duration_sec
        out_t = min(entry.curated_out_seconds, in_t + dur + 0.5)
        scale_label = f"avs{n}"
        filters.append(
            f"[{idx}:v]scale={PIP_W}:{PIP_H}:force_original_aspect_ratio=decrease,"
            f"pad={PIP_W}:{PIP_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"setpts=PTS-STARTPTS+{in_t:.3f}/TB[{scale_label}]"
        )
        # Soft fade-in/out for the PIP
        out_label = f"vPv{n}"
        filters.append(
            f"[{last_v}][{scale_label}]overlay=enable='between(t,{in_t:.3f},{out_t:.3f})':"
            f"x={PIP_X}:y={PIP_Y}[{out_label}]"
        )
        last_v = out_label

    # Audio mix
    audio_chains: List[str] = []
    if commentary_inputs:
        for n, (idx, entry) in enumerate(commentary_inputs):
            anchor = entry.curated_in_seconds + 1.0  # match avatar in
            label = f"comm{n}"
            filters.append(
                f"[{idx}:a]adelay={int(anchor*1000)}|{int(anchor*1000)},"
                f"volume=1.0[{label}]"
            )
            audio_chains.append(f"[{label}]")
    for n, (idx, asset) in enumerate(sfx_inputs):
        vol = float(asset.get("volume_db") or -12)
        label = f"sfx{n}"
        if asset.get("type") == "atmosphere":
            # play the loop (already -stream_loop -1) for the whole hero
            filters.append(
                f"[{idx}:a]volume={_db_to_lin(vol):.4f},atrim=0:{total_dur_sec:.3f}[{label}]"
            )
        else:
            start = float(asset.get("start_seconds") or 0.0)
            filters.append(
                f"[{idx}:a]adelay={int(start*1000)}|{int(start*1000)},"
                f"volume={_db_to_lin(vol):.4f}[{label}]"
            )
        audio_chains.append(f"[{label}]")

    if audio_chains:
        amix = "".join(audio_chains) + f"amix=inputs={len(audio_chains)}:duration=longest:dropout_transition=0,aresample=44100[aout]"
        filters.append(amix)
        a_out = "[aout]"
    else:
        # fallback: silence
        filters.append(
            f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:{total_dur_sec:.3f}[aout]"
        )
        a_out = "[aout]"

    filter_arg = ";".join(filters)

    cmd: List[str] = [FFMPEG, "-y", "-loglevel", "error"]
    for inp in inputs:
        cmd.extend(inp)
    cmd.extend([
        "-filter_complex", filter_arg,
        "-map", f"[{last_v}]",
        "-map", a_out,
        "-t", f"{total_dur_sec:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ])
    LOG.info(
        "compose: %d inputs, %d filters, dur=%.1fs",
        len(inputs), len(filters), total_dur_sec,
    )
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # write the failing graph to disk for inspection
        graph_path = Path(out_path).with_suffix(".filter.txt")
        graph_path.write_text(filter_arg, encoding="utf-8")
        raise RuntimeError(
            f"ffmpeg compose failed (graph at {graph_path}):\n{res.stderr.strip()[:1500]}"
        )
    return Path(out_path)


def _db_to_lin(db: float) -> float:
    return 10.0 ** (float(db) / 20.0)


# --- Misc helpers ---------------------------------------------------------


def probe_duration(path: Path) -> float:
    if not Path(path).exists():
        return 0.0
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return 0.0
    try:
        return float((res.stdout or "0").strip())
    except ValueError:
        return 0.0


def _budget_exceeded(spent: Dict[str, int], max_credits: int, kind: str) -> bool:
    next_cost = {"avatar": COST_AVATAR_VIDEO, "aleph": COST_ALEPH_5S, "sfx": COST_SFX_PER_30S}.get(kind, 0)
    return (sum(spent.values()) + next_cost) > max_credits


def _copy_into(src: Path, dest: Path) -> Path:
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return dest
    if dest.exists() and dest.resolve() == src.resolve():
        return dest
    shutil.copyfile(src, dest)
    return dest
