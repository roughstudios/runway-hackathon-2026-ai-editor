"""Thin pinned wrapper over the Runway ML SDK.

Centralises:
  - dotenv loading + RUNWAYML_API_SECRET resolution
  - default model + voice preset for documentary commentary
  - fingerprinted on-disk cache for TTS audio (Runway output URLs expire
    in 24-48h, and we don't want reruns to burn credits)
  - polite poll-and-fetch wrapper around text_to_speech.create

This is intentionally minimal — Stage 1 only needs TTS. Stage 2 (Saturday)
will add image_to_video / text_to_video / character_performance helpers
on top of the same client.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from runwayml import RunwayML

LOG = logging.getLogger(__name__)

DEFAULT_TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_PRESET = "Vincent"
TTS_TIMEOUT_SEC = 600
DOWNLOAD_TIMEOUT_SEC = 120.0


@dataclass(frozen=True)
class TTSResult:
    audio_path: Path
    audio_url: str
    task_id: str
    cached: bool


class RunwayClient:
    """Construct once, reuse. Loads RUNWAYML_API_SECRET via dotenv if needed."""

    def __init__(self, api_key: Optional[str] = None):
        load_dotenv()
        self._api_key = api_key or os.environ.get("RUNWAYML_API_SECRET")
        if not self._api_key:
            raise RuntimeError(
                "RUNWAYML_API_SECRET not set. Add it to .env or export it. "
                "See .env.example."
            )
        self._client = RunwayML(api_key=self._api_key)

    @property
    def raw(self) -> RunwayML:
        return self._client

    def text_to_speech(
        self,
        text: str,
        cache_dir: Path,
        voice_preset: str = DEFAULT_VOICE_PRESET,
        model: str = DEFAULT_TTS_MODEL,
    ) -> TTSResult:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        fp = _fingerprint(text=text, voice_preset=voice_preset, model=model)
        cache_audio = cache_dir / f"{fp}.mp3"
        cache_meta = cache_dir / f"{fp}.url"
        if cache_audio.exists() and cache_audio.stat().st_size > 0:
            audio_url = cache_meta.read_text(encoding="utf-8").strip() if cache_meta.exists() else ""
            LOG.info("TTS cache hit %s", fp)
            return TTSResult(
                audio_path=cache_audio,
                audio_url=audio_url,
                task_id="<cached>",
                cached=True,
            )

        voice = {"type": "runway-preset", "preset_id": voice_preset}
        LOG.info("TTS create fp=%s preset=%s chars=%d", fp, voice_preset, len(text))
        task = self._client.text_to_speech.create(
            model=model,
            prompt_text=text,
            voice=voice,
        )
        result = task.wait_for_task_output(timeout=TTS_TIMEOUT_SEC)
        if not result.output:
            raise RuntimeError(f"Runway TTS task {task.id} returned empty output")
        audio_url = result.output[0]
        _download(audio_url, cache_audio)
        cache_meta.write_text(audio_url, encoding="utf-8")
        LOG.info("TTS done fp=%s task=%s bytes=%d", fp, task.id, cache_audio.stat().st_size)
        return TTSResult(
            audio_path=cache_audio,
            audio_url=audio_url,
            task_id=task.id,
            cached=False,
        )


def _fingerprint(*, text: str, voice_preset: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update(b"|")
    h.update(voice_preset.encode("utf-8"))
    h.update(b"|")
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


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
