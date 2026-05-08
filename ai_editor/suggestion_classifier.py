"""Suggestion classifier — observation vs suggestion-with-visualization.

Reads the commentary segments JSON produced by
:mod:`ai_editor.commentary_synthesizer` and asks Claude to label each one
as either:

  - OBSERVATION: a remark about what's already on screen. Narrate only.
  - SUGGESTION: an editorial gap or enhancement opportunity. Narrate AND
    visualize via Aleph (gen4_aleph video-to-video).

For each SUGGESTION, the classifier also produces a `vfx_category` —
one of the locked Aleph use-cases (atmospheric, volumetric_light,
particle, color_grade, lens, selective_enhance, other). That category
becomes the entry point into the Visual Director's transformation-brief
prompt.

Cached on disk by sha256(comment_id|narration_text|model). Re-runs of
the same report don't hit Anthropic. No frame inputs at this stage —
classification is a text-only pass to keep the cost ceiling low; the
multimodal step is the Visual Director (see visual_director.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

LOG = logging.getLogger(__name__)

DEFAULT_CLASSIFIER_MODEL = "claude-sonnet-4-5"

VFX_CATEGORIES = (
    "atmospheric",        # mist, fog, dust motes, breath in cold air
    "volumetric_light",   # god rays, lens flares, golden-hour bloom
    "particle",           # rain, snow, ash
    "color_grade",        # time-of-day shift, mood transform
    "lens",               # anamorphic, shallow DoF, vignette
    "selective_enhance",  # backlight subject, darken background
    "other",
)

CLASSIFIER_SYSTEM_PROMPT = """You are the Suggestion Classifier for an AI documentary editor.

You read editorial-critique commentary segments authored from a Film
Analyzer report. For each segment you decide whether it's:

  OBSERVATION  - a remark about what's already on screen (no
                 visualization required; the audio commentary alone
                 carries it).
  SUGGESTION   - an editorial gap or atmospheric enhancement that would
                 land the beat better. The visualization layer is the
                 Aleph (gen4_aleph) video-to-video transformer applied to
                 the existing footage at the timecode.

We do NOT generate new footage to fill gaps. We TRANSFORM the existing
footage with VFX-class enhancements. Therefore SUGGESTIONS must be
expressible as a treatment of the already-present shot.

If you label a segment SUGGESTION, also pick a vfx_category:
  atmospheric        mist, fog, dust motes, breath in cold air
  volumetric_light   god rays, lens flares, golden-hour bloom
  particle           rain, snow, ash
  color_grade        time-of-day shift, mood transform
  lens               anamorphic compression, shallow DoF, vignette
  selective_enhance  backlight subject, darken background
  other              none of the above; visual_director will reason from text

GUIDANCE
- A note about timing or duration alone ("hold an extra beat", "let the
  moment breathe") is OBSERVATION — the audio commentary handles it.
- A note about emotional weight that calls for atmosphere or texture
  ("the cold should feel real", "this needs the weight of dusk") is
  SUGGESTION.
- When unsure, prefer OBSERVATION. Bad SUGGESTIONS burn Aleph credits
  for nothing.

OUTPUT FORMAT - a single JSON object, no preamble, no markdown fence:

{
  "label": "OBSERVATION" | "SUGGESTION",
  "needs_visualization": true | false,
  "vfx_category": "atmospheric" | "volumetric_light" | "particle"
                  | "color_grade" | "lens" | "selective_enhance"
                  | "other" | null,
  "reasoning": "1 sentence — why this label.",
  "confidence": 0.0-1.0
}

`needs_visualization` is true iff label==SUGGESTION AND we're confident
Aleph can land the treatment.
"""


@dataclass
class ClassifierResult:
    comment_id: str
    label: str
    needs_visualization: bool
    vfx_category: Optional[str]
    reasoning: str
    confidence: float
    cached: bool


def classify_segments(
    segments: Iterable[dict],
    *,
    cache_dir: Path,
    anthropic_client=None,
    model: str = DEFAULT_CLASSIFIER_MODEL,
    dry_run: bool = False,
) -> List[ClassifierResult]:
    """Classify each segment. `segments` is the list emitted by
    `commentary_synthesizer.build_commentary` (CommentSegment as dict).

    `dry_run` produces a deterministic stub label without calling
    Anthropic. Use it in tests and for pipeline plumbing checks.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not dry_run and anthropic_client is None:
        import anthropic  # local import keeps test module importable
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Required for "
                "suggestion_classifier unless dry_run=True."
            )
        anthropic_client = anthropic.Anthropic()

    results: List[ClassifierResult] = []
    for seg in segments:
        comment_id = str(seg.get("comment_id") or seg.get("_id") or "")
        narration = str(seg.get("narration_text") or "").strip()
        if not narration:
            continue
        impact = str(seg.get("impact") or "")
        seg_type = str(seg.get("type") or "")

        fp = _fingerprint(comment_id=comment_id, narration=narration, model=model)
        cache_path = cache_dir / f"{fp}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            results.append(_payload_to_result(comment_id, payload, cached=True))
            LOG.info("classifier cache hit %s -> %s", comment_id, payload.get("label"))
            continue

        if dry_run:
            payload = _stub_payload(narration=narration, impact=impact, seg_type=seg_type)
        else:
            payload = _call_classifier(
                anthropic_client,
                model=model,
                comment_id=comment_id,
                narration=narration,
                impact=impact,
                seg_type=seg_type,
            )
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        results.append(_payload_to_result(comment_id, payload, cached=False))
    return results


def _payload_to_result(comment_id: str, payload: dict, *, cached: bool) -> ClassifierResult:
    label = str(payload.get("label") or "OBSERVATION").upper()
    needs = bool(payload.get("needs_visualization", False)) and label == "SUGGESTION"
    cat = payload.get("vfx_category")
    if cat not in VFX_CATEGORIES:
        cat = None
    return ClassifierResult(
        comment_id=comment_id,
        label=label,
        needs_visualization=needs,
        vfx_category=cat,
        reasoning=str(payload.get("reasoning") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        cached=cached,
    )


def _call_classifier(
    client,
    *,
    model: str,
    comment_id: str,
    narration: str,
    impact: str,
    seg_type: str,
) -> dict:
    user_text = (
        f"COMMENT_ID: {comment_id}\n"
        f"TYPE: {seg_type}\n"
        f"IMPACT: {impact}\n"
        f"NARRATION:\n{narration}\n"
    )
    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=CLASSIFIER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    return _parse_first_json_object(text)


def _stub_payload(*, narration: str, impact: str, seg_type: str) -> dict:
    """Deterministic dry-run heuristic.

    Picks SUGGESTION with `atmospheric` category when the narration text
    contains an atmosphere/sensation cue. Otherwise OBSERVATION. The goal
    is to give the pipeline real data to chew on without an API call.
    """
    lower = narration.lower()
    atmosphere_cues = (
        "cold", "warmth", "haze", "mist", "fog", "dust", "rain", "snow",
        "wind", "breath", "light", "shadow", "dusk", "dawn", "golden",
        "atmosphere", "atmospheric", "weight", "tension", "vulnerab",
    )
    needs_viz = any(c in lower for c in atmosphere_cues)
    if needs_viz:
        cat = "atmospheric"
        if "light" in lower or "golden" in lower or "dusk" in lower or "dawn" in lower:
            cat = "volumetric_light"
        elif "rain" in lower or "snow" in lower or "ash" in lower:
            cat = "particle"
        return {
            "label": "SUGGESTION",
            "needs_visualization": True,
            "vfx_category": cat,
            "reasoning": "Stub heuristic matched atmospheric cue.",
            "confidence": 0.55,
        }
    return {
        "label": "OBSERVATION",
        "needs_visualization": False,
        "vfx_category": None,
        "reasoning": "Stub heuristic: no atmospheric cue detected.",
        "confidence": 0.55,
    }


def _fingerprint(*, comment_id: str, narration: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(comment_id.encode("utf-8"))
    h.update(b"|")
    h.update(narration.encode("utf-8"))
    h.update(b"|")
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
