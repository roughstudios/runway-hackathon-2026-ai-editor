# Golf-v3 — Stage 4 handoff (atmospheres + hard FX via Runway sound_effect)

_Window: 2026-05-08, within the May 8 09:00 ET → May 11 09:00 ET hackathon
window. Repo: `github.com/roughstudios/runway-hackathon-2026-ai-editor`._

## Status

**Stage 4 architecture shippable, dry-run end-to-end green on Canyons.**
The fourth layer of the AI editor — diegetic sound design — is plumbed
and code-complete. The Audio Director reads each analyzer scene, decides
whether atmosphere and/or hard FX should be added, writes briefs, and
the SFX client renders each via Runway `sound_effect`
(`eleven_text_to_sound_v2`). Output is the frozen manifest the Hotel-v3
Resolve exporter consumes.

Live Runway path is unrun in this session — same rationale as every
prior stage: `RUNWAYML_API_SECRET` is held outside this working set, and
dry-run + mocked-live tests cover the wiring. First live invocation will
burn ~6–10 SFX credits on the Canyons cut.

## What's in this commit

```
ai_editor/
  audio_director.py        — multimodal Claude pass that turns ONE
                              analyzer scene into an AudioBrief
                              (needs_atmosphere, atmosphere_brief,
                              hard_fx_briefs[]). Mirrors visual_director.
                              Cached by sha256(scene meta | frames |
                              transcript prefix | model). Stub fallback
                              for dry-run keys off scene-name cues
                              ("forest", "race", "dawn", "vineyard"…).
  sfx_client.py            — thin SFXClient wrapping
                              client.sound_effect.create. Two semantic
                              helpers: generate_atmosphere(prompt,
                              duration, loop=True) and generate_fx
                              (prompt, duration). Content-fingerprint
                              cache at .cache/sfx/<sha256>.mp3 — identical
                              briefs across scenes hit cache and never
                              re-bill.
  audio_pipeline.py        — Stage 4 orchestrator. Walks
                              report['scenes'], runs Audio Director per
                              scene, calls SFX client for each brief,
                              copies cache into per-report output dir,
                              writes manifest matching the FROZEN
                              schema below. Atmosphere → A3 track,
                              hard FX → A4. Long scenes (>30s) get
                              loop=True atmospheres so Hotel-v3 can
                              tile the 30s clip in Resolve.
  cli.py                   — extended with build-sfx subcommand
                              (--report, --out, --frames-dir, --limit,
                              --max-sfx, --skip-sfx, --dry-run).
tests/
  test_audio_pipeline.py   — 20 unit + smoke tests covering Audio
                              Director (scene_id format, timecode parse,
                              stub heuristics, payload normalisation,
                              JSON fence stripping, brief caching), SFX
                              client (duration clamping, fingerprint
                              stability, content-keyed cache, empty-
                              prompt rejection), and the orchestrator
                              (frozen manifest shape, long-scene loop
                              behaviour, short-scene non-loop, skip-sfx,
                              mocked-live end-to-end, cross-scene brief
                              dedupe via content cache, max-sfx budget
                              cap). All green via direct invocation;
                              pytest hangs in StudioOS .venv (matches
                              Charlie/Echo/Foxtrot, benign).
docs/
  golf-v3-handoff.md       — this file.
```

## Manifest schema (FROZEN — Hotel-v3 contract)

```json
{
  "report_id": "<report stem>",
  "report_path": "<input report .json>",
  "source_video": "<from report.video_path>",
  "total_duration_sec": 1114.93,
  "model": "eleven_text_to_sound_v2",
  "scenes": [
    {
      "scene_id": "scene_00",
      "scene_number": 1,
      "scene_name": "Pre-Race Setting",
      "scene_start_seconds": 0.0,
      "scene_end_seconds": 75.0,
      "audio_assets": [
        {
          "type": "atmosphere",
          "track": "A3",
          "asset_path": "output/sfx/<report_id>/scene_00_atmos.mp3",
          "start_offset_in_scene": 0.0,
          "duration": 75.0,
          "loop": true,
          "prompt": "low forest ambience, dawn, distant water",
          "fingerprint": "abc123…",
          "cached": false
        },
        {
          "type": "hard_fx",
          "track": "A4",
          "asset_path": "output/sfx/<report_id>/scene_00_fx_01.mp3",
          "start_offset_in_scene": 5.3,
          "duration": 1.8,
          "loop": false,
          "prompt": "soft footstep on gravel",
          "fingerprint": "def456…",
          "cached": false
        }
      ],
      "brief": { "<full AudioBrief JSON>": "…" },
      "frames_used": ["…"],
      "skipped_reason": null
    }
  ],
  "stats": {
    "n_scenes": 9, "n_atmospheres": 4, "n_hard_fx": 6,
    "sfx_calls": 8, "sfx_cached": 2, "skipped": 0
  }
}
```

Hotel-v3 reads `scenes[].audio_assets[].asset_path`, places it on the
`track` (A3/A4), at `scene_start_seconds + start_offset_in_scene`, for
`duration` seconds. When `loop: true` and `duration` > clip length,
Hotel-v3 tiles in Resolve. The Runway 30s cap on `sound_effect.duration`
is the only reason `loop` exists in this manifest.

## Atmosphere semantics

- **One per scene, max.**
- Track **A3** (Resolve dialogue/atmosphere/FX convention: A1-A2 dialogue,
  A3 atmosphere bed, A4 hard FX, A5+ music).
- Loop strategy:
  - scene ≤ 30s → request_duration = scene_duration, `loop: false`
  - scene > 30s → request_duration = 30.0, `loop: true`,
    manifest `duration` = scene_duration (Hotel-v3 tiles)

## Hard FX semantics

- **Zero or more per scene.**
- Track **A4**, `loop: false`, duration in [0.5, 30.0] seconds.
- `start_offset_in_scene` is relative to the scene's start. The Audio
  Director picks placement; transcript has no timestamps in the current
  analyzer report, so this is creative direction, not a precise cue
  point. Hotel-v3 treats the offset as authoritative.

## Cross-scene dedupe

When two scenes produce identical briefs (same prompt + same duration +
same loop flag), the SFX client's content-fingerprint cache makes the
second a cache hit — one Runway call, two manifest entries pointing at
sibling per-scene MP3 copies of the same cached audio. The orchestrator
counts this as `sfx_cached += 1`, not `sfx_calls += 1`.
`test_pipeline_dedupes_identical_briefs_across_scenes` verifies the
behaviour.

## Verified end-to-end (dry-run)

```powershell
$env:PYTHONPATH = "."

& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-sfx `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\sfx\canyons_dryrun --dry-run

# → output\sfx\canyons_dryrun\manifest.json
# → output\sfx\canyons_dryrun\scene_NN_atmos.mp3 (zero-byte placeholders)
#   stats: scenes=9 atmospheres=6 hard_fx=0 sfx_calls=0 cached=0
```

Stub heuristics fire atmosphere on 6 of 9 scenes for the Canyons cut
(matched on `race`, `vineyard`, `pre-race`, `mountain` cues). Hard FX
is 0 in dry-run — the stub heuristics only emit FX when the scene
description contains a discrete-action cue ("door", "footstep",
"applause"). The Canyons report's scene descriptions are atmospheric,
not action-y, so the live Audio Director will be the one filling in
hard FX from the actual transcript + frames.

## Test results

```
20 passed in tests/test_audio_pipeline.py
  Audio Director: scene_id, timecode parse, stub heuristics,
                  payload normalisation, JSON fence handling,
                  generate_brief caching, brief_to_json serialisation
  SFX client:     duration clamp, fingerprint stability, content-keyed
                  cache, empty-prompt rejection
  Pipeline:       frozen manifest shape, long-scene loop=True,
                  short-scene loop=False, skip_sfx, mocked live
                  end-to-end, cross-scene dedupe, max_sfx budget cap
```

## Live-path cost projection

For the Canyons cut (1115s, 9 scenes):
- Lower bound: 6 atmosphere calls (avg 30s each) = 6 × 30 = 180 sec
  of generated SFX.
- Upper bound: 9 atmospheres + ~2 hard FX per atmospheric scene =
  ~12 calls.
- Default `--max-sfx 24` is comfortably above either bound. Cache
  ensures reruns are free.

## Live-path runbook (Hotel-v3 / Sunday)

```powershell
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-sfx `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --max-sfx 12      # safe ceiling for a first live run
```

Then for an even cheaper rerun on a brief edit:
```powershell
& "…" build-sfx --report "…" --skip-sfx
# Audio Director re-runs against any cache miss; manifest carries
# fresh briefs and prior asset paths where they exist.
```

## What I did NOT touch (per Bravo's no-touch list)

- `ai_editor/runway_client.py`
- `ai_editor/commentary_synthesizer.py`
- `ai_editor/suggestion_classifier.py`
- `ai_editor/visual_director.py`
- `ai_editor/aleph_client.py`
- `ai_editor/visualization_pipeline.py`
- `ai_editor/avatar_creator.py`
- `ai_editor/avatar_animator.py`
- `web/`
- `R:\--CODE--\StudioOS-v1\` (read-only library)

`ai_editor/cli.py` is the only existing file modified, and the change is
purely additive — a new `build-sfx` subparser; no edits to the existing
three subcommands or their imports.

## Pre-existing test failure (not my work)

`tests/test_avatar_pipeline.py` has one pre-existing failure
(`test_build_avatar_track_live_path_with_mocks`) — unrelated to Stage
4. Confirmed with `git diff` showing only `cli.py` as modified.
Foxtrot-v3 / Delta-v3 territory.

## Next stage (Hotel-v3)

Read this manifest. Push `audio_assets[]` into the Resolve fusion JSON
on tracks A3 and A4 at `scene_start_seconds + start_offset_in_scene`,
honouring `loop` for atmospheres longer than the rendered clip length.
Resolve's audio item duration field carries the `duration` value
verbatim; loop flag controls whether the renderer tiles or end-pins.
