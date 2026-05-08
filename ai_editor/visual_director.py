"""Visual Director — multimodal Claude pass that turns a SUGGESTION
into an executable Aleph (gen4_aleph video-to-video) brief.

Inputs per call:
  - the segment dict (comment_id, timecode, narration_text, vfx_category)
  - 1-3 source frames extracted from the cut at and around the timecode
  - the channel's brand identity dict (CRAFT_PLACEHOLDER by default)

Output: an AlephBrief — model is locked to gen4_aleph because that's the
Saturday Stage-2 thesis (transformation, not generation). The director
chooses prompt_text and the small Aleph parameter surface (ratio,
duration). It does NOT pick the endpoint — that's pre-locked.

Cached on disk by sha256(comment_id|narration|frame paths|brand|model).
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

ALEPH_MODEL = "gen4_aleph"
ALEPH_DEFAULT_RATIO = "1280:720"
ALEPH_DEFAULT_DURATION_SEC = 5
ALEPH_VALID_RATIOS = (
    "1280:720", "720:1280", "1104:832", "960:960",
    "832:1104", "1584:672", "848:480", "640:480",
)
ALEPH_VALID_DURATIONS = (5, 10)

CRAFT_PLACEHOLDER = {
    "channel": "craft",
    "tone": "intimate, observational",
    "lighting": "warm natural light, golden hour preferred",
    "color_palette": ["warm earth tones", "soft greens", "amber"],
    "lens_aesthetic": "anamorphic compression, shallow DoF",
    "movement": "handheld breathing, no gimbal stabilization",
    "ai_role": "atmospheric_complement",
    "ai_aesthetic_philosophy": (
        "AI generates suggestive cinematic texture that ENHANCES the real "
        "interview cut. Never replaces the real subject. Hands, feet, gear "
        "close-ups, light/dust/fog/wind, silhouettes, atmospheric environment."
    ),
    "avoid": [
        "identified subjects with visible faces being relit beyond plausibility",
        "full-body figures performing named actions",
        "wide establishing landscapes (use Media Brain instead)",
        "neon/synthetic colors",
        "readable text on any surface",
    ],
}

DIRECTOR_SYSTEM_PROMPT = """You are the Visual Director for an AI documentary editor.

You receive a single SUGGESTION segment from the editor's commentary
plus 1-3 reference frames pulled from the documentary cut at the
suggestion's timecode. Your job is to author an executable transformation
brief for Runway's gen4_aleph (video-to-video) endpoint.

The endpoint is LOCKED. We do NOT generate new footage. We TRANSFORM the
existing footage with a VFX-class enhancement layer (atmospheric mist,
volumetric light, particles, color/grade shift, lens treatment, or
selective lighting/darkening).

The product thesis: same actual person, same frame, same timing — only
the atmosphere is added by AI. Verifiable not-deepfake. Brand-honest.

PROMPT_TEXT RULES (you author this for Aleph):
  - <=220 characters.
  - Describe the TRANSFORMATION, not a new scene. Treat the source as
    given.
  - Lead with the VFX category: "Add drifting morning mist...", "Apply
    soft golden-hour bloom...", "Subtle anamorphic shallow-DoF
    compression...".
  - Specify intensity ("low intensity", "subtle", "barely visible") to
    keep the result believable. Heavy = AI-tells.
  - Append the avoid clause: " no figure overlay, no readable text, no
    color shift on faces, preserve subject's lighting".
  - Never name a brand product or read-able logos. Keep silhouettes
    abstract.

OUTPUT FORMAT - one JSON object, no preamble, no markdown fence:

{
  "model": "gen4_aleph",
  "prompt_text": "...",
  "ratio": "1280:720" | "720:1280" | "1104:832" | "960:960"
           | "832:1104" | "1584:672" | "848:480" | "640:480",
  "duration_sec": 5 | 10,
  "vfx_category": "atmospheric" | "volumetric_light" | "particle"
                  | "color_grade" | "lens" | "selective_enhance" | "other",
  "story_reasoning": "1-2 sentences explaining WHY this VFX layer
                       serves the editorial beat. Reference what's in
                       the source frames.",
  "fallback_strategy": "string - what to do if Aleph result is muddy.
                         e.g. 'reduce intensity word', 'fall through to
                         color_grade only'.",
  "confidence": 0.0-1.0
}
"""


@dataclass
class AlephBrief:
    comment_id: str
    model: str
    prompt_text: str
    ratio: str
    duration_sec: int
    vfx_category: str
    story_reasoning: str
    fallback_strategy: str
    confidence: float
    frames_used: List[str] = field(default_factory=list)
    cached: bool = False


def find_frames_at_timecode(
    frames_dir: Path,
    start_seconds: float,
    *,
    window: int = 1,
) -> List[Path]:
    """Return up to (2*window+1) frame paths nearest to start_seconds.

    Frames are expected to be named like `frame_NN_TTTs.jpg` (the pattern
    StudioOS's `clip_frame_extractor` and the analysis_review folder
    use). Returns empty list if frames_dir is missing or empty.
    """
    frames_dir = Path(frames_dir)
    if not frames_dir.exists():
        return []
    parsed: List[tuple[int, Path]] = []
    pat = re.compile(r"_(\d+)s\.(?:jpg|jpeg|png)$", re.IGNORECASE)
    for p in sorted(frames_dir.iterdir()):
        if not p.is_file():
            continue
        m = pat.search(p.name)
        if not m:
            continue
        parsed.append((int(m.group(1)), p))
    if not parsed:
        return []
    parsed.sort(key=lambda t: abs(t[0] - int(start_seconds)))
    nearest = parsed[: 2 * window + 1]
    nearest.sort(key=lambda t: t[0])
    return [p for _, p in nearest]


def generate_brief(
    segment: dict,
    *,
    frames: Sequence[Path],
    brand: Optional[dict] = None,
    cache_dir: Path,
    anthropic_client=None,
    model: str = DEFAULT_DIRECTOR_MODEL,
    dry_run: bool = False,
) -> AlephBrief:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    brand = brand or CRAFT_PLACEHOLDER

    comment_id = str(segment.get("comment_id") or segment.get("_id") or "")
    narration = str(segment.get("narration_text") or "").strip()
    vfx_hint = str(segment.get("vfx_category") or "")

    fp = _fingerprint(
        comment_id=comment_id,
        narration=narration,
        frames=frames,
        brand=brand,
        model=model,
    )
    cache_path = cache_dir / f"{fp}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return _payload_to_brief(comment_id, payload, frames, cached=True)

    if dry_run:
        payload = _stub_brief(
            narration=narration, vfx_hint=vfx_hint, brand=brand
        )
    else:
        if anthropic_client is None:
            import anthropic
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "ANTHROPIC_API_KEY not set. Required for visual_director "
                    "unless dry_run=True."
                )
            anthropic_client = anthropic.Anthropic()
        payload = _call_director(
            anthropic_client,
            model=model,
            segment=segment,
            frames=frames,
            brand=brand,
        )

    payload = _normalize_payload(payload)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _payload_to_brief(comment_id, payload, frames, cached=False)


def _payload_to_brief(
    comment_id: str, payload: dict, frames: Sequence[Path], *, cached: bool
) -> AlephBrief:
    return AlephBrief(
        comment_id=comment_id,
        model=str(payload.get("model") or ALEPH_MODEL),
        prompt_text=str(payload["prompt_text"]),
        ratio=str(payload["ratio"]),
        duration_sec=int(payload["duration_sec"]),
        vfx_category=str(payload.get("vfx_category") or "other"),
        story_reasoning=str(payload.get("story_reasoning") or ""),
        fallback_strategy=str(payload.get("fallback_strategy") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        frames_used=[str(p) for p in frames],
        cached=cached,
    )


def _call_director(
    client,
    *,
    model: str,
    segment: dict,
    frames: Sequence[Path],
    brand: dict,
) -> dict:
    image_blocks = []
    for fp in frames[:3]:
        b64, mime = _read_b64(Path(fp))
        image_blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            }
        )

    summary = (
        f"COMMENT_ID: {segment.get('comment_id') or segment.get('_id')}\n"
        f"TIMECODE: {segment.get('timecode')}  start_seconds: {segment.get('start_seconds')}\n"
        f"VFX_HINT_FROM_CLASSIFIER: {segment.get('vfx_category')}\n"
        f"NARRATION: {segment.get('narration_text')}\n"
        f"IMPACT: {segment.get('impact')}\n"
    )
    content = [{"type": "text", "text": summary}]
    content.extend(image_blocks)
    content.append({"type": "text", "text": f"BRAND IDENTITY:\n{json.dumps(brand, indent=2)}"})

    response = client.messages.create(
        model=model,
        max_tokens=1200,
        system=DIRECTOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return _parse_first_json_object(text)


def _stub_brief(*, narration: str, vfx_hint: str, brand: dict) -> dict:
    """Deterministic dry-run brief. Useful for tests and pipeline plumbing.

    Mirrors the live director's output schema 1:1.
    """
    cat = vfx_hint or "atmospheric"
    base = {
        "atmospheric": "Add drifting morning mist, low intensity, soft golden-hour kiss",
        "volumetric_light": "Apply soft volumetric light shafts, subtle, warm",
        "particle": "Add gentle drifting dust particles, low density",
        "color_grade": "Apply warm dusk color grade, +0.5 contrast, amber lift",
        "lens": "Subtle anamorphic compression, shallow DoF, warm vignette",
        "selective_enhance": "Subtle backlight on subject, slight darken on background",
        "other": "Subtle atmospheric haze",
    }.get(cat, "Subtle atmospheric haze")
    prompt = (
        f"{base}. Preserve subject's natural lighting, no figure overlay, "
        "no readable text, no color shift on faces."
    )[:220]
    return {
        "model": ALEPH_MODEL,
        "prompt_text": prompt,
        "ratio": ALEPH_DEFAULT_RATIO,
        "duration_sec": ALEPH_DEFAULT_DURATION_SEC,
        "vfx_category": cat,
        "story_reasoning": (
            "Stub director: matched the classifier's vfx_category; the "
            "transformation should land the editorial beat without altering "
            "the subject."
        ),
        "fallback_strategy": "Reduce intensity wording; rerun.",
        "confidence": 0.5,
    }


def _normalize_payload(payload: dict) -> dict:
    payload = dict(payload)
    payload["model"] = ALEPH_MODEL  # locked
    ratio = str(payload.get("ratio") or ALEPH_DEFAULT_RATIO)
    if ratio not in ALEPH_VALID_RATIOS:
        LOG.warning("Director returned invalid ratio %r — coercing to %s", ratio, ALEPH_DEFAULT_RATIO)
        ratio = ALEPH_DEFAULT_RATIO
    payload["ratio"] = ratio
    duration = int(payload.get("duration_sec") or ALEPH_DEFAULT_DURATION_SEC)
    if duration not in ALEPH_VALID_DURATIONS:
        LOG.warning("Director returned invalid duration %d — coercing to %d", duration, ALEPH_DEFAULT_DURATION_SEC)
        duration = ALEPH_DEFAULT_DURATION_SEC
    payload["duration_sec"] = duration
    prompt = str(payload.get("prompt_text") or "").strip()
    if len(prompt) > 220:
        prompt = prompt[:217].rstrip() + "..."
    payload["prompt_text"] = prompt
    return payload


def _read_b64(path: Path) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/jpeg"
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), mime


def _fingerprint(
    *,
    comment_id: str,
    narration: str,
    frames: Sequence[Path],
    brand: dict,
    model: str,
) -> str:
    h = hashlib.sha256()
    h.update(comment_id.encode("utf-8"))
    h.update(b"|")
    h.update(narration.encode("utf-8"))
    h.update(b"|")
    for f in frames:
        h.update(b"|f|")
        h.update(str(f).encode("utf-8"))
    h.update(b"|brand|")
    h.update(json.dumps(brand, sort_keys=True).encode("utf-8"))
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


def brief_to_json(brief: AlephBrief) -> dict:
    return asdict(brief)
