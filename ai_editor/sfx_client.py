"""Runway SFX wrapper — sound_effect (eleven_text_to_sound_v2).

Workflow per call:
  1. Hash the prompt + duration + loop into a content fingerprint.
  2. Hit the on-disk cache at ``.cache/sfx/<sha256>.mp3``; if present,
     return immediately. SFX credits are cheap individually but add up
     across an 18-min cut, so caching is mandatory.
  3. Call ``client.sound_effect.create(model='eleven_text_to_sound_v2',
                                        prompt_text=<>, duration=<>,
                                        loop=<>)``.
  4. Poll via ``task.wait_for_task_output(timeout=...)``.
  5. Download the resulting audio to the cache, return :class:`SFXResult`.

The wrapper is deliberately thin — Audio Director picks the prompt and
the duration; this module just executes. Two semantic conveniences on
top of one underlying primitive:

  - :func:`SFXClient.generate_atmosphere` — defaults loop=True, clamps
    duration into [0.5, 30] (Hotel-v3 will tile in Resolve).
  - :func:`SFXClient.generate_fx` — defaults loop=False.
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

LOG = logging.getLogger(__name__)

SFX_MODEL = "eleven_text_to_sound_v2"
SFX_TIMEOUT_SEC = 600
DOWNLOAD_TIMEOUT_SEC = 120.0

SFX_MIN_DURATION_SEC = 0.5
SFX_MAX_DURATION_SEC = 30.0

DEFAULT_CACHE_DIR = Path(".cache") / "sfx"


@dataclass(frozen=True)
class SFXResult:
    audio_path: Path
    audio_url: str
    task_id: str
    fingerprint: str
    cached: bool
    duration_requested: float
    loop: bool


class SFXClient:
    """Thin wrapper around ``client.sound_effect.create``.

    Construct once, reuse across the pipeline.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        runway_client=None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        model: str = SFX_MODEL,
    ):
        load_dotenv()
        self._api_key = api_key or os.environ.get("RUNWAYML_API_SECRET")
        if runway_client is None and not self._api_key:
            raise RuntimeError(
                "RUNWAYML_API_SECRET not set. Add it to .env or export it. "
                "See .env.example."
            )
        if runway_client is None:
            from runwayml import RunwayML
            runway_client = RunwayML(api_key=self._api_key)
        self._client = runway_client
        self._model = model
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def raw(self):
        return self._client

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def model(self) -> str:
        return self._model

    def generate_atmosphere(
        self,
        prompt: str,
        duration: float,
        *,
        loop: bool = True,
    ) -> SFXResult:
        """Generate an ambient bed. Defaults loop=True so Hotel-v3 can
        tile a 30-second clip under a longer scene in Resolve.
        """
        return self._create(prompt=prompt, duration=duration, loop=loop)

    def generate_fx(
        self,
        prompt: str,
        duration: float,
        *,
        loop: bool = False,
    ) -> SFXResult:
        """Generate a transient FX clip. Loop is normally False — these
        are point-in-time sounds, not beds.
        """
        return self._create(prompt=prompt, duration=duration, loop=loop)

    # --- low-level shared path -------------------------------------------

    def _create(self, *, prompt: str, duration: float, loop: bool) -> SFXResult:
        if not prompt or not prompt.strip():
            raise ValueError("SFX prompt is empty")
        prompt = prompt.strip()
        duration_req = _clamp_duration(duration)
        fp = _fingerprint(
            prompt=prompt,
            duration=duration_req,
            loop=loop,
            model=self._model,
        )
        cache_audio = self._cache_dir / f"{fp}.mp3"
        cache_meta = self._cache_dir / f"{fp}.url"
        if cache_audio.exists() and cache_audio.stat().st_size > 0:
            audio_url = (
                cache_meta.read_text(encoding="utf-8").strip()
                if cache_meta.exists() else ""
            )
            LOG.info("SFX cache hit %s loop=%s dur=%.2fs", fp, loop, duration_req)
            return SFXResult(
                audio_path=cache_audio,
                audio_url=audio_url,
                task_id="<cached>",
                fingerprint=fp,
                cached=True,
                duration_requested=duration_req,
                loop=loop,
            )

        LOG.info(
            "SFX create model=%s loop=%s dur=%.2fs chars=%d",
            self._model, loop, duration_req, len(prompt),
        )
        task = self._client.sound_effect.create(
            model=self._model,
            prompt_text=prompt,
            duration=duration_req,
            loop=loop,
        )
        result = task.wait_for_task_output(timeout=SFX_TIMEOUT_SEC)
        if not result.output:
            raise RuntimeError(f"SFX task {task.id} returned empty output")
        audio_url = result.output[0]
        _download(audio_url, cache_audio)
        cache_meta.write_text(audio_url, encoding="utf-8")
        LOG.info(
            "SFX done fp=%s task=%s bytes=%d",
            fp, task.id, cache_audio.stat().st_size,
        )
        return SFXResult(
            audio_path=cache_audio,
            audio_url=audio_url,
            task_id=task.id,
            fingerprint=fp,
            cached=False,
            duration_requested=duration_req,
            loop=loop,
        )


# --- module-level convenience wrappers (used by tests + scripts) ---------


def generate_atmosphere(
    prompt: str,
    duration: float,
    *,
    loop: bool = True,
    client: Optional[SFXClient] = None,
) -> SFXResult:
    if client is None:
        client = SFXClient()
    return client.generate_atmosphere(prompt, duration, loop=loop)


def generate_fx(
    prompt: str,
    duration: float,
    *,
    loop: bool = False,
    client: Optional[SFXClient] = None,
) -> SFXResult:
    if client is None:
        client = SFXClient()
    return client.generate_fx(prompt, duration, loop=loop)


# --- internals -----------------------------------------------------------


def _clamp_duration(duration: float) -> float:
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = SFX_MIN_DURATION_SEC
    if d < SFX_MIN_DURATION_SEC:
        d = SFX_MIN_DURATION_SEC
    if d > SFX_MAX_DURATION_SEC:
        d = SFX_MAX_DURATION_SEC
    return round(d, 3)


def _fingerprint(*, prompt: str, duration: float, loop: bool, model: str) -> str:
    """Stable per-asset fingerprint.

    Prompt + duration + loop + model fully determines the SFX call.
    Identical briefs across scenes hash to the same fingerprint, which
    makes deduplication a property of the cache rather than something
    the orchestrator has to manage explicitly.
    """
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(b"|")
    h.update(f"{duration:.3f}".encode("utf-8"))
    h.update(b"|")
    h.update((b"L" if loop else b"-"))
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
