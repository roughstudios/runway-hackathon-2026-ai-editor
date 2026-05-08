"""AI Jonny avatar — per-segment talking-head animation via Runway.

Stage 3 of the hackathon submission. Builds on Charlie-v3's commentary
segments (each carries an ``audio_path`` to a pre-rendered TTS .mp3) and
on ``avatar_creator.get_or_create_jonny_character`` (which gives us the
custom avatar id). For each commentary segment, this module:

  1. Fingerprints the segment's audio bytes + character id + model name.
  2. Hits an on-disk cache; same audio + same avatar = same MP4 every
     time. Avatar generation is the most expensive thing Stage 3 spends,
     so caching is a hard requirement.
  3. Uploads the segment's audio file via ``uploads.create_ephemeral``
     (the upload returns a ``runway://`` URI; ``avatar_videos.create``
     accepts it the same way the rest of this codebase's endpoints do).
  4. Calls ``client.avatar_videos.create`` with
     ``avatar={"type": "custom", "avatarId": <character_id>}``,
     ``model="gwm1_avatars"``, ``speech={"type": "audio", "audio": <uri>}``.
  5. Polls via ``task.wait_for_task_output(timeout=...)`` (the standard
     Runway SDK polling helper, same as ``aleph_client``).
  6. Downloads the resulting MP4 to the per-segment output directory.

Why ``avatar_videos`` and not ``character_performance``?
  - ``avatar_videos.create`` is the audio-driven talking-head endpoint:
    pass speech (audio or text) and a custom avatar id, get a lip-synced
    video back. That's the Stage-3 hot path.
  - ``character_performance.create`` (Act-Two) requires a *reference
    video* of someone performing the way you want the character to —
    no audio input, and a 3-30s duration cap. It's the "ONE wow cameo"
    slot Echo-v3 reserves for visualization use; Stage 3 must not burn
    that quota on commentary playback.

The build_avatar_track orchestrator at the bottom is the entrypoint
``cli.py`` calls. It walks the segments JSON, animates each, and writes
``output/avatar_segments/<report_id>/manifest.json`` plus per-segment
MP4s in the agreed Delta-v3 format. The manifest matches the Delta-v3
``AvatarOverlay`` contract — pass each segment's ``mp4_path`` as the
``avatarVideoUrl`` prop while that segment is the active commentary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv

from ai_editor.avatar_creator import (
    CHARACTER_CACHE_PATH,
    DEFAULT_REFERENCE_IMAGE,
    JonnyCharacter,
    get_or_create_jonny_character,
)

LOG = logging.getLogger(__name__)

AVATAR_VIDEO_MODEL = "gwm1_avatars"
AVATAR_VIDEO_TIMEOUT_SEC = 1200  # talking-head jobs run in minutes
DOWNLOAD_TIMEOUT_SEC = 300.0
DEFAULT_AVATAR_CACHE_DIR = Path(".cache") / "avatar_videos"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "output" / "avatar_segments"


# --- Result dataclasses --------------------------------------------------


@dataclass
class AvatarVideoResult:
    """One animated commentary segment."""

    video_path: Path
    video_url: str
    task_id: str
    fingerprint: str
    cached: bool


@dataclass
class AvatarTrackEntry:
    """Manifest entry consumed by Delta-v3's AvatarOverlay component."""

    comment_id: str
    start_seconds: float
    end_seconds: float
    duration_sec: float
    narration_text: str
    mp4_path: str
    cached: bool
    task_id: str
    fingerprint: str
    skipped_reason: Optional[str] = None


# --- AvatarAnimator -------------------------------------------------------


class AvatarAnimator:
    """Per-segment talking-head animator. Construct once, reuse per run.

    The character id is fixed at construction time — Stage 3 has a single
    AI Jonny character, so we don't switch mid-run.
    """

    def __init__(
        self,
        character_id: str,
        runway_client=None,
        api_key: Optional[str] = None,
        cache_dir: Path = DEFAULT_AVATAR_CACHE_DIR,
        model: str = AVATAR_VIDEO_MODEL,
    ):
        self._character_id = character_id
        self._model = model
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        if runway_client is None:
            load_dotenv()
            key = api_key or os.environ.get("RUNWAYML_API_SECRET")
            if not key:
                raise RuntimeError(
                    "RUNWAYML_API_SECRET not set. Add it to .env or export it. "
                    "See .env.example."
                )
            from runwayml import RunwayML
            runway_client = RunwayML(api_key=key)
        self._client = runway_client

    @property
    def character_id(self) -> str:
        return self._character_id

    @property
    def raw(self):
        return self._client

    def animate(self, audio_path: Path, *, label: str = "") -> AvatarVideoResult:
        """Generate (or reuse) one talking-head MP4 for one audio clip."""
        audio_path = Path(audio_path)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise FileNotFoundError(
                f"Avatar animation source audio missing or empty: {audio_path}"
            )

        fp = _fingerprint(
            audio_path=audio_path,
            character_id=self._character_id,
            model=self._model,
        )
        cache_video = self._cache_dir / f"{fp}.mp4"
        cache_meta = self._cache_dir / f"{fp}.url"
        if cache_video.exists() and cache_video.stat().st_size > 0:
            video_url = (
                cache_meta.read_text(encoding="utf-8").strip()
                if cache_meta.exists() else ""
            )
            LOG.info("AvatarVideo cache hit %s (label=%r)", fp, label)
            return AvatarVideoResult(
                video_path=cache_video,
                video_url=video_url,
                task_id="<cached>",
                fingerprint=fp,
                cached=True,
            )

        upload_mime = _audio_mime_for(audio_path)
        LOG.info(
            "AvatarVideo upload audio %s (%d bytes) for fp=%s label=%r",
            audio_path.name, audio_path.stat().st_size, fp, label,
        )
        with audio_path.open("rb") as fh:
            upload = self._client.uploads.create_ephemeral(
                file=(audio_path.name, fh, upload_mime),
            )
        audio_uri = upload.uri

        avatar = {"type": "custom", "avatarId": self._character_id}
        speech = {"type": "audio", "audio": audio_uri}
        LOG.info(
            "AvatarVideo create model=%s avatar=%s",
            self._model, self._character_id,
        )
        task = self._client.avatar_videos.create(
            avatar=avatar,
            model=self._model,
            speech=speech,
        )
        result = task.wait_for_task_output(timeout=AVATAR_VIDEO_TIMEOUT_SEC)
        if not result.output:
            raise RuntimeError(
                f"AvatarVideo task {task.id} returned empty output"
            )
        video_url = result.output[0]
        _download(video_url, cache_video)
        cache_meta.write_text(video_url, encoding="utf-8")
        LOG.info(
            "AvatarVideo done fp=%s task=%s bytes=%d",
            fp, task.id, cache_video.stat().st_size,
        )
        return AvatarVideoResult(
            video_path=cache_video,
            video_url=video_url,
            task_id=task.id,
            fingerprint=fp,
            cached=False,
        )


# --- build-avatar-track orchestrator -------------------------------------


def build_avatar_track(
    *,
    segments_json_path: Path,
    out_dir: Optional[Path] = None,
    runway_client=None,
    character: Optional[JonnyCharacter] = None,
    reference_image: Path = DEFAULT_REFERENCE_IMAGE,
    character_cache_path: Path = CHARACTER_CACHE_PATH,
    limit: Optional[int] = None,
    max_segments: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """Walk Charlie-v3's commentary_segments.json, animate each segment,
    and write a manifest in the format the AvatarOverlay component expects.

    Args:
      segments_json_path: path to ``output/commentary/commentary_segments.json``
        produced by ``ai_editor.commentary_synthesizer.build_commentary``.
      out_dir: where to write the manifest + per-segment MP4s. Defaults to
        ``output/avatar_segments/<report_id>/`` derived from the segments
        file's report_path stem.
      character: a pre-built ``JonnyCharacter``. If omitted, we
        ``get_or_create_jonny_character`` automatically. Passing this in
        is the seam tests use to avoid hitting the API.
      limit: cap the number of segments processed (cheap testing).
      max_segments: hard cap on Runway calls per run; cached calls don't
        count against it. Like ``--max-aleph`` in build-visualizations.
      dry_run: skip Runway entirely. Manifest still written, mp4_path
        entries point at empty placeholder files for plumbing tests.

    Returns:
      A dict with the manifest contents + ``manifest_path`` so the CLI
      layer can print stats without reparsing the file.
    """

    segments_json_path = Path(segments_json_path)
    payload = json.loads(segments_json_path.read_text(encoding="utf-8"))
    raw_segments: List[dict] = payload.get("segments") or []
    if limit is not None:
        raw_segments = raw_segments[:limit]
    if not raw_segments:
        raise RuntimeError(
            f"No segments in {segments_json_path}. Run build-commentary first."
        )

    report_id = _report_id_from_payload(payload, segments_json_path)
    if out_dir is None:
        out_dir = DEFAULT_OUT_DIR / report_id
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if character is None and not dry_run:
        character = get_or_create_jonny_character(
            runway_client=runway_client,
            reference_image=reference_image,
            cache_path=character_cache_path,
        )

    animator: Optional[AvatarAnimator] = None
    if not dry_run:
        assert character is not None  # for type-checker
        animator = AvatarAnimator(
            character_id=character.id,
            runway_client=runway_client,
        )

    entries: List[AvatarTrackEntry] = []
    runway_calls = 0
    cached_calls = 0
    skipped = 0

    for i, seg in enumerate(raw_segments):
        comment_id = str(seg.get("comment_id") or f"segment.{i}")
        narration = str(seg.get("narration_text") or "")
        start = float(seg.get("start_seconds") or 0.0)
        end = float(seg.get("end_seconds") or start)
        duration = float(seg.get("duration_sec") or 0.0)
        audio_path_raw = seg.get("audio_path")

        seg_mp4 = out_dir / f"seg_{i:02d}.mp4"

        if dry_run:
            seg_mp4.write_bytes(b"")
            entries.append(AvatarTrackEntry(
                comment_id=comment_id,
                start_seconds=start,
                end_seconds=end,
                duration_sec=duration,
                narration_text=narration,
                mp4_path=str(seg_mp4),
                cached=False,
                task_id="<dry-run>",
                fingerprint="<dry-run>",
                skipped_reason=None,
            ))
            continue

        if not audio_path_raw:
            LOG.info("Skipping %s — no audio_path on segment", comment_id)
            entries.append(AvatarTrackEntry(
                comment_id=comment_id,
                start_seconds=start,
                end_seconds=end,
                duration_sec=duration,
                narration_text=narration,
                mp4_path="",
                cached=False,
                task_id="",
                fingerprint="",
                skipped_reason="no audio_path on segment",
            ))
            skipped += 1
            continue

        audio_path = Path(audio_path_raw)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            LOG.info(
                "Skipping %s — audio_path missing or empty: %s",
                comment_id, audio_path,
            )
            entries.append(AvatarTrackEntry(
                comment_id=comment_id,
                start_seconds=start,
                end_seconds=end,
                duration_sec=duration,
                narration_text=narration,
                mp4_path="",
                cached=False,
                task_id="",
                fingerprint="",
                skipped_reason="audio_path missing on disk",
            ))
            skipped += 1
            continue

        if (
            max_segments is not None
            and runway_calls >= max_segments
            and not _will_hit_cache(audio_path, animator)
        ):
            entries.append(AvatarTrackEntry(
                comment_id=comment_id,
                start_seconds=start,
                end_seconds=end,
                duration_sec=duration,
                narration_text=narration,
                mp4_path="",
                cached=False,
                task_id="",
                fingerprint="",
                skipped_reason=f"max_segments={max_segments} reached",
            ))
            skipped += 1
            continue

        assert animator is not None  # for type-checker
        result = animator.animate(audio_path, label=comment_id)
        # Copy the cache hit into the per-report output dir so the
        # manifest is self-contained for the player UI.
        shutil.copyfile(result.video_path, seg_mp4)
        if result.cached:
            cached_calls += 1
        else:
            runway_calls += 1
        entries.append(AvatarTrackEntry(
            comment_id=comment_id,
            start_seconds=start,
            end_seconds=end,
            duration_sec=duration,
            narration_text=narration,
            mp4_path=str(seg_mp4),
            cached=result.cached,
            task_id=result.task_id,
            fingerprint=result.fingerprint,
            skipped_reason=None,
        ))

    manifest_path = out_dir / "manifest.json"
    manifest = {
        "report_id": report_id,
        "report_path": payload.get("report_path"),
        "segments_json": str(segments_json_path),
        "character_id": character.id if character else None,
        "voice_preset": character.voice_preset if character else None,
        "model": AVATAR_VIDEO_MODEL,
        "dry_run": dry_run,
        "stats": {
            "n_total": len(raw_segments),
            "n_animated": len(entries) - skipped,
            "runway_calls": runway_calls,
            "cached_calls": cached_calls,
            "skipped": skipped,
        },
        "segments": [asdict(e) for e in entries],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    LOG.info(
        "AvatarTrack wrote %s — animated=%d cached=%d runway=%d skipped=%d",
        manifest_path, len(entries) - skipped, cached_calls, runway_calls, skipped,
    )
    return {**manifest, "manifest_path": str(manifest_path)}


# --- Internals -----------------------------------------------------------


def _fingerprint(
    *,
    audio_path: Path,
    character_id: str,
    model: str,
) -> str:
    h = hashlib.sha256()
    h.update(_sha256_file(audio_path).encode("ascii"))
    h.update(b"|")
    h.update(character_id.encode("utf-8"))
    h.update(b"|")
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _audio_mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix in (".m4a", ".mp4"):
        return "audio/mp4"
    if suffix == ".ogg":
        return "audio/ogg"
    return "application/octet-stream"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET", url, timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True
    ) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    tmp.replace(dest)


def _will_hit_cache(
    audio_path: Path,
    animator: Optional[AvatarAnimator],
) -> bool:
    if animator is None:
        return False
    fp = _fingerprint(
        audio_path=audio_path,
        character_id=animator.character_id,
        model=animator._model,  # noqa: SLF001 — intentional internal peek
    )
    return (animator._cache_dir / f"{fp}.mp4").exists()


def _report_id_from_payload(payload: dict, segments_json_path: Path) -> str:
    report_path = payload.get("report_path")
    if report_path:
        return Path(report_path).stem
    # Fallback: the segments file usually lives in
    # output/commentary/<report_id>/commentary_segments.json or in
    # output/commentary/commentary_segments.json (Stage 1's flat layout).
    parent = segments_json_path.parent.name
    return parent if parent and parent != "commentary" else "default"
