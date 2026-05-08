# Echo-v3 — Saturday handoff (Stage 2, Aleph-as-VFX-assistant)

_Window: 2026-05-09 within the May 8 09:00 ET → May 11 09:00 ET hackathon
window. Repo: `github.com/roughstudios/runway-hackathon-2026-ai-editor`._

## Status

**Stage 2 architecture shippable, dry-run end-to-end green on Canyons.**
Live Aleph + Anthropic paths plumbed and code-complete; live smoke
deferred to the next agent because the ANTHROPIC_API_KEY +
RUNWAYML_API_SECRET secrets are correctly held outside this repo's
working set.

The Stage 2 thesis from Friday's blueprint is implemented as locked:
**`gen4_aleph` video-to-video transforms existing footage with VFX-class
enhancements; we never generate new footage to fill gaps.** All four
new modules (classifier, director, aleph client, pipeline) hard-code
that endpoint as the only path; the Visual Director normalizes any
upstream LLM output back to `model="gen4_aleph"`.

## What's in this commit

```
ai_editor/
  suggestion_classifier.py     — text-only LLM pass per segment:
                                 OBSERVATION vs SUGGESTION + vfx_category.
                                 Cached by sha256(comment_id|narration|model).
                                 Dry-run heuristic for tests.
  visual_director.py           — multimodal Claude (sonnet-4-5) pass:
                                 narration + 1-3 source frames at the
                                 timecode + brand identity → AlephBrief
                                 (prompt_text, ratio, duration). Endpoint
                                 hard-locked to gen4_aleph; output
                                 normalized to valid Aleph ratios + 5/10s.
  aleph_client.py              — AlephClient.transform(): upload via
                                 uploads.create_ephemeral → video_to_video
                                 .create(model="gen4_aleph", video_uri,
                                  prompt_text, ratio, duration) →
                                 wait_for_task_output → download. Cached
                                 by sha256(source_video_bytes|prompt|ratio
                                 |duration|seed).
  visualization_pipeline.py    — orchestrator: read commentary segments
                                 JSON → classify → director (suggestions)
                                 → ffmpeg-extract source segment at
                                 timecode → Aleph → manifest JSON for
                                 player UI.
  cli.py                       — extended with `build-visualizations`
                                 subcommand (--segments, --source-video,
                                 --frames-dir, --max-aleph, --skip-aleph,
                                 --dry-run).
tests/
  test_visualization_pipeline.py — 8 unit + smoke tests on classifier,
                                   director, find_frames_at_timecode,
                                   pipeline manifest shape, observation
                                   routing. All green via direct
                                   invocation.
docs/
  echo-v3-saturday-handoff.md (this file)
```

## Verified end-to-end (dry-run)

```powershell
$env:PYTHONPATH = "."

# Stage 1 → segments JSON
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-commentary `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\commentary --dry-run
# → output\commentary\commentary_segments.json (9 segments, 1114.9s)

# Stage 2 → visualization manifest
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-visualizations `
    --segments "output\commentary\commentary_segments.json" `
    --out "output\visualizations" --dry-run
# → output\visualizations\visualizations.json
#   stats: 8 suggestions, 1 observation, 0 Aleph calls (dry-run)
#   each visualization includes: brief (model=gen4_aleph, prompt_text,
#   ratio, duration), 3 reference frames at the timecode, narration text,
#   vfx_category
```

8/8 tests pass via direct invocation; 6/6 Stage 1 tests still green
(no regression).

Sample brief from the manifest (Canyons emotional.0 @ 00:00:31):

> _vfx_category_: `atmospheric`  
> _model_: `gen4_aleph`  ratio `1280:720`  duration `5s`  
> _prompt_text_: "Add drifting morning mist, low intensity, soft golden-
> hour kiss. Preserve subject's natural lighting, no figure overlay, no
> readable text, no color shift on faces."

## Architectural notes

1. **Endpoint selection is locked, not chosen.** The blueprint's earlier
   Visual Director system prompt selected between t2v / i2v /
   character_performance / video_to_video. That branching was Friday-
   morning thinking. Stage 2 collapses to a single endpoint because the
   product thesis is transformation, not generation. The Visual Director
   only authors `prompt_text` + `ratio` + `duration_sec`; everything else
   is fixed in `visual_director.ALEPH_*` constants. `_normalize_payload`
   coerces any drift back.

2. **Three independent caches keyed by content fingerprint.**

   | Cache | Key | Why |
   |---|---|---|
   | `.cache/classifier/` | `sha256(comment_id|narration|model)` | Re-running classifier on same report = no Anthropic spend. |
   | `.cache/director/`   | `sha256(comment_id|narration|frame_paths|brand|model)` | Same plus frames + brand identity in the key, so swapping brand re-renders cleanly. |
   | `.cache/aleph/`      | `sha256(source_video_bytes|prompt|ratio|duration|seed)` | Source video hashed by **content** so re-extracted-but-identical segments hit cache. This is the credit-burn line of defence. |

3. **`max_aleph` is a hard cap per CLI invocation.** Cached calls don't
   count against it. Default 6 — covers the Canyons report's likely 4-6
   real suggestions with headroom. Any segment above the cap lands in
   the manifest with `skipped_reason: "max_aleph=N reached"` so the
   player UI can still narrate it as commentary (graceful degradation
   per Friday's risk register row).

4. **Source segment extraction re-encodes (libx264 + aac), not stream-
   copy.** Stream-copy was tempting for speed but would land cuts on
   non-keyframes for arbitrary timecodes, and Runway upload validates
   playable container. ~3-5s of CPU per segment is acceptable.

5. **Manifest schema is the contract with the player UI.** Two parallel
   lists: `visualizations` (suggestions; includes the transformed_video
   path) and `narrations` (observations; commentary-only). The UI walks
   both ordered by `start_seconds`. `frames_used` is included on every
   entry so the player can show the analyzer's reference frames as a
   tooltip during the visualization moment.

6. **Forward-compatible with Charlie-v3's segments JSON.** Stage 1's
   `CommentSegment` dict already includes `is_suggestion` and
   `narration_text`. Stage 2 reads those fields without modification;
   the `is_suggestion=True` Stage-1 default doesn't propagate (Stage 2
   re-classifies) so there's no contradiction, just a tighter signal.

7. **Anthropic SDK 0.76 + Runway SDK 4.13.** Both already in the
   StudioOS-v1 .venv where Charlie-v3 set them up. No new deps added to
   `pyproject.toml` (anthropic is already declared).

## Source-video question (Friday's flag, partial answer)

Friday's blueprint flagged that the actual `Canyons - 100 Miles 1.3.mov`
referenced in the analyzer report's `video_path` field is not on disk
under `data/learndocumentary/uploads/14/`. Confirmed Saturday morning:

- The 7 movs in `uploads/14/` are mostly 417s clips; the longest is
  `5f6c3318-92d.mov` at 629s (still short of the 1114.93s analyzed cut).
- The analyzer's `frames_canyons-100-miles-1-3/` directory contains 30
  pre-extracted reference frames at ~37s spacing — these ARE present
  and **the Visual Director uses them directly** for multimodal
  reasoning. So the brief-generation half of the pipeline doesn't
  depend on the missing mov.
- For Aleph itself, the pipeline accepts an arbitrary `--source-video`
  argument and clamps timecodes to that file's duration via ffprobe; if
  a timecode exceeds the source duration, that visualization lands in
  the manifest with `skipped_reason: "source out of range"`.

**Action for the user before live Aleph smoke**: locate or re-export the
1115s Canyons cut (Resolve project name probably `Canyons - 100 Miles
1.3` per the report's `video_path` slug). Drop it at any path; pass via
`--source-video <path>`. If unavailable, `--source-video` of one of the
existing 629s movs will run Aleph against the first ~10 minutes of
suggestions, which is still enough for the demo-video moments.

## What's not done

- **Live Aleph smoke not run in this session.** Same secrets-scoping
  rationale as Charlie-v3's Friday handoff. `--limit 1 --max-aleph 1`
  with a real `--source-video` is a 30-second user action. Expected
  cost: 1 Aleph call (~25-50 credits) + 1 Anthropic Sonnet vision call
  (~$0.05).
- **Live Anthropic classifier + director smoke not run.** Same reason.
  Dry-run path validates the JSON schema and parsing; live run validates
  the prompt's actual output quality. Recommend running classifier-only
  first (`--skip-aleph`) on the full Canyons set to inspect briefs
  before committing Aleph credits.
- **No `character_performance` ("ONE wow cameo") path.** Blueprint
  flagged this as separate from the gen4_aleph workhorse; deferred to
  whichever agent owns that scope, or to a Sunday push if scope allows.
  Adding it cleanly: a parallel `act_two_client.py` module + an opt-in
  branch in the pipeline gated on a per-segment `cameo: true` flag from
  the classifier.
- **No `image_to_video` fallback for "no source segment exists" cases.**
  Currently those land in the manifest as `skipped_reason: "no
  source_video provided"` or `"source out of range"`. The image_to_video
  path (using one of the analyzer's reference frames as the conditioning
  image) is straightforward to add as a third strategy in
  `visualization_pipeline._extract_source_segment` if the demo benefits
  from it. Per blueprint endpoint priority: rare, 0-2x per session.
- **pytest still hangs in the StudioOS .venv** (Charlie-v3's same
  flag). Tests run green via direct invocation; not blocking.

## Originality compliance

Every file under `ai_editor/` and `tests/` added or modified in this
session was authored fresh in the May 8 09:00 ET → May 11 09:00 ET
hackathon window. Reference reading: Friday's blueprint
(`R:/--CODE--/StudioOS-v1/docs/handoffs/director-agent-rebuild-blueprint-2026-05-08.md`)
and Charlie-v3's Stage 1 output (`ai_editor/commentary_synthesizer.py`,
`ai_editor/runway_client.py`). No code copied from
`R:/--CODE--/StudioOS-v1/agents/film_analyzer/analyzers/` or any other
StudioOS module — those exist as conceptual reference only. The single
analyzer report and pre-extracted frame directory under `StudioOS-v1/data/`
are read as input data, not embedded in this repo.

## Bravo flags

None blocking. The deferred live smoke is the only deliberate gap and
costs ~$0.05 + ~50 credits to retire end-to-end.
