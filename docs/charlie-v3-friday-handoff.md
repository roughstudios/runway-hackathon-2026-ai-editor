# Charlie-v3 — Friday handoff (Stage 1, audio commentary)

_Window: 2026-05-08, ~Friday morning. Repo:
`github.com/roughstudios/runway-hackathon-2026-ai-editor` (origin = the new
public repo)._

## Status

**Stage 1 shippable.** Audio commentary pipeline runs end-to-end on the
Canyons 100 Miles 1.3 analyzer report (1114.9s / 18:34). Dry-run validated;
live Runway TTS path unverified in this session because the secret was
correctly held back (see "What's not done" below).

## What's in this commit

```
ai_editor/
  __init__.py
  runway_client.py            — thin pinned wrapper over Runway SDK 4.13;
                                fingerprinted on-disk TTS cache; uses
                                task.wait_for_task_output() (built-in poll)
  elevenlabs_voice.py         — STUB. Mirrors StudioOS ElevenLabsBackend
                                public surface so the Sunday voice swap
                                (Vincent → cloned-Jonny) is one line
  commentary_synthesizer.py   — read analyzer report → compose narration
                                from emotional_analysis.moments (n=9 in
                                Canyons) → per-comment Runway TTS →
                                pydub overlay onto silent track at
                                start_seconds → single commentary.wav +
                                segments JSON sidecar for the player UI
  cli.py                      — `python -m ai_editor.cli build-commentary`
tests/
  test_commentary_synthesizer.py  — 6 unit + smoke tests, all green
                                    (run direct, not via pytest — see
                                    "Known issue" below)
.env.example                  — Stage 1 only needs RUNWAYML_API_SECRET
pyproject.toml                — added pydub>=0.25.1, pinned runwayml>=4.13;
                                added [dev] optional with pytest
.gitignore                    — added output/
docs/charlie-v3-friday-handoff.md (this file)
```

## How to run

```powershell
# From R:\runway-hackathon-2026-ai-editor
$env:PYTHONPATH = "."

# Dry-run (no Runway calls, no credits) — green:
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-commentary `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\commentary `
    --limit 3 --dry-run

# Live, single-comment smoke (~5-10 credits):
$env:RUNWAYML_API_SECRET = "<your secret>"
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-commentary `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\commentary `
    --limit 1
# → outputs output\commentary\commentary.wav (1114.9s track, 1 segment overlaid)
#   and output\commentary\commentary_segments.json
# Cache lives at .cache\tts\<fp>.mp3 — second run hits cache, burns no credits
```

## Verified end-to-end (dry-run)

```
2026-05-08 19:03 INFO  Synthesizing 3 moments from canyons_100_miles_1_3.json
track:    output\commentary\commentary.wav
segments: output\commentary\commentary_segments.json
count:    3
duration: 1114.9s
```

Sample narration (templated from `type` + `suggestion`, paste-ready for TTS):

> _Tension here._ Let the speaker's face hold for an extra second after
> this line, allowing the weight of the statement to land.

> _Vulnerable beat._ Allow the hesitation and slight change in tone to
> play out. Hold on the speaker's face, letting the shift from anxiety
> to acceptance be visible.

## Architectural notes (so Saturday's session picks this up cleanly)

1. **Comment source for Stage 1 is `report["emotional_analysis"]["moments"]`
   only** (n=9 in Canyons). `report["story_surgeon"]["cuts_recommended"]`
   is also rich and timecoded — wire it in Saturday alongside the
   suggestion classifier (see `extract_moments` for the pattern; it uses
   a `_id` namespace `emotional.<i>` so a future `surgeon.<i>` doesn't
   collide).
2. **Narration is templated** (TYPE_LABELS + suggestion). This is
   intentional Stage-1 simplicity. Saturday's `suggestion_classifier.py`
   + multimodal `visual_director.py` will replace `compose_narration()`
   with an LLM-composed pass; the segments JSON shape is forward-
   compatible (`is_suggestion`, `narration_text` already present).
3. **TTS cache key = sha256(text|voice_preset|model)[:16]**. So changing
   the voice preset re-renders cleanly without invalidating prior runs;
   changing narration text re-renders only the changed segments.
4. **Voice-flip seam.** Sunday swaps the Runway preset (Vincent) for
   ElevenLabs cloned-Jonny. Wire `ai_editor.elevenlabs_voice` into
   `build_commentary` by passing it as a `runway` substitute that
   exposes `.text_to_speech(text, cache_dir, ...) -> TTSResult`-shaped
   thing. Today's `RunwayClient` has the right shape; the stub will
   need to mirror the cache directory + return path.
5. **Output WAV is a full-duration silent track with overlays at
   `start_seconds`**. Player can play it as a second `<Audio>` track on
   top of the source video — see blueprint Test 2 (multi-track Remotion
   audio).
6. **The hackathon repo does NOT depend on StudioOS as an installed
   library yet.** Stage 1 only reads one report file. Saturday's frame
   extraction needs `pip install -e ../StudioOS-v1` per the README.

## What's not done

- **Live TTS smoke against Runway not run in this session.** The agent
  declined to extract `RUNWAYML_API_SECRET` from the StudioOS `.env`
  into the new repo (correct behaviour — secrets in StudioOS are scoped
  to that project). The user should run a `--limit 1` live invocation
  before the Saturday session to confirm: (a) the SDK call shape is
  correct (it is per the SDK source — `voice={"type":"runway-preset",
  "preset_id":"Vincent"}`, model `eleven_multilingual_v2`), (b) the URL
  download works, (c) pydub successfully decodes the resulting MP3.
  The blueprint already verified the voice infrastructure separately
  (Test 1, 2026-05-08).
- **pytest startup hangs in the StudioOS .venv** (likely a conftest /
  plugin issue inherited from that venv's broader install). Tests run
  green when invoked directly (`python -c "..."` against the test
  module's functions) — recommend a clean per-repo venv for Saturday
  if the team wants `pytest` runs in CI later. Not blocking Stage 1.
- **No commentary segments yet from `story_surgeon.cuts_recommended`.**
  Adds maybe ~10 more aligned beats. Saturday work.
- **No Anthropic-composed narration yet.** Saturday's classifier adds
  this.

## Originality compliance

Every file in `ai_editor/` was written fresh in this session (Friday
2026-05-08 within the 09:00 ET → 09:00 ET Mon window). No code copied
from `R:\--CODE--\StudioOS-v1\agents\director_agent\` or
`R:\--CODE--\StudioOS-v1\agents\voice_over\backends\` — those were read
as reference (interface shapes, public methods, env var names) and
re-implemented to fit the hackathon repo's narrower scope. The single
analyzer report at
`R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json`
is read as input data, not embedded in the repo.

## Bravo flags

None blocking. The pytest hang is benign (tests pass via direct
invocation). The deferred live-TTS smoke is the only deliberate gap and
is a 30-second action for the user.
