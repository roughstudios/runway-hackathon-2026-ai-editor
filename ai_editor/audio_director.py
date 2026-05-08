"""Audio Director — multimodal Claude pass that turns ONE analyzer scene
into an executable Runway sound_effect brief (atmosphere + hard FX).

Mirrors :mod:`ai_editor.visual_director` but for the audio track. Inputs
per call:

  - the scene dict (number, timecode_in/out, name, description,
    story_purpose, story_beat, act, plus computed start_/end_seconds)
  - the documentary's full transcript (as a single string — current
    analyzer reports don't carry per-scene transcript slices)
  - 1-3 reference frames pulled from the cut at the scene's midpoint

Output: an :class:`AudioBrief` — at most one atmosphere brief (loops
under the scene) and zero or more hard-FX briefs (transient sounds at
specific in-scene offsets). The director defaults to RESTRAINT — many
documentary scenes are fully diegetic and only want either no
atmosphere, or only atmosphere with no hard FX.

Cached on disk by sha256(scene_id|name|description|story_purpose|
timecodes|frame_paths|transcript_excerpt|model). Re-runs against the
same report don't re-bill Anthropic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

LOG = logging.getLogger(__name__)

DEFAULT_DIRECTOR_MODEL = "claude-sonnet-4-5"

# Hard FX duration window the Runway sound_effect endpoint accepts.
SFX_MIN_DURATION_SEC = 0.5
SFX_MAX_DURATION_SEC = 30.0
DEFAULT_FX_DURATION_SEC = 2.0
MAX_PROMPT_CHARS = 220

DIRECTOR_SYSTEM_PROMPT = """You are the Audio Director for an AI documentary editor.

You receive ONE scene from a documentary cut and decide what audio
sweetening — if any — should be layered on top of the existing dialogue
and Foley. The Runway sound_effect endpoint
(eleven_text_to_sound_v2) generates each brief you author.

You have two channels available:

  ATMOSPHERE
    - At most ONE per scene. Loops under the entire scene.
    - LOW-ENERGY, NON-TRANSIENT: ambient bed, room tone, weather, distant
      hum, environmental wash.
    - GOOD: "low forest ambience at dawn, distant water, gentle wind"
            "warm room tone, refrigerator hum, distant traffic"
            "high-altitude wind bed, sparse, no gusts"
    - BAD:  "footsteps and a door slam"  (those are transients)
            "30 seconds of crowd cheers"  (too active)

  HARD FX
    - Zero or more per scene. Each is a single transient sound landing
      at a specific moment.
    - duration: 0.5 to 30 seconds. Typical 0.5-3 seconds.
    - GOOD: "soft footstep on gravel"
            "metallic gate latch click"
            "distant thunder rumble, low"
            "single match strike, close mic"
    - BAD:  "ambient cafe sounds"  (that's atmosphere)
            "10 minutes of forest sounds"  (over the cap, also ambient)

DEFAULT TO RESTRAINT:
  - Dialogue-heavy scenes with full location audio rarely need
    atmosphere. needs_atmosphere=false is a perfectly good answer.
  - Hard FX should only appear when the scene clearly calls for a
    transient that the captured audio is missing — a single emphatic
    moment, not a montage of busywork.
  - When unsure, prefer fewer briefs. Spurious SFX burns Runway credits
    AND clutters the mix.

PROMPT_TEXT RULES (you author this for sound_effect):
  - <=220 characters.
  - Describe the sound itself, not the visual or emotional intent.
  - Prefer concrete acoustic descriptors: "low", "distant", "soft",
    "muffled", "close mic", "with reverb", "no music".
  - NEVER include music ("piano sting", "cinematic drone") — this
    pipeline is sound design only; music is a separate layer.

OUTPUT FORMAT - one JSON object, no preamble, no markdown fence:

{
  "needs_atmosphere": true | false,
  "atmosphere_brief": null | {
    "prompt": "<=220 chars, ambient bed only, no transients",
    "reasoning": "1 sentence — why this ambience serves the scene"
  },
  "hard_fx_briefs": [
    {
      "start_offset_in_scene": <float, >=0, <= scene_duration>,
      "duration": <float, 0.5..30.0>,
      "prompt": "<=220 chars, transient sound description",
      "reasoning": "1 sentence — why this beat needs this FX"
    }
  ],
  "confidence": 0.0..1.0
}

If needs_atmosphere is false, atmosphere_brief MUST be null.
hard_fx_briefs may be empty [].
"""


@dataclass
class HardFxBrief:
    start_offset_in_scene: float
    duration: float
    prompt: str
    reasoning: str = ""


@dataclass
class AtmosphereBrief:
    prompt: str
    reasoning: str = ""


@dataclass
class AudioBrief:
    scene_id: str
    needs_atmosphere: bool
    atmosphere_brief: Optional[AtmosphereBrief]
    hard_fx_briefs: List[HardFxBrief]
    confidence: float
    frames_used: List[str] = field(default_factory=list)
    cached: bool = False


# --- Public entry points -------------------------------------------------


def generate_brief(
    scene: dict,
    *,
    transcript: str,
    frames: Sequence[Path],
    cache_dir: Path,
    anthropic_client=None,
    model: str = DEFAULT_DIRECTOR_MODEL,
    dry_run: bool = False,
) -> AudioBrief:
    """Run the Audio Director once for one scene.

    The ``scene`` dict shape mirrors what the Film Analyzer report
    carries under ``report['scenes']`` — number, timecode_in/out, name,
    description, story_purpose, story_beat, act — plus the computed
    ``scene_id``, ``start_seconds`` and ``end_seconds`` keys the
    pipeline adds before calling.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    scene_id = str(scene.get("scene_id") or _scene_id_for(scene))
    fp = _fingerprint(
        scene=scene,
        frames=frames,
        transcript=transcript,
        model=model,
    )
    cache_path = cache_dir / f"{fp}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return _payload_to_brief(scene_id, payload, frames, cached=True)

    if dry_run:
        payload = _stub_brief(scene=scene)
    else:
        if anthropic_client is None:
            import anthropic
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set. Required for audio_director "
                    "unless dry_run=True."
                )
            anthropic_client = anthropic.Anthropic()
        payload = _call_director(
            anthropic_client,
            model=model,
            scene=scene,
            transcript=transcript,
            frames=frames,
        )

    payload = _normalize_payload(payload, scene=scene)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _payload_to_brief(scene_id, payload, frames, cached=False)


def find_frames_at_timecode(
    frames_dir: Path,
    start_seconds: float,
    *,
    window: int = 1,
) -> List[Path]:
    """Re-export of :func:`ai_editor.visual_director.find_frames_at_timecode`.

    Pipelines should import from this module to avoid cross-stage
    dependencies, even though the implementation is shared.
    """
    from ai_editor.visual_director import find_frames_at_timecode as _impl
    return _impl(frames_dir, start_seconds, window=window)


def brief_to_json(brief: AudioBrief) -> dict:
    return {
        "scene_id": brief.scene_id,
        "needs_atmosphere": brief.needs_atmosphere,
        "atmosphere_brief": (
            asdict(brief.atmosphere_brief) if brief.atmosphere_brief else None
        ),
        "hard_fx_briefs": [asdict(b) for b in brief.hard_fx_briefs],
        "confidence": brief.confidence,
        "frames_used": list(brief.frames_used),
        "cached": brief.cached,
    }


# --- Internals -----------------------------------------------------------


def _scene_id_for(scene: dict) -> str:
    """``scene_NN`` zero-padded by 0-indexed position (manifest contract)."""
    n = scene.get("index")
    if n is None:
        # Fall back to the analyzer's 1-indexed ``number`` minus one.
        raw = scene.get("number")
        n = (int(raw) - 1) if raw is not None else 0
    return f"scene_{int(n):02d}"


def _payload_to_brief(
    scene_id: str, payload: dict, frames: Sequence[Path], *, cached: bool
) -> AudioBrief:
    needs_atm = bool(payload.get("needs_atmosphere"))
    atm = payload.get("atmosphere_brief")
    atm_obj: Optional[AtmosphereBrief] = None
    if needs_atm and isinstance(atm, dict):
        atm_obj = AtmosphereBrief(
            prompt=str(atm.get("prompt") or "").strip(),
            reasoning=str(atm.get("reasoning") or ""),
        )
    hf_list: List[HardFxBrief] = []
    for raw in payload.get("hard_fx_briefs") or []:
        if not isinstance(raw, dict):
            continue
        hf_list.append(
            HardFxBrief(
                start_offset_in_scene=float(raw.get("start_offset_in_scene") or 0.0),
                duration=float(raw.get("duration") or DEFAULT_FX_DURATION_SEC),
                prompt=str(raw.get("prompt") or "").strip(),
                reasoning=str(raw.get("reasoning") or ""),
            )
        )
    return AudioBrief(
        scene_id=scene_id,
        needs_atmosphere=needs_atm and atm_obj is not None,
        atmosphere_brief=atm_obj if needs_atm else None,
        hard_fx_briefs=hf_list,
        confidence=float(payload.get("confidence") or 0.0),
        frames_used=[str(p) for p in frames],
        cached=cached,
    )


def _call_director(
    client,
    *,
    model: str,
    scene: dict,
    transcript: str,
    frames: Sequence[Path],
) -> dict:
    image_blocks = []
    for fp in list(frames)[:3]:
        b64, mime = _read_b64(Path(fp))
        image_blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            }
        )

    scene_summary = (
        f"SCENE_ID: {scene.get('scene_id') or _scene_id_for(scene)}\n"
        f"NUMBER: {scene.get('number')}\n"
        f"NAME: {scene.get('name') or ''}\n"
        f"TIMECODE: {scene.get('timecode_in')} -> {scene.get('timecode_out')}\n"
        f"START_SECONDS: {scene.get('start_seconds')}\n"
        f"END_SECONDS: {scene.get('end_seconds')}\n"
        f"SCENE_DURATION_SECONDS: {_scene_duration(scene):.2f}\n"
        f"STORY_BEAT: {scene.get('story_beat') or ''}\n"
        f"ACT: {scene.get('act') or ''}\n"
        f"DESCRIPTION: {scene.get('description') or ''}\n"
        f"STORY_PURPOSE: {scene.get('story_purpose') or ''}\n"
    )
    transcript_excerpt = _excerpt_transcript(transcript, scene)

    content = [{"type": "text", "text": scene_summary}]
    content.extend(image_blocks)
    content.append({"type": "text", "text": "TRANSCRIPT EXCERPT (full track; no per-scene timing):\n" + transcript_excerpt})

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=DIRECTOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return _parse_first_json_object(text)


def _stub_brief(*, scene: dict) -> dict:
    """Deterministic dry-run brief.

    Heuristic: pick atmosphere when the scene's name/description/purpose
    contains an ambient cue (forest, race, mountain, dawn, indoor,
    night, room). Pick a single hard FX when an action cue is present
    (door, footstep, water, applause). Otherwise return an empty brief
    (needs_atmosphere=false, hard_fx_briefs=[]).
    """
    text = " ".join(
        str(scene.get(k) or "")
        for k in ("name", "description", "story_purpose", "story_beat")
    ).lower()
    ambient_cues = {
        "forest": "low forest ambience, sparse leaves, distant water",
        "mountain": "high-altitude wind bed, sparse, no gusts",
        "race": "distant ambient crowd murmur, low energy, outdoor air",
        "morning": "low dawn ambience, distant birds, gentle wind",
        "dawn": "low dawn ambience, distant birds, gentle wind",
        "evening": "low dusk ambience, soft cricket bed, warm air",
        "night": "low night ambience, distant insects, faint wind",
        "kitchen": "warm room tone, refrigerator hum, distant chatter",
        "indoor": "warm room tone, soft hum, sparse",
        "outdoor": "open-air ambience, soft wind, no traffic",
        "vineyard": "warm rural ambience, distant cicadas, soft breeze",
        "luxurious": "warm interior tone, soft hum, sparse",
        "pre-race": "outdoor air, distant gear shuffle, low energy",
    }
    matched_atm = next(
        (v for k, v in ambient_cues.items() if k in text),
        None,
    )
    fx_cues = [
        ("footstep", 1.0, "soft footstep on gravel"),
        ("door", 0.5, "soft wooden door close"),
        ("knock", 0.5, "single firm knock on wood"),
        ("car", 2.0, "single car door close, distant"),
        ("water", 1.5, "single water splash, close mic"),
        ("applause", 2.0, "brief distant applause, fading"),
    ]
    matched_fx = []
    scene_dur = max(_scene_duration(scene), 1.0)
    for cue, dur, prompt in fx_cues:
        if cue in text:
            matched_fx.append({
                "start_offset_in_scene": min(scene_dur * 0.4, max(0.0, scene_dur - dur - 0.5)),
                "duration": dur,
                "prompt": prompt,
                "reasoning": f"Stub heuristic matched cue '{cue}'.",
            })
            break  # one stub FX per scene is enough for plumbing

    if matched_atm is None and not matched_fx:
        return {
            "needs_atmosphere": False,
            "atmosphere_brief": None,
            "hard_fx_briefs": [],
            "confidence": 0.4,
        }

    payload: dict = {
        "needs_atmosphere": matched_atm is not None,
        "atmosphere_brief": (
            {
                "prompt": matched_atm,
                "reasoning": "Stub heuristic matched ambient cue in scene metadata.",
            }
            if matched_atm else None
        ),
        "hard_fx_briefs": matched_fx,
        "confidence": 0.5,
    }
    return payload


def _normalize_payload(payload: dict, *, scene: dict) -> dict:
    payload = dict(payload)
    needs = bool(payload.get("needs_atmosphere"))
    atm = payload.get("atmosphere_brief")
    if needs and isinstance(atm, dict):
        atm = dict(atm)
        atm["prompt"] = _trim_prompt(str(atm.get("prompt") or ""))
        atm["reasoning"] = str(atm.get("reasoning") or "")
        if not atm["prompt"]:
            needs = False
            atm = None
    else:
        atm = None
        needs = False
    payload["needs_atmosphere"] = needs
    payload["atmosphere_brief"] = atm

    scene_dur = max(_scene_duration(scene), 0.0)
    fx_clean: List[dict] = []
    for raw in payload.get("hard_fx_briefs") or []:
        if not isinstance(raw, dict):
            continue
        prompt = _trim_prompt(str(raw.get("prompt") or ""))
        if not prompt:
            continue
        try:
            offset = float(raw.get("start_offset_in_scene") or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        offset = max(0.0, offset)
        if scene_dur > 0:
            offset = min(offset, max(0.0, scene_dur - 0.1))
        try:
            duration = float(raw.get("duration") or DEFAULT_FX_DURATION_SEC)
        except (TypeError, ValueError):
            duration = DEFAULT_FX_DURATION_SEC
        duration = max(SFX_MIN_DURATION_SEC, min(SFX_MAX_DURATION_SEC, duration))
        fx_clean.append({
            "start_offset_in_scene": round(offset, 3),
            "duration": round(duration, 3),
            "prompt": prompt,
            "reasoning": str(raw.get("reasoning") or ""),
        })
    payload["hard_fx_briefs"] = fx_clean
    try:
        payload["confidence"] = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        payload["confidence"] = 0.0
    return payload


def _trim_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[: MAX_PROMPT_CHARS - 3].rstrip() + "..."
    return prompt


def _scene_duration(scene: dict) -> float:
    """Best-effort scene duration in seconds.

    Prefer ``end_seconds - start_seconds`` if both are present; fall
    back to parsing ``timecode_in/out`` HH:MM:SS strings; default 0.0.
    """
    s = scene.get("start_seconds")
    e = scene.get("end_seconds")
    try:
        if s is not None and e is not None:
            return max(0.0, float(e) - float(s))
    except (TypeError, ValueError):
        pass
    a = _parse_timecode(scene.get("timecode_in") or "")
    b = _parse_timecode(scene.get("timecode_out") or "")
    if a is not None and b is not None:
        return max(0.0, b - a)
    return 0.0


def _parse_timecode(tc: str) -> Optional[float]:
    if not tc:
        return None
    parts = tc.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except ValueError:
        return None
    return None


def _excerpt_transcript(transcript: str, scene: dict, max_chars: int = 6000) -> str:
    """Trim the transcript so we don't burn tokens.

    Per-scene transcript slices aren't available in the current report
    format, so we approximate: keep up to ``max_chars`` centered on the
    scene's relative position in the cut. If the transcript is short
    enough already, return it unchanged.
    """
    if not transcript:
        return ""
    transcript = transcript.strip()
    if len(transcript) <= max_chars:
        return transcript
    s = scene.get("start_seconds")
    e = scene.get("end_seconds")
    total = scene.get("_total_duration_seconds")  # populated by the pipeline
    if not (s is not None and e is not None and total):
        return transcript[:max_chars]
    try:
        midpoint_frac = (float(s) + float(e)) / 2.0 / float(total)
    except (TypeError, ValueError, ZeroDivisionError):
        return transcript[:max_chars]
    midpoint_frac = max(0.0, min(1.0, midpoint_frac))
    centre = int(len(transcript) * midpoint_frac)
    half = max_chars // 2
    start = max(0, centre - half)
    end = min(len(transcript), start + max_chars)
    start = max(0, end - max_chars)
    return transcript[start:end]


def _read_b64(path: Path) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/jpeg"
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), mime


def _fingerprint(
    *,
    scene: dict,
    frames: Sequence[Path],
    transcript: str,
    model: str,
) -> str:
    h = hashlib.sha256()
    h.update(str(scene.get("scene_id") or _scene_id_for(scene)).encode("utf-8"))
    for k in ("number", "name", "description", "story_purpose",
              "story_beat", "act", "timecode_in", "timecode_out",
              "start_seconds", "end_seconds"):
        h.update(b"|")
        h.update(str(scene.get(k) or "").encode("utf-8"))
    for f in frames:
        h.update(b"|f|")
        h.update(str(f).encode("utf-8"))
    h.update(b"|tx|")
    # Don't fingerprint the entire transcript — it's the whole movie and
    # would invalidate the cache on any tiny edit. The scene metadata
    # already pins the inputs we care about. Hash a short prefix as
    # tiebreaker only.
    h.update((transcript or "")[:512].encode("utf-8"))
    h.update(b"|model|")
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


_FIRST_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_first_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _FIRST_OBJ_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))
