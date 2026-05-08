"""Time-aligned commentary synthesizer.

Reads a Film Analyzer report, generates per-comment TTS via Runway
(Vincent preset for the Friday/Saturday dev build; ElevenLabs cloned
voice for Sunday's recording), and produces a single commentary track
WAV time-aligned to the source cut's duration plus a sidecar JSON of
segment metadata for the player UI.

Cache is fingerprint-keyed per (text, voice_preset, model) so reruns
don't burn TTS credits — see ai_editor.runway_client.

Stage 1 narration is templated from the analyzer's `type` + `suggestion`
fields. Saturday's suggestion_classifier + visual_director will replace
this with multimodal LLM-composed narration; the segments JSON shape is
forward-compatible with that swap.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from pydub import AudioSegment

from ai_editor.runway_client import (
    DEFAULT_TTS_MODEL,
    DEFAULT_VOICE_PRESET,
    RunwayClient,
)

LOG = logging.getLogger(__name__)

ESTIMATED_CHARS_PER_SEC = 14.0  # used only by --dry-run

TYPE_LABELS = {
    "TURNING_POINT": "Turning point.",
    "VULNERABILITY": "Vulnerable beat.",
    "TENSION": "Tension here.",
    "PUNCHLINE": "Punchline.",
}


@dataclass
class CommentSegment:
    comment_id: str
    type: str
    timecode: str
    start_seconds: float
    end_seconds: float
    impact: str
    narration_text: str
    audio_path: Optional[str] = None
    duration_sec: float = 0.0
    is_suggestion: bool = True
    cached: bool = False


@dataclass
class CommentaryBuildResult:
    track_path: Path
    segments_path: Path
    segments: List[CommentSegment]
    total_duration_sec: float


def compose_narration(moment: dict) -> str:
    label = TYPE_LABELS.get((moment.get("type") or "").upper(), "")
    suggestion = (moment.get("suggestion") or "").strip()
    if not suggestion:
        return ""
    if label:
        return f"{label} {suggestion}"
    return suggestion


def extract_moments(report: dict) -> List[dict]:
    moments = (report.get("emotional_analysis") or {}).get("moments") or []
    out: List[dict] = []
    for i, m in enumerate(moments):
        if "start_seconds" not in m:
            continue
        out.append({**m, "_id": f"emotional.{i}"})
    return out


def build_commentary(
    report_path: Path,
    out_dir: Path,
    runway: Optional[RunwayClient] = None,
    voice_preset: str = DEFAULT_VOICE_PRESET,
    model: str = DEFAULT_TTS_MODEL,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> CommentaryBuildResult:
    report_path = Path(report_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(".cache") / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    total_duration = float(report.get("duration") or 0.0)
    if total_duration <= 0:
        raise RuntimeError(
            f"report['duration'] is missing or non-positive in {report_path}"
        )

    moments = extract_moments(report)
    if limit is not None:
        moments = moments[:limit]
    if not moments:
        raise RuntimeError(
            "No moments found at report['emotional_analysis']['moments']"
        )
    LOG.info("Synthesizing %d moments from %s", len(moments), report_path)

    if not dry_run and runway is None:
        runway = RunwayClient()

    segments: List[CommentSegment] = []
    for m in moments:
        narration = compose_narration(m)
        if not narration:
            LOG.info("Skipping moment %s — empty narration", m.get("_id"))
            continue

        audio_path: Optional[str] = None
        cached = False
        if dry_run:
            duration_sec = max(1.5, len(narration) / ESTIMATED_CHARS_PER_SEC)
        else:
            assert runway is not None  # for type-checker
            tts = runway.text_to_speech(
                text=narration,
                cache_dir=cache_dir,
                voice_preset=voice_preset,
                model=model,
            )
            audio_path = str(tts.audio_path)
            cached = tts.cached
            seg = AudioSegment.from_file(tts.audio_path)
            duration_sec = len(seg) / 1000.0

        segments.append(
            CommentSegment(
                comment_id=m["_id"],
                type=str(m.get("type") or ""),
                timecode=str(m.get("timecode") or ""),
                start_seconds=float(m["start_seconds"]),
                end_seconds=float(m.get("end_seconds") or m["start_seconds"]),
                impact=str(m.get("impact") or ""),
                narration_text=narration,
                audio_path=audio_path,
                duration_sec=duration_sec,
                is_suggestion=True,
                cached=cached,
            )
        )

    track_path = out_dir / "commentary.wav"
    segments_path = out_dir / "commentary_segments.json"

    if dry_run:
        track_path.write_bytes(b"")
    else:
        _compose_track(segments, total_duration, track_path)

    segments_path.write_text(
        json.dumps(
            {
                "report_path": str(report_path),
                "track_path": str(track_path),
                "total_duration_sec": total_duration,
                "voice_preset": voice_preset,
                "model": model,
                "dry_run": dry_run,
                "segments": [asdict(s) for s in segments],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return CommentaryBuildResult(
        track_path=track_path,
        segments_path=segments_path,
        segments=segments,
        total_duration_sec=total_duration,
    )


def _compose_track(
    segments: List[CommentSegment],
    total_duration_sec: float,
    out_path: Path,
) -> None:
    track = AudioSegment.silent(
        duration=int(total_duration_sec * 1000), frame_rate=44100
    )
    for s in segments:
        if not s.audio_path:
            continue
        clip = AudioSegment.from_file(s.audio_path)
        position_ms = max(0, int(s.start_seconds * 1000))
        track = track.overlay(clip, position=position_ms)
    track.export(out_path, format="wav")
