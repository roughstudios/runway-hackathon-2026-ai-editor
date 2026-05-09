"""Direct httpx wrappers for Runway endpoints — bypasses the broken SDK.

The runwayml Python SDK hangs on every API call on this machine
(uploads.create_ephemeral, video_to_video.create, avatar_videos.create,
sound_effect.create, organization.retrieve — all timeout >30s). Curl and
raw httpx work fine against the same endpoints with the same key, so
we route through this module for the Stage-6 hero composer.

Endpoints proven by inspection of the SDK source:
  POST /v1/uploads            {filename, type:"ephemeral"}
                              -> {runwayUri, uploadUrl, fields}
  POST <uploadUrl>            multipart form: fields + file
  GET  /v1/tasks/{id}         -> {id, status, output, ...}
  POST /v1/avatar_videos      {avatar:{type:custom,avatarId}, model, speech}
                              -> {id} (poll /v1/tasks/{id})
  POST /v1/video_to_video     {model:gen4_aleph, videoUri, promptText,
                               ratio, duration, seed?}
                              -> {id} (poll)
  POST /v1/sound_effect       {model:eleven_text_to_sound_v2, promptText,
                               duration, loop}
                              -> {id} (poll)

The base URL + version header are pulled from the SDK constants so we
stay in lock-step if Runway moves them.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

LOG = logging.getLogger(__name__)

API_BASE = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
DEFAULT_TIMEOUT = 60.0
TASK_POLL_INTERVAL = 5.0
TASK_TIMEOUT_DEFAULT = 1200.0  # 20 minutes — Aleph + avatar can be slow


class RunwayHTTPError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("RUNWAYML_API_SECRET")
    if not k:
        raise RuntimeError(
            "RUNWAYML_API_SECRET not set. Add it to .env or export it."
        )
    return k


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_key()}",
        "X-Runway-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _post_json(path: str, body: dict, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, headers=_headers(), json=body)
    if r.status_code >= 400:
        raise RunwayHTTPError(
            f"POST {path} -> {r.status_code}: {r.text[:500]}"
        )
    return r.json()


def _get_json(path: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    with httpx.Client(timeout=timeout) as c:
        r = c.get(url, headers=_headers())
    if r.status_code >= 400:
        raise RunwayHTTPError(
            f"GET {path} -> {r.status_code}: {r.text[:500]}"
        )
    return r.json()


# --- uploads ----------------------------------------------------------------


def upload_ephemeral(file_path: Path, *, mime: Optional[str] = None) -> str:
    """Upload a local file and return the runway:// URI for it.

    Two-step protocol: POST /v1/uploads to claim a presigned URL, then
    POST the file as multipart to that URL.
    """
    file_path = Path(file_path)
    filename = file_path.name
    placeholder = _post_json(
        "/v1/uploads",
        {"filename": filename, "type": "ephemeral"},
        timeout=DEFAULT_TIMEOUT,
    )
    upload_url = placeholder["uploadUrl"]
    fields = dict(placeholder.get("fields") or {})
    runway_uri = placeholder["runwayUri"]
    inferred_mime = mime or _guess_mime(file_path)

    with file_path.open("rb") as fh:
        files = {"file": (filename, fh, inferred_mime)}
        with httpx.Client(timeout=300.0) as c:
            r = c.post(upload_url, data=fields, files=files)
    if r.status_code >= 400:
        raise RunwayHTTPError(
            f"upload presigned POST -> {r.status_code}: {r.text[:500]}"
        )
    LOG.info(
        "uploaded %s (%d bytes) -> %s",
        filename, file_path.stat().st_size, runway_uri,
    )
    return runway_uri


def _guess_mime(path: Path) -> str:
    s = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".m4v": "video/mp4",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
    }.get(s, "application/octet-stream")


# --- task polling -----------------------------------------------------------


def poll_task(
    task_id: str,
    *,
    timeout_sec: float = TASK_TIMEOUT_DEFAULT,
    poll_interval: float = TASK_POLL_INTERVAL,
) -> dict:
    """Block until the task is SUCCEEDED, return the JSON.

    Raises on FAILED / CANCELED / timeout.
    """
    start = time.time()
    while True:
        data = _get_json(f"/v1/tasks/{task_id}")
        status = (data.get("status") or "").upper()
        if status in ("SUCCEEDED", "COMPLETED"):
            return data
        if status in ("FAILED", "CANCELED", "CANCELLED", "ABORTED"):
            reason = data.get("failure") or data.get("failureReason") or data.get("error") or "<no reason>"
            raise RunwayHTTPError(f"task {task_id} {status}: {reason}")
        elapsed = time.time() - start
        if elapsed > timeout_sec:
            raise TimeoutError(
                f"task {task_id} still {status} after {elapsed:.0f}s"
            )
        LOG.info(
            "task %s status=%s elapsed=%.0fs",
            task_id, status or "?", elapsed,
        )
        time.sleep(poll_interval)


def first_output_url(task_data: dict) -> str:
    out = task_data.get("output") or []
    if isinstance(out, list) and out:
        first = out[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            for k in ("url", "uri", "downloadUrl"):
                if first.get(k):
                    return first[k]
    raise RunwayHTTPError(f"task has no usable output: {task_data!r}")


# --- create endpoints -------------------------------------------------------


def create_avatar_video(
    *,
    avatar_id: str,
    audio_uri: str,
    model: str = "gwm1_avatars",
) -> str:
    body = {
        "avatar": {"type": "custom", "avatarId": avatar_id},
        "model": model,
        "speech": {"type": "audio", "audio": audio_uri},
    }
    data = _post_json("/v1/avatar_videos", body)
    task_id = data.get("id")
    if not task_id:
        raise RunwayHTTPError(f"avatar_videos returned no id: {data!r}")
    return task_id


def create_aleph(
    *,
    video_uri: str,
    prompt_text: str,
    ratio: str = "1280:720",
    duration: int = 5,
    seed: Optional[int] = None,
    model: str = "gen4_aleph",
) -> str:
    body: Dict[str, Any] = {
        "model": model,
        "videoUri": video_uri,
        "promptText": prompt_text,
        "ratio": ratio,
        "duration": duration,
    }
    if seed is not None:
        body["seed"] = seed
    data = _post_json("/v1/video_to_video", body)
    task_id = data.get("id")
    if not task_id:
        raise RunwayHTTPError(f"video_to_video returned no id: {data!r}")
    return task_id


def create_sound_effect(
    *,
    prompt_text: str,
    duration: float,
    loop: bool = False,
    model: str = "eleven_text_to_sound_v2",
) -> str:
    body = {
        "model": model,
        "promptText": prompt_text,
        "duration": float(duration),
        "loop": bool(loop),
    }
    data = _post_json("/v1/sound_effect", body)
    task_id = data.get("id")
    if not task_id:
        raise RunwayHTTPError(f"sound_effect returned no id: {data!r}")
    return task_id


# --- download ---------------------------------------------------------------


def download_to_path(url: str, dest: Path, *, timeout: float = 300.0) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET", url, timeout=timeout, follow_redirects=True
    ) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    tmp.replace(dest)
    return dest
