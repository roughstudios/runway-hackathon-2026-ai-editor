# Player UI extension — Stage 1

React components that turn the existing `analysis-preview` surface on
[makeadocumentary.ai](https://makeadocumentary.ai) into the AI editor's
interactive player: an audio-commentary toggle, a corner avatar overlay,
and an inline-visualization slot for Saturday's Aleph clips.

These files are the **canonical source** for the hackathon submission.
The deploy target — the `web/learndocumentary/src/app/analysis-preview`
Next.js 14 (App Router) page in the private `StudioOS-v1` repo — gets a
mirror copy with attribution; see "Deploying to makeadocumentary.ai"
below.

Reference (read-only): the existing analysis-preview components live at
`R:\--CODE--\StudioOS-v1\web\learndocumentary\src\app\analysis-preview\`
(NoteItem, EmotionalCurve, MarkedTranscript, StoryAxis). This extension
does not modify them.

## Files

| File | Role |
|---|---|
| `types.ts` | `CommentSegment` + `CommentaryManifest` — mirrors the synthesizer dataclass |
| `useCommentaryPlayer.ts` | Hook that owns the `<audio>` element, time-sync, duck volume, active-segment derivation |
| `CommentaryToggle.tsx` | Toggle button + "now speaking" indicator |
| `InlineVisualization.tsx` | Overlay video that covers the source frame for 3-5s when an active segment carries a `visualization_url` |
| `AvatarOverlay.tsx` | Corner avatar; placeholder monogram until Runway Characters API result is wired |
| `CommentaryPlayer.tsx` | Drop-in composition that wires all three around a source `<video>` |
| `api/commentary/[reportId]/route.ts` | GET manifest JSON · `?format=wav` streams the commentary track |

## Quick start (standalone, in this repo)

```tsx
import { CommentaryPlayer } from "./web/CommentaryPlayer";

<CommentaryPlayer
  reportId="canyons_100_miles_1_3"
  videoSrc="/canyons.mp4"
  // Saturday-stretch demo: mock the inline visualization until Echo-v3 ships
  mockVisualization
  mockVisualizationUrl="/canyons-aleph-demo.mp4"
/>
```

The component fetches `/api/commentary/canyons_100_miles_1_3`, gets back
the manifest + track URL, then keeps the audio playhead locked to the
source video.

## Deploying to makeadocumentary.ai (StudioOS-v1)

The Vercel build for `makeadocumentary.ai` runs out of
`R:\--CODE--\StudioOS-v1\web\learndocumentary` per the StudioOS deploy
contract (`rootDirectory` is API-set; `git push` triggers deploy; never
run the `vercel` CLI). To extend the existing `analysis-preview` page:

1. **Mirror the components** (single commit on `roughstudios/StudioOS-v1`):
   ```
   web/learndocumentary/src/app/analysis-preview/_commentary/
     types.ts
     useCommentaryPlayer.ts
     CommentaryToggle.tsx
     InlineVisualization.tsx
     AvatarOverlay.tsx
     CommentaryPlayer.tsx
   ```
   Each mirror gets a header attribution comment:
   ```ts
   // Mirrored from roughstudios/runway-hackathon-2026-ai-editor:web/<file>.
   // Source of truth lives there. Edit there and re-mirror.
   ```

2. **Mirror the API route**:
   ```
   web/learndocumentary/src/app/api/commentary/[reportId]/route.ts
   ```
   Same attribution header. The route reads from
   `<repo-root>/output/commentary/<reportId>/` and falls through to
   `<repo-root>/output/commentary/` for the Stage-1 single-output case.
   Synced output is checked into git or staged via Vercel build artifacts
   — see "Output sync" below.

3. **Wire into `analysis-preview/page.tsx`**:
   In the hero section (just below the title bar — see existing structure
   around the `<ExportAllBar>` line), add:
   ```tsx
   import { CommentaryPlayer } from "./_commentary/CommentaryPlayer";

   {/* AI editor commentary player — extends the analysis-preview surface */}
   <CommentaryPlayer
     reportId={filmKey}
     videoSrc={`/cuts/${filmKey}.mp4`}
     mockVisualization
   />
   ```
   (The `videoSrc` resolution depends on where Charlie/Echo land the
   source cut. For Friday EOD demo, point it at the Canyons cut hosted
   on Vercel Blob.)

4. **Output sync**: copy the synthesizer artefacts to a path the route
   resolver finds. Two options:
   - Put `output/commentary/<id>/` at the repo root and let the route's
     `process.cwd() + ../../output/commentary/<id>` candidate resolve to
     it (rootDirectory is `web/learndocumentary`, so `../../` points at
     repo root).
   - Copy them into `web/learndocumentary/public/commentary/<id>/` for a
     fully static deploy.

   `web/learndocumentary/public/` is the simplest Stage-1 path. The WAV
   for Canyons is ~100 MB — within Vercel's deployment-size limits but
   we should consider Blob storage for Stage-2 if we add many cuts.

5. **Push**. The Vercel webhook builds and deploys. After the push, run:
   ```powershell
   python R:\--CODE--\StudioOS-v1\scripts\deploy_wait.py
   ```
   per the StudioOS CLAUDE.md mandatory in-session check.

## What's still mocked / pending

- **InlineVisualization** has no real data yet — every segment from
  `commentary_synthesizer` Stage 1 has `visualization_url == null`. The
  `mockVisualization` prop on `CommentaryPlayer` injects a placeholder URL
  on the first suggestion segment so the wiring can be demoed end-to-end.
  Echo-v3 (Saturday) replaces this with real Aleph-transformed clip URLs.

- **AvatarOverlay** renders the JvW monogram placeholder by default. When
  Runway Characters / `avatar_videos` lands (Saturday afternoon test
  trigger per the blueprint), pass `avatarVideoUrl` and the placeholder
  is replaced.

- **The commentary track is the dry-run silent WAV** until the user runs
  a live `--limit N` invocation per
  `docs/charlie-v3-friday-handoff.md`. `useCommentaryPlayer` doesn't
  care — silent WAV and live WAV both play the same way; you just don't
  hear narration on the dry-run output.

## Design notes

- **Pattern source**: the multi-track audio approach (separate audio
  element, source-video volume ducked when commentary plays) mirrors
  the StudioOS Remotion `DirectorShort` composition's
  `interview_audio_url` / `commentary_audio_url` shape. Re-implemented
  here for in-browser `<video>` + `<audio>` instead of Remotion server
  rendering.

- **Where state lives**: in `useCommentaryPlayer`. The three TSX
  components are dumb-renderers; this keeps the time-sync logic in one
  place that's easy to audit and to replace if we move to a different
  player (e.g. Vercel Blob HLS, video.js, etc.).

- **Why the API route lives in this repo's `web/api`**: so the new repo
  can stand alone — `next dev` against `web/` works without StudioOS
  present. The deploy mirror to `web/learndocumentary` is purely a
  packaging step.
