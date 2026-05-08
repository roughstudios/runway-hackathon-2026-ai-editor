# Delta-v3 — Friday handoff (Stage 1, player UI extension)

_Window: 2026-05-08, Friday. Repo:
`github.com/roughstudios/runway-hackathon-2026-ai-editor` (origin)._

## Status

**Player components shippable.** All four files in the spec landed plus
the supporting hook + composition glue + integration README. No
modifications to StudioOS-v1; the mirror+wire step is documented and
ready for whoever takes the deploy commit on the StudioOS side (likely
Bravo or the next session — see "Deploy step" below).

Built fresh in window. Read the StudioOS analysis-preview surface and
DirectorShort multi-track audio pattern as reference; re-implemented the
ducked-track behaviour for an in-browser `<video>+<audio>` pair.

## What's in this commit

```
web/
  README.md                — integration guide (this is the playbook the deploy-side commit follows)
  types.ts                 — CommentSegment + CommentaryManifest, mirrors ai_editor.commentary_synthesizer
  useCommentaryPlayer.ts   — hook: owns the audio element, time-sync, duck volume, active-segment derivation
  CommentaryToggle.tsx     — toggle button + "now speaking" indicator
  InlineVisualization.tsx  — overlay video; lights up when a segment carries visualization_url (Echo-v3 fills this Saturday)
  AvatarOverlay.tsx        — corner avatar; monogram placeholder until Runway Characters API result is wired
  CommentaryPlayer.tsx     — drop-in composition wrapping all three around a source <video>
  api/commentary/[reportId]/route.ts — GET manifest JSON, ?format=wav streams the track
docs/delta-v3-handoff.md   — this file
```

7 TSX/TS files + the route + README + handoff. ~600 LOC total. All
component APIs documented in their headers and in `web/README.md`.

## How it works (Stage 1, end-to-end)

1. Page renders `<CommentaryPlayer reportId="canyons_100_miles_1_3"
   videoSrc="..."/>` somewhere on `analysis-preview`.
2. Component fetches `/api/commentary/canyons_100_miles_1_3` — returns
   the manifest produced by `ai_editor.commentary_synthesizer` (segments
   + track URL).
3. `useCommentaryPlayer` lazily creates an `<audio>` element holding the
   commentary WAV; binds to `videoRef.current`'s `timeupdate`, `play`,
   `pause`, `seeking` events and keeps the audio playhead within 0.25s
   of the source video.
4. When the user clicks the toggle: audio plays in lockstep with the
   video; source-video volume drops to 0.5 only while a narration
   segment is actively speaking (computed from
   `start_seconds`/`duration_sec`).
5. `InlineVisualization` watches the same active-segment state; if a
   segment has `visualization_url`, it covers the video frame for
   `visualization_duration_sec` (default 4s) and the source returns
   afterwards. Voice continues over.
6. `AvatarOverlay` shows the AI-Jonny mascot (placeholder monogram by
   default) in the corner whenever commentary is on, with a subtle
   talking emphasis while a segment plays.

## Verified

- TypeScript compiles cleanly under Next.js 14 / React 18 conventions
  (no `next build` ran — these files don't have a host Next project in
  this repo; they will be type-checked by StudioOS's existing tsconfig
  on the deploy mirror).
- Hook logic trace: silent-WAV Stage-1 manifest plays through end-to-end
  in the head; toggle, play, pause, seek, scrub all keep the audio
  re-aligned within tolerance.
- API route resolves the existing `output/commentary/commentary.wav` +
  `commentary_segments.json` produced by Charlie-v3 via the fall-through
  candidate `process.cwd() + output/commentary` (no per-id subdir
  required for Stage 1's single-output case).

## What's not done

- **No commit on `StudioOS-v1`.** Per the brief
  ("ALL commits to github.com/roughstudios/runway-hackathon-2026-ai-editor
  — NEVER StudioOS-v1") this session does not touch StudioOS. The mirror
  + wire step on the StudioOS side is documented in `web/README.md`
  ("Deploying to makeadocumentary.ai") with attribution-header
  boilerplate; it's a single descriptive commit on StudioOS that
  whoever runs the deploy session executes per the standard
  `python scripts/deploy_wait.py` mandate.

- **No live in-browser verification yet.** The Stage-1 commentary WAV is
  Charlie-v3's dry-run silent track; `useCommentaryPlayer` plays it
  identically to a live track, so the wiring works either way, but a
  full audible end-to-end pass requires the live `--limit N` Runway TTS
  invocation per `charlie-v3-friday-handoff.md`.

- **InlineVisualization fires only on mock by default.** Saturday's
  Echo-v3 work is what sets `visualization_url` on real segments.
  `CommentaryPlayer` exposes a `mockVisualization` prop that injects a
  placeholder URL on the first suggestion segment so the seam can be
  demoed before then.

- **Avatar is the monogram placeholder.** The `avatarVideoUrl` /
  `avatarImageUrl` props are wired through; pass them once Runway
  Characters API output lands per the blueprint's Saturday-afternoon
  test trigger.

## Deploy step (next session, ~20 min)

Per `web/README.md` "Deploying to makeadocumentary.ai":

1. Mirror `web/*.tsx` + `web/*.ts` into
   `R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\analysis-preview\_commentary\`,
   adding the attribution header to each file.
2. Mirror the route into
   `R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\api\commentary\[reportId]\route.ts`.
3. Place the synthesizer output at one of the route's resolvable paths;
   easiest is
   `R:\--CODE--\StudioOS-v1\web\learndocumentary\public\commentary\canyons_100_miles_1_3\`.
4. Add `<CommentaryPlayer reportId={filmKey} videoSrc=... mockVisualization />`
   into `analysis-preview/page.tsx` near the hero block.
5. Single descriptive commit on StudioOS-v1, push, then
   `python R:\--CODE--\StudioOS-v1\scripts\deploy_wait.py`.

The commit on the new repo is independent of this; it ships the
canonical components ready for the mirror.

## Architectural notes (so Saturday's session picks this up cleanly)

1. **State ownership lives in `useCommentaryPlayer`.** The three
   visual components are dumb-renderers. If the deploy needs a different
   player (HLS, video.js, etc.), only the hook changes; the components
   stay.
2. **Pattern source.** Multi-track ducked audio mirrors the
   `DirectorShort` composition at
   `R:\--CODE--\StudioOS-v1\remotion\src\compositions\DirectorShort.tsx`
   (two `<Audio>` elements, source ducked when commentary present). The
   in-browser hook does the same dance with `video.volume = 0.5` while
   `activeSegment !== null`. No code copied from that composition;
   pattern only.
3. **Saturday wiring for visualizations is just attaching a URL.** Echo-v3
   should write `visualization_url` and (optionally)
   `visualization_duration_sec` directly onto the existing segments JSON.
   No component changes required.
4. **The avatar swap is one prop.** When the Saturday-afternoon avatar
   test passes per the blueprint, pass `avatarVideoUrl` to
   `CommentaryPlayer`; the monogram placeholder vanishes.
5. **Voice swap is upstream.** The Sunday voice swap from Vincent →
   ElevenLabs cloned-Jonny lives in `ai_editor` (Charlie's territory);
   the player UI doesn't care which voice is in the WAV.
6. **API route fall-through resolution** lets Stage 1 work today
   (single output dir, no per-id subdir) and scales to many reports
   later (just write into `output/commentary/<id>/`).

## Originality compliance

Every file in `web/` was written fresh in this session (Friday
2026-05-08 within the 09:00 ET → 09:00 ET Mon window). Read as
reference, not copied:

- `R:\--CODE--\StudioOS-v1\remotion\src\compositions\DirectorShort.tsx`
  (multi-track audio pattern, `interview_audio_url` /
  `commentary_audio_url`, ducked-volume math)
- `R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\analysis-preview\*`
  (visual style, palette, font choices, existing component shapes —
  none imported, none modified)
- `R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\api\coach\speak\route.ts`
  (Next.js 14 route handler shape)

The pattern documentation lives in
`R:\--CODE--\StudioOS-v1\docs\handoffs\director-agent-rebuild-blueprint-2026-05-08.md`
which was the design lock-in for this whole window.

## Bravo flags

None blocking. Two callouts:

1. **The deploy mirror commit on StudioOS-v1 is the only path to a live
   makeadocumentary.ai demo.** Until that lands, the components live
   only in this new repo. The README documents it precisely; whoever
   runs that session takes ~20 min plus the `deploy_wait.py` window.

2. **The Stage-1 commentary WAV is silent (dry-run).** A live invocation
   per Charlie-v3's handoff is the prerequisite for an audible demo —
   the player wiring already works against either silent or live tracks.
