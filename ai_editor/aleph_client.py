"""Runway Aleph wrapper — gen4_aleph video-to-video transformation.

Workflow per call:
  1. Hash the source video segment + brief inputs into a fingerprint.
  2. Hit the on-disk cache; if present, return immediately. Aleph credits
     are the most expensive thing this pipeline can spend, so caching is
     a hard requirement, not a nice-to-have.
  3. Upload the source segment via uploads.create_ephemeral and capture
     the runway:// URI.
  4. Call client.video_to_video.create(model='gen4_aleph',
                                        video_uri=<uri>,
                                        prompt_text=<brief>,
                                        ratio=<>, duration=<>).
  5. Poll via task.wait_for_task_output(timeout=...).
  6. Download the resulting MP4 to the cache, return AlephResult.

The wrapper is deliberately thin — Visual Director picks all the
parameters, this module just executes. Caches inputs at .cache/aleph_in/
so a rerun against the same source segment + brief is a no-op.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

LOG = logging.getLogger(__name__)

ALEPH_MODEL = "gen4_aleph"
ALEPH_TIMEOUT_SEC = 1200  # gen4_aleph can take a few minutes
DOWNLOAD_TIMEOUT_SEC = 300.0


@dataclass(frozen=True)
class AlephResult:
    video_path: Path
    video_url: str
    task_id: str
    fingerprint: str
    cached: bool


class AlephClient:
    """Thin wrapper around the Runway video_to_video endpoint pinned to
    gen4_aleph. Construct once, reuse across the pipeline."""

    def __init__(self, api_key: Optional[str] = None, runway_client=None):
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

    @property
    def raw(self):
        return self._client

    def transform(
        self,
        *,
        source_video_path: Path,
        prompt_text: str,
        ratio: str,
        duration_sec: int,
        cache_dir: Path,
        seed: Optional[int] = None,
        upload_mime: str = "video/mp4",
    ) -> AlephResult:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        source_video_path = Path(source_video_path)
        if not source_video_path.exists():
            raise FileNotFoundError(f"Aleph source video missing: {source_video_path}")

        fp = _fingerprint(
            source_video_path=source_video_path,
            prompt_text=prompt_text,
            ratio=ratio,
            duration_sec=duration_sec,
            seed=seed,
        )
        cache_video = cache_dir / f"{fp}.mp4"
        cache_meta = cache_dir / f"{fp}.url"
        if cache_video.exists() and cache_video.stat().st_size > 0:
            video_url = cache_meta.read_text(encoding="utf-8").strip() if cache_meta.exists() else ""
            LOG.info("Aleph cache hit %s", fp)
            return AlephResult(
                video_path=cache_video,
                video_url=video_url,
                task_id="<cached>",
                fingerprint=fp,
                cached=True,
            )

        LOG.info(
            "Aleph upload source %s (%d bytes) for fp=%s",
            source_video_path.name, source_video_path.stat().st_size, fp,
        )
        with source_video_path.open("rb") as fh:
            upload = self._client.uploads.create_ephemeral(
                file=(source_video_path.name, fh, upload_mime),
            )
        video_uri = upload.uri
        LOG.info("Aleph create model=%s ratio=%s dur=%ds chars=%d", ALEPH_MODEL, ratio, duration_sec, len(prompt_text))
        kwargs = {
            "model": ALEPH_MODEL,
            "video_uri": video_uri,
            "prompt_text": prompt_text,
            "ratio": ratio,
            "duration": duration_sec,
        }
        if seed is not None:
            kwargs["seed"] = seed
        task = self._client.video_to_video.create(**kwargs)
        result = task.wait_for_task_output(timeout=ALEPH_TIMEOUT_SEC)
        if not result.output:
            raise RuntimeError(f"Aleph task {task.id} returned empty output")
        video_url = result.output[0]
        _download(video_url, cache_video)
        cache_meta.write_text(video_url, encoding="utf-8")
        LOG.info("Aleph done fp=%s task=%s bytes=%d", fp, task.id, cache_video.stat().st_size)
        return AlephResult(
            video_path=cache_video,
            video_url=video_url,
            task_id=task.id,
            fingerprint=fp,
            cached=False,
        )


def _fingerprint(
    *,
    source_video_path: Path,
    prompt_text: str,
    ratio: str,
    duration_sec: int,
    seed: Optional[int],
) -> str:
    """Hash inputs to a stable per-transformation fingerprint.

    Source video is hashed by content (sha256 of file bytes streamed in
    chunks) so that re-extracted but identical segments hit cache.
    """
    h = hashlib.sha256()
    h.update(_sha256_file(source_video_path).encode("ascii"))
    h.update(b"|")
    h.update(prompt_text.encode("utf-8"))
    h.update(b"|")
    h.update(ratio.encode("utf-8"))
    h.update(b"|")
    h.update(str(duration_sec).encode("utf-8"))
    h.update(b"|")
    h.update(str(seed).encode("utf-8"))
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


def guess_upload_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "video/mp4"
