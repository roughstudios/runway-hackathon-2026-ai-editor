# `analysis-preview/page.tsx` — minimal patch for HeroPlayer integration

Morning Jonny: drop this into the Vercel-deployed `analysis-preview`
page. It's two changes — an import and a single component placement
above the existing UI. Nothing in the existing page is removed or
modified.

## 1. Add import (top of `page.tsx`, with the other imports)

```tsx
import HeroPlayer from "./_hero/HeroPlayer";
```

## 2. Render `<HeroPlayer>` at the top of the page render

Find wherever the existing `<ExportAllBar>` / NoteList / story-axis
chrome starts. Just **before** that block, insert:

```tsx
<HeroPlayer reportId={filmKey} />
```

Where `filmKey` is whatever the page already uses to identify the
report (it's almost certainly named that way already — search for
`filmKey` or `params.id` in the file).

If the existing page renders different per-tab views (e.g.
`activeTab === "analysis"`), mount the hero on the Analysis / Notes
tab — that's the one judges hit.

## 3. Layout sanity

`HeroPlayer` is `max-width: 1280px` and self-centers; it works inside
any container that allows full-width children. If the surrounding
page constrains width, set its parent's `max-width` to at least 1280
or wrap with `<div style={{ width: "100%" }}>`.

The existing analytical components (`EmotionalCurve`, `StoryAxis`,
`MarkedUpTranscript`, `NoteItem`) keep their layout untouched — they
render below the hero and the page scrolls naturally.

## 4. Test locally before pushing

```powershell
cd R:\--CODE--\StudioOS-v1\web\learndocumentary
npm run dev
# open http://localhost:3000/analysis-preview/canyons_100_miles_1_3
```

You should see the cinematic hero on top (auto-muted, click to
unmute), then the existing analysis below.

If the hero shows "loading hero…" indefinitely, either:

- the API route `/api/hero/canyons_100_miles_1_3` is missing → mirror
  the route file from `web/api/hero/[reportId]/route.ts` (handoff
  step 2).
- the asset isn't synced → copy `output/hero/canyons_100_miles_1_3/`
  to `public/hero/canyons_100_miles_1_3/` (handoff step 4 option A).

## 5. Push

```powershell
git add `
  web/learndocumentary/src/app/analysis-preview/_hero `
  web/learndocumentary/src/app/api/hero `
  web/learndocumentary/public/hero/canyons_100_miles_1_3 `
  web/learndocumentary/src/app/analysis-preview/page.tsx
git commit -m "feat(analysis-preview): HeroPlayer above analysis (India-v3)"
git push
& "R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe" `
    R:\--CODE--\StudioOS-v1\scripts\deploy_wait.py
```

Open https://makeadocumentary.ai/analysis-preview/canyons_100_miles_1_3
and verify.
