"""AI Jonny avatar — one-time creation against the Runway Avatars API.

Stage 3 (Sunday) of the hackathon submission. The third modality of the
AI editor: visual identity. Voice (Charlie-v3 Stage 1) + Aleph VFX
(Echo-v3 Stage 2) + Avatar (this) = three modalities, one character.

What this module does:
  1. Picks a reference photograph of Jonny from the project's local
     `data/avatars/jonny_reference_frames/` directory. Default:
     ``jonny_main.jpg`` — copied at session start from the StudioOS
     training-photos library (per session handoff). Only one image is
     fed to ``avatars.create`` because that endpoint takes a single
     ``reference_image`` URL; additional frames in the directory are
     candidates a future revision can audition between.
  2. Uploads the chosen frame via ``uploads.create_ephemeral`` so that
     the avatar build has a remote URI (the API expects an HTTPS URL;
     the SDK's ``upload.uri`` is a ``runway://`` reference, which the
     other endpoints in this codebase accept — same path used by
     ``aleph_client``).
  3. Calls ``client.avatars.create`` with the project's identity
     personality + the dev-mode voice preset (``vincent``) so the
     avatar character matches Charlie-v3's Stage-1 TTS. Voice on the
     avatar is functionally a metadata anchor for ``realtime_sessions``;
     ``avatar_videos.create`` overrides it per-call by passing audio
     directly, so the choice doesn't affect Stage-3 commentary.
  4. Polls ``client.avatars.retrieve`` until status flips
     ``PROCESSING -> READY`` (or fails / times out).
  5. Caches the resulting character record at
     ``data/avatars/jonny_character.json``. Subsequent runs reuse the
     cached ``id`` and skip the create step entirely. ``avatar_videos``
     animation is the per-segment hot-path; this module is a one-time
     bootstrap.

A note on terminology: the brief, the blueprint, and the SDK use
"avatar" and "character" interchangeably. The Runway endpoint is
``client.avatars.create``; we surface it here as
``get_or_create_jonny_character`` to match the blueprint's
"AI Jonny character" framing. The cache file likewise.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

LOG = logging.getLogger(__name__)

# --- Defaults ------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_FRAMES_DIR = REPO_ROOT / "data" / "avatars" / "jonny_reference_frames"
DEFAULT_REFERENCE_IMAGE = REFERENCE_FRAMES_DIR / "jonny_main.jpg"
CHARACTER_CACHE_PATH = REPO_ROOT / "data" / "avatars" / "jonny_character.json"

AVATAR_NAME = "Jonny von Wallström — AI editor"

# System prompt anchors the avatar's voice if it's ever wired into
# realtime_sessions. The personality is required by avatars.create even
# though Stage 3 only uses avatar_videos (which doesn't read it).
AVATAR_PERSONALITY = (
    "You are Jonny von Wallström, a documentary filmmaker functioning as "
    "an AI editor. You read documentary cuts and offer editorial critique "
    "in the same voice you'd whisper to another editor sitting next to "
    "you. Plainspoken, intimate, observational. You name what's working "
    "and what's missing without performance. You stay close to the cut "
    "and reference what's actually on screen. You never replace real "
    "subjects in the film — you augment the editorial reading."
)

DEFAULT_VOICE_PRESET = "vincent"  # matches Charlie-v3 dev-mode TTS
AVATAR_PROCESSING_TIMEOUT_SEC = 600
AVATAR_POLL_INTERVAL_SEC = 5.0


# --- Result dataclass ----------------------------------------------------


@dataclass
class JonnyCharacter:
    """Stable record persisted at ``data/avatars/jonny_character.json``."""

    id: str
    name: str
    status: str  # PROCESSING | READY | FAILED
    reference_image_path: str
    reference_image_uri: Optional[str]  # Runway processed preview URI, if any
    voice_preset: str
    created_at: Optional[str]  # iso-8601 from the API, if present
    cached: bool = False


# --- Public API ----------------------------------------------------------


def list_reference_candidates(
    frames_dir: Path = REFERENCE_FRAMES_DIR,
) -> List[Path]:
    """All candidate reference images on disk, sorted by name."""
    frames_dir = Path(frames_dir)
    if not frames_dir.is_dir():
        return []
    return sorted(
        p for p in frames_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def get_or_create_jonny_character(
    runway_client=None,
    *,
    reference_image: Path = DEFAULT_REFERENCE_IMAGE,
    cache_path: Path = CHARACTER_CACHE_PATH,
    voice_preset: str = DEFAULT_VOICE_PRESET,
    name: str = AVATAR_NAME,
    personality: str = AVATAR_PERSONALITY,
    api_key: Optional[str] = None,
    poll_timeout_sec: float = AVATAR_PROCESSING_TIMEOUT_SEC,
) -> JonnyCharacter:
    """Return a READY ``JonnyCharacter``; create it once and reuse forever.

    Cache semantics:
      - If the cache file holds a ``READY`` record, return it (cached=True).
      - If it holds a ``PROCESSING`` record, poll the API for the cached id
        until it flips to ``READY`` (or fails). This handles the case where
        a previous run timed out before the avatar finished baking.
      - Otherwise (missing or ``FAILED``), call ``avatars.create`` fresh.

    The ``runway_client`` parameter is injectable so tests can pass a mock
    without needing the real ``RUNWAYML_API_SECRET``.
    """

    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cached = _load_cache(cache_path)
    if cached and cached.status == "READY":
        LOG.info("Avatar cache hit %s status=%s", cached.id, cached.status)
        return JonnyCharacter(**{**asdict(cached), "cached": True})

    if runway_client is None:
        runway_client = _build_runway_client(api_key)

    if cached and cached.status == "PROCESSING":
        LOG.info(
            "Avatar cache held PROCESSING record %s — polling instead of creating",
            cached.id,
        )
        finalised = _poll_until_ready(
            runway_client, cached.id, poll_timeout_sec=poll_timeout_sec
        )
        record = _record_from_response(
            finalised,
            reference_image_path=cached.reference_image_path,
            voice_preset=cached.voice_preset,
        )
        _save_cache(cache_path, record)
        return JonnyCharacter(**{**asdict(record), "cached": True})

    reference_image = Path(reference_image)
    if not reference_image.exists():
        raise FileNotFoundError(
            f"Avatar reference image missing: {reference_image}. "
            f"Drop a portrait jpg/png into {REFERENCE_FRAMES_DIR}/."
        )

    LOG.info(
        "Avatar create: uploading reference %s (%d bytes)",
        reference_image.name,
        reference_image.stat().st_size,
    )
    reference_uri = _upload_reference_image(runway_client, reference_image)

    voice = {"type": "runway-live-preset", "preset_id": voice_preset}
    LOG.info(
        "Avatar create: name=%r preset=%s personality_chars=%d",
        name, voice_preset, len(personality),
    )
    create_response = runway_client.avatars.create(
        name=name,
        personality=personality,
        reference_image=reference_uri,
        voice=voice,
        image_processing="optimize",
    )

    # Persist a PROCESSING marker so a crash here doesn't strand the
    # avatar — the next run polls instead of recreating.
    interim = _record_from_response(
        create_response,
        reference_image_path=str(reference_image),
        voice_preset=voice_preset,
    )
    _save_cache(cache_path, interim)
    LOG.info("Avatar create returned id=%s status=%s", interim.id, interim.status)

    if interim.status == "READY":
        return interim
    if interim.status == "FAILED":
        raise RuntimeError(
            f"Runway avatars.create returned FAILED for {interim.id}: "
            f"{getattr(create_response, 'failure_reason', '<no reason>')}"
        )

    final_response = _poll_until_ready(
        runway_client, interim.id, poll_timeout_sec=poll_timeout_sec
    )
    final_record = _record_from_response(
        final_response,
        reference_image_path=str(reference_image),
        voice_preset=voice_preset,
    )
    _save_cache(cache_path, final_record)
    return final_record


# --- Internals -----------------------------------------------------------


def _build_runway_client(api_key: Optional[str]):
    load_dotenv()
    key = api_key or os.environ.get("RUNWAYML_API_SECRET")
    if not key:
        raise RuntimeError(
            "RUNWAYML_API_SECRET not set. Add it to .env or export it. "
            "See .env.example."
        )
    from runwayml import RunwayML
    return RunwayML(api_key=key)


def _upload_reference_image(runway_client, image_path: Path) -> str:
    mime = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    with image_path.open("rb") as fh:
        upload = runway_client.uploads.create_ephemeral(
            file=(image_path.name, fh, mime),
        )
    return upload.uri


def _poll_until_ready(runway_client, avatar_id: str, *, poll_timeout_sec: float):
    """Block until status is READY (or FAILED, or we hit the timeout)."""
    start = time.time()
    while True:
        time.sleep(AVATAR_POLL_INTERVAL_SEC)
        resp = runway_client.avatars.retrieve(avatar_id)
        status = getattr(resp, "status", None)
        LOG.info("Avatar %s poll status=%s", avatar_id, status)
        if status == "READY":
            return resp
        if status == "FAILED":
            reason = getattr(resp, "failure_reason", "<no reason>")
            raise RuntimeError(f"Runway avatar {avatar_id} FAILED: {reason}")
        if time.time() - start > poll_timeout_sec:
            raise TimeoutError(
                f"Runway avatar {avatar_id} still {status} after "
                f"{poll_timeout_sec:.0f}s"
            )


def _record_from_response(
    response,
    *,
    reference_image_path: str,
    voice_preset: str,
) -> JonnyCharacter:
    created_at = getattr(response, "created_at", None)
    return JonnyCharacter(
        id=str(response.id),
        name=str(getattr(response, "name", AVATAR_NAME)),
        status=str(getattr(response, "status", "PROCESSING")),
        reference_image_path=reference_image_path,
        reference_image_uri=getattr(response, "reference_image_uri", None),
        voice_preset=voice_preset,
        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else (
            str(created_at) if created_at else None
        ),
        cached=False,
    )


def _load_cache(cache_path: Path) -> Optional[JonnyCharacter]:
    if not cache_path.exists():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        LOG.warning("Avatar cache unreadable at %s: %s", cache_path, e)
        return None
    return JonnyCharacter(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", AVATAR_NAME)),
        status=str(raw.get("status", "")),
        reference_image_path=str(raw.get("reference_image_path", "")),
        reference_image_uri=raw.get("reference_image_uri"),
        voice_preset=str(raw.get("voice_preset", DEFAULT_VOICE_PRESET)),
        created_at=raw.get("created_at"),
        cached=False,
    )


def _save_cache(cache_path: Path, record: JonnyCharacter) -> None:
    payload = asdict(record)
    payload.pop("cached", None)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOG.info("Avatar cache wrote %s status=%s -> %s", record.id, record.status, cache_path)
