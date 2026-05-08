# Foxtrot-v3 — Stage 3 handoff (AI Jonny avatar character)

_Window: 2026-05-08, within the May 8 09:00 ET → May 11 09:00 ET hackathon
window. Repo: `github.com/roughstudios/runway-hackathon-2026-ai-editor`._

## Status

**Stage 3 architecture shippable, dry-run end-to-end green on Canyons.**
The third modality of the AI editor — visual identity — is plumbed and
code-complete: voice (Charlie-v3) + Aleph VFX (Echo-v3) + Avatar (this)
= three modalities, one character. Live Runway path is unrun in this
session because `RUNWAYML_API_SECRET` is correctly held outside this
repo's working set, matching the secrets-scoping rationale of every
prior stage.

The submission's voice/likeness/VFX trinity now has a single coherent
character to project from the player UI corner.

## What's in this commit

```
ai_editor/
  avatar_creator.py        — one-time Runway Avatars API bootstrap.
                              Selects a Jonny reference photo, uploads
                              via uploads.create_ephemeral, calls
                              avatars.create with personality + voice
                              preset (vincent, matches Charlie-v3 dev
                              TTS), polls retrieve until READY, caches
                              record at data/avatars/jonny_character.json.
                              get_or_create_jonny_character() is the
                              public surface.
  avatar_animator.py       — per-segment talking-head animation via
                              avatar_videos.create. AvatarAnimator class
                              fingerprints (audio_bytes|character_id|
                              model), caches at .cache/avatar_videos/.
                              build_avatar_track orchestrator walks
                              Charlie-v3 commentary_segments.json,
                              animates each, emits manifest matching
                              the Delta-v3 AvatarOverlay contract.
  cli.py                   — extended with build-avatar-track subcommand
                              (--segments, --out, --limit, --max-segments,
                              --reference-image, --dry-run).
data/
  avatars/jonny_reference_frames/
    jonny_main.jpg         — primary reference photo (gitignored, sourced
                              from StudioOS training_photos_v2 at session
                              start).
    jonny_alt.jpg          — alternate (also gitignored).
tests/
  test_avatar_pipeline.py  — 12 unit + smoke tests on creator (cache,
                              processing-poll, record-normalisation),
                              animator (fingerprint stability, content-
                              keyed cache, mocked end-to-end), and
                              build_avatar_track (dry-run, mocked-live,
                              missing-audio, report-id derivation).
                              All green via direct invocation; pytest
                              hangs in StudioOS .venv (Charlie/Echo's
                              same flag, benign).
docs/
  foxtrot-v3-handoff.md    — this file.
```

## Verified end-to-end (dry-run)

```powershell
$env:PYTHONPATH = "."

# Stage 1 → segments JSON (3 dry-run moments):
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-commentary `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\commentary --dry-run --limit 3

# Stage 3 → avatar manifest:
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-avatar-track `
    --segments output\commentary\commentary_segments.json --dry-run
# → output\avatar_segments\canyons_100_miles_1_3\manifest.json
# → output\avatar_segments\canyons_100_miles_1_3\seg_00..02.mp4 (placeholders)
#   stats: animated=3/3 runway=0 cached=0 skipped=0
```

12/12 Stage-3 tests pass. 6/6 Stage 1 + 8/8 Stage 2 tests still green
(no regression).

Sample manifest entry (Canyons emotional.0 @ 00:00:31):

```json
{
  "comment_id": "emotional.0",
  "start_seconds": 31.67,
  "end_seconds": 34.813,
  "duration_sec": 8.857,
  "narration_text": "Tension here. Let the speaker's face hold for an extra second...",
  "mp4_path": ".../output/avatar_segments/canyons_100_miles_1_3/seg_00.mp4",
  "cached": false,
  "task_id": "<dry-run>",
  "fingerprint": "<dry-run>",
  "skipped_reason": null
}
```

## SDK shape (the part the brief asked us to verify)

The blueprint and the brief both hedged on whether the talking-head
endpoint was `character_performance` or `avatar_videos`. Verified in
this session against `runwayml==4.13.0`:

- **`client.avatars.create`** — one-time character bootstrap. Required
  args: `name`, `personality` (system prompt for `realtime_sessions`,
  unused by Stage 3 itself but mandatory at the type level), single
  `reference_image` URL, `voice` (preset or custom).
  Returns `AvatarCreateResponse` — a discriminated union of
  `Processing | Ready | Failed` keyed on `status`. `id` is stable across
  states. Optional `image_processing="optimize"` does the face-crop
  preprocessing for us.
- **`client.avatars.retrieve(id)`** — same discriminated-union shape,
  used to poll `PROCESSING → READY`.
- **`client.avatar_videos.create`** — the audio-driven talking-head
  endpoint and Stage-3's hot path. Required args: `avatar`
  (`{"type": "custom", "avatarId": <id>}` for our case),
  `model="gwm1_avatars"`, `speech` (audio URI **or** text). Returns
  the standard `NewTaskCreatedResponse` with `wait_for_task_output()`
  helper — same polling contract as `text_to_speech` and
  `video_to_video`.
- **`client.character_performance.create`** — Act-Two reanimation. Takes
  `character` (image URI or video URI), `model="act_two"`, `reference`
  (a 3-30s video of someone performing the way you want the character
  to). Stage 3 does NOT call this — Echo-v3 reserves the ONE Act-Two
  cameo slot for its visualization use, and `avatar_videos` is the
  right endpoint for audio-driven lip-sync anyway.

Net: the blueprint's "characters.create / character_performance" line
resolves to **avatars.create + avatar_videos.create** in the actual SDK,
with `character_performance` reserved as Echo-v3's territory.

## Source assets — which path we took

The brief said: BRAW extraction from `N:\Backup offload\A162_09121344_C035.braw`
preferred; fallback to `R:\--CODE--\StudioOS-v1\data\` photos.

**Decision: fallback path.** Reasons:

1. ffmpeg cannot decode BRAW directly — Blackmagic's RAW format requires
   the BRAW SDK or DaVinci Resolve. Adding either as a Stage-3
   build-time dependency is out of scope.
2. `R:\temp_jonny_voice\test_frames\frame_*s.jpg` exist on disk but are
   inspection frames from the Canyons documentary itself (Tommy in
   profile against Sierra foothills) — they're not Jonny's face.
3. `R:\--CODE--\StudioOS-v1\data\thumbnail_generator\training_photos_v2\
   JonnyVonWallstrom-2.jpg` is a clean Jonny portrait (face visible,
   lit by window backlight, holding camera at chest level — does not
   obscure face). This is the chosen primary reference.

Both `JonnyVonWallstrom-2.jpg` (primary) and `JonnyVonWallstrom-1.jpg`
(alternate, slightly lower-key version of the same setup) are copied
into `data/avatars/jonny_reference_frames/` at session start. The
directory is gitignored (per the project's `.gitignore` `data/` rule);
the photos are pre-existing assets, not authored in-window.

## Architectural notes

1. **Two independent caches keyed by content fingerprint.**

   | Cache | Key | Why |
   |---|---|---|
   | `data/avatars/jonny_character.json` | (single record) | Avatar creation is a one-time event. Subsequent runs read this; if status is `READY`, no API call. If `PROCESSING`, poll instead of recreate (handles crash-recovery). If `FAILED` or absent, create fresh. |
   | `.cache/avatar_videos/<fp>.mp4` | `sha256(audio_bytes\|character_id\|model)` | Per-segment animation cache. Audio is hashed by **content** so re-rendered-but-identical TTS hits cache; same character + same voice text = same fingerprint = no new credits burned on demo iteration. |

2. **`character_performance` vs `avatar_videos` is a hard split.**
   Stage 3's commentary-playback path needs audio-driven lip-sync over
   a static portrait. That's exactly `avatar_videos` (model
   `gwm1_avatars`). `character_performance` (Act-Two) needs a
   reference-video performance — wrong shape for our input, and
   reserved by Echo-v3 for the ONE wow cameo. The two endpoints have
   non-overlapping use cases in this submission.

3. **Voice preset on the avatar is functionally cosmetic for Stage 3.**
   `avatars.create` requires a voice configuration so the character can
   plug into `realtime_sessions` later. We pass `vincent` to match
   Charlie-v3's dev-mode TTS preset for narrative consistency. It does
   not affect `avatar_videos.create` output because we always pass
   pre-rendered audio (`speech={"type": "audio", "audio": <uri>}`),
   which overrides per call. Sunday's voice flip from Vincent →
   ElevenLabs cloned-Jonny lives entirely in Charlie-v3's territory;
   Stage 3 just consumes whatever audio the segments JSON carries.

4. **Manifest schema is the contract with Delta-v3.**
   `output/avatar_segments/<report_id>/manifest.json` carries
   `segments[]` ordered by `start_seconds`, each entry pointing at a
   `seg_<n>.mp4` in the same directory. Delta-v3's `AvatarOverlay`
   takes `avatarVideoUrl` as a single prop — for Stage 3 the player
   selects the active segment's `mp4_path` from the manifest based on
   the source video's playhead. The unanimated/observation-only
   fallback (when a segment has no `audio_path` from Stage 1) lands in
   the manifest with `skipped_reason` populated; the player can
   degrade gracefully to the placeholder monogram for those windows.

5. **The avatar IS the AI editor's mascot in the player only.**
   Editorial-commitment compliance preserved: AI Jonny appears as the
   corner overlay in the analysis-preview player; real Jonny appears
   in the demo video opening. No AI replacement of real subjects in
   any cut.

6. **Forward-compatible with Charlie-v3's segments JSON shape.**
   `CommentSegment.audio_path` (str path to `.cache/tts/<fp>.mp3`) is
   already populated when Stage 1 runs without `--dry-run`. Stage 3
   reads that field directly with no schema changes; the `cached`,
   `start_seconds`, `end_seconds`, `narration_text`, `comment_id`
   fields all flow through into the avatar manifest. When Sunday's
   voice swap from Runway preset to ElevenLabs cloned-Jonny lands,
   Stage 3 keeps working unchanged because the audio_path still points
   at a local mp3.

7. **`max_segments` cap mirrors `max_aleph` from Stage 2.** Cached
   segments don't count against it. Defaults to "unlimited" — the
   Canyons report yields 9 commentary moments, so per-run cost ceiling
   is ~9 avatar_videos calls × demo iterations. Cache hit rate is
   expected to be high after the first end-to-end run (audio is the
   same across reruns).

## Live-smoke runbook (for the next session, ~10 min, ~$2-5 of credits)

```powershell
$env:PYTHONPATH = "."
$env:RUNWAYML_API_SECRET = "<your secret>"

# 1. Run Stage 1 live (single-segment cheap smoke first):
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-commentary `
    --report "R:\--CODE--\StudioOS-v1\data\film_analyzer\reports\canyons_100_miles_1_3.json" `
    --out output\commentary --limit 1
# → output\commentary\commentary_segments.json (one populated audio_path)

# 2. Bootstrap the avatar (one-time, ~30-60s wait while Runway processes):
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" -m ai_editor.cli `
    build-avatar-track `
    --segments output\commentary\commentary_segments.json `
    --max-segments 1
# → first run: creates avatar (cached at data/avatars/jonny_character.json),
#   animates 1 segment, writes manifest. Subsequent runs hit both caches.

# 3. Inspect:
Get-Content output\avatar_segments\canyons_100_miles_1_3\manifest.json
ls output\avatar_segments\canyons_100_miles_1_3\seg_00.mp4
# Open the mp4 in any player; should be lip-synced AI-Jonny over the
# narration audio.
```

After verification, scale by removing `--max-segments 1` and re-running
both commands without the `--limit` flag — cached calls return instantly,
new segments cost ~1 avatar_videos generation each.

## What's not done

- **Live Runway path not run in this session.** Same secrets-scoping
  rationale as Charlie-v3 and Echo-v3: agent does not extract
  `RUNWAYML_API_SECRET` from the StudioOS `.env` into the new repo. The
  10-minute runbook above retires it for ~$2-5.
- **Reference image is a single still.** `avatars.create` accepts only
  one `reference_image` URL. We chose `jonny_main.jpg`. The directory
  holds an alternate so a future session can A/B which produces a
  better avatar without changing code (`--reference-image
  data/avatars/jonny_reference_frames/jonny_alt.jpg`).
- **No BRAW frame extraction.** Documented in "Source assets" above —
  ffmpeg can't decode BRAW directly. If the Sunday demo needs a
  literally-BMPCC4K-coherent reference (matching the voice clone's
  source clip), a short Resolve export pass would be a 5-minute manual
  step: open `A162_09121344_C035.braw`, scrub to a clean head-and-
  shoulders frame, export 1920x1080 PNG, drop into
  `data/avatars/jonny_reference_frames/jonny_braw.jpg`, rerun with
  `--reference-image .../jonny_braw.jpg`. The `data/avatars/
  jonny_character.json` cache must be cleared for the new ref to take
  effect.
- **HTTPS-vs-runway:// uri provenance is unverified for avatars.**
  The SDK type docstrings say `reference_image` and
  `speech.audio` are "A HTTPS URL", but the SDK's `upload.uri` returns
  a `runway://` URI — same shape every other endpoint in this codebase
  (`video_to_video`, `text_to_speech`) accepts without complaint. The
  live smoke above retires this question; if the API rejects the
  `runway://` URI, the fix is to either route uploads through a public
  HTTPS host or use a Runway-side public-URL helper. Documented as a
  Bravo flag below.
- **pytest still hangs in StudioOS .venv.** Charlie-v3 / Echo-v3's same
  benign flag. Tests run green via direct invocation
  (`python tests/test_avatar_pipeline.py`); not blocking.

## Originality compliance

Every file under `ai_editor/` and `tests/` added or modified in this
session was authored fresh in the May 8 09:00 ET → May 11 09:00 ET
hackathon window. Reference reading: Friday's blueprint
(`R:/--CODE--/StudioOS-v1/docs/handoffs/director-agent-rebuild-blueprint-2026-05-08.md`),
Charlie-v3's Stage 1 output (`runway_client.py`,
`commentary_synthesizer.py`), Echo-v3's Stage 2 output
(`aleph_client.py`, `visualization_pipeline.py`), Delta-v3's UI surface
(`web/AvatarOverlay.tsx`, `web/types.ts`), and the runwayml-python SDK
type stubs at `.venv/Lib/site-packages/runwayml/types/avatar_*.py`. No
code copied from `R:/--CODE--/StudioOS-v1/agents/` or any other
StudioOS module. The two Jonny portrait photographs at
`data/avatars/jonny_reference_frames/` are pre-existing assets sourced
from `StudioOS-v1/data/thumbnail_generator/training_photos_v2/`; they
are gitignored (per the existing `.gitignore` `data/` rule), used as
input data only, and not embedded in the repo.

## Bravo flags

None blocking. Two callouts:

1. **`runway://` URI on the avatar endpoints is unverified.** The SDK
   type docstrings say HTTPS; every other endpoint in this codebase
   accepts the SDK's upload URI as-is. Live smoke retires the question
   for ~$0.50.

2. **The single reference image bottlenecks avatar quality.**
   `avatars.create` takes one URL, not many. If the chosen still
   produces an uncanny avatar, the alternate is a one-flag swap
   (`--reference-image .../jonny_alt.jpg`) plus deleting
   `data/avatars/jonny_character.json` to bust the bootstrap cache.
   Saturday-afternoon test trigger from the blueprint applies here:
   if quality lands, integrate via Delta-v3's `avatarVideoUrl` prop;
   if uncanny, the player gracefully degrades to the monogram via
   `AvatarOverlay`'s existing fallback path with no other changes.
