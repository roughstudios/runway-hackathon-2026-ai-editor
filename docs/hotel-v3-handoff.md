# Hotel-v3 — Stage 5: Resolve Rough-Cut Bundler

**Status:** Shipped (tests green). Awaiting first live Resolve import on Jonny's box.

## What Hotel-v3 does

Stage 5 is the endgame: take the four upstream stages' manifests
(commentary, visualizations, avatar, SFX) and package them into a
self-contained directory that Resolve can import via a single
double-click on a Scripts-menu Lua entry.

The result is a structured rough cut on Jonny's timeline:

| Track | Role | Source |
|---|---|---|
| **V1** | Source video | The documentary master cut (full duration) |
| **V2** | Visualization VFX | Charlie-v3 segments → Echo-v3 gen4_aleph transforms, placed at `start_seconds` |
| **V3** | AI Jonny talking head | Foxtrot-v3 avatar segments, placed at the same `start_seconds` |
| **A1** | Source dialogue | The audio side of source.mov |
| **A2** | Commentary track | Charlie-v3's time-aligned narration WAV |
| **A3** | Atmosphere SFX | Golf-v3 per-scene ambience (loop=true → tiled to scene length) |
| **A4** | Hard FX | Golf-v3 one-shots placed at `scene_start + start_offset_in_scene` |

## Files added

| Path | What it is |
|---|---|
| `ai_editor/resolve_exporter.py` | `bundle_for_resolve(report_id, source_video_path)` — reads the four upstream manifests, copies media into the bundle, writes the unified manifest + Lua artifacts. |
| `ai_editor/resolve_lua_template.py` | Lua-data serializer (`render_manifest_lua`) + Resolve Scripts-menu importer template (`render_importer_lua`). |
| `ai_editor/cli.py` | Adds `export-resolve` subcommand. |
| `tests/test_resolve_exporter.py` | 9 dry-run tests: Lua escaping, manifest discovery, full-bundle layout, missing-manifest tolerance, no-copy mode, override paths. |

## Bundle layout

```
output/resolve_bundle/<report_id>/
├── source.mov                       (or whatever extension the master cut has)
├── commentary_track.wav             (Stage 1's track_path, copied)
├── visualizations/
│   └── <comment_id>.mp4             (one per Stage 2 transformed_video)
├── avatar/
│   └── seg_<NN>.mp4                 (one per Stage 3 segment)
├── sfx/
│   ├── scene_<NN>_atmos.mp3         (Stage 4 atmosphere)
│   └── scene_<NN>_fx_<NN>.mp3       (Stage 4 hard FX)
├── manifest.json                    ← canonical artifact (track-grouped)
├── manifest.lua                     ← same data as a Lua return-table
└── StudioOS_Import_AIEdit.lua       ← Resolve Scripts-menu importer
```

## Unified manifest schema

`manifest.json` is a track-grouped contract — anything that wants to
re-implement the importer (e.g. an FCPXML emitter or a different NLE
target) reads from the same shape:

```json
{
  "report_id": "canyons_100_miles_1_3",
  "source_video": "source.mov",
  "fps": 25.0,
  "total_duration_sec": 1114.93,
  "tracks": {
    "V1": [{"path": "source.mov", "in": 0, "out": 1114.93, "timeline_in": 0, "label": "source"}],
    "V2": [{"path": "visualizations/emotional.0.mp4", "duration": 5.0, "timeline_in": 31.67, "label": "viz: emotional.0"}],
    "V3": [{"path": "avatar/seg_00.mp4", "duration": 8.86, "timeline_in": 31.67, "label": "avatar: emotional.0"}],
    "A1": [{"path": "source.mov", "in": 0, "out": 1114.93, "timeline_in": 0, "label": "source dialogue"}],
    "A2": [{"path": "commentary_track.wav", "duration": 1114.93, "timeline_in": 0, "label": "commentary"}],
    "A3": [{"path": "sfx/scene_00_atmos.mp3", "duration": 30.0, "timeline_in": 0.0, "loop": true, "tile_to_seconds": 75.0, "label": "atmos: scene_00"}],
    "A4": [{"path": "sfx/scene_05_fx_01.mp3", "duration": 1.8, "timeline_in": 590.3, "label": "fx: scene_05 +5.3s"}]
  },
  "stats": {
    "n_visualizations": 6,
    "n_avatar_segments": 12,
    "n_atmospheres": 6,
    "n_hard_fx": 0,
    "missing_manifests": []
  },
  "lua_script": "StudioOS_Import_AIEdit.lua",
  "source_manifests": { ... }
}
```

### A3 atmosphere tiling

Runway's `sound_effect.create` caps a single render at 30s. Stage 4
(audio_pipeline.py) handles long scenes by rendering a 30s clip with
`loop=True`. Hotel-v3 mirrors that in the manifest:

- `duration` — the rendered clip length on disk (`min(in_scene_dur, 30)`).
- `tile_to_seconds` — the in-scene playback length to fill (the full scene length).
- `loop` — `True` when tiling is needed.

The Lua importer reads these and `mediaPool:AppendToTimeline`s the
clip repeatedly until the tile target is reached, with the trailing
copy trimmed to fit exactly.

### Track index conventions

The Lua importer pins:

| Track | `mediaType` | `trackIndex` |
|---|---|---|
| V1 / V2 / V3 | 1 (video) | 1 / 2 / 3 |
| A1 / A2 / A3 / A4 | 2 (audio) | 1 / 2 / 3 / 4 |

Default Resolve timelines are 1V/1A — the importer calls
`timeline:AddTrack` until V3/A4 exist before it starts appending.

## How to run

```powershell
# 1. Build the bundle (after Stages 1-4 have run for this report_id).
python -m ai_editor.cli export-resolve `
    --report canyons_100_miles_1_3 `
    --source "R:/--CODE--/StudioOS-v1/data/learndocumentary/uploads/14/Canyons - 100 Miles 1.3.mov"

# 2. Install the Resolve Scripts-menu entry (one-time, then on changes).
python R:/--CODE--/StudioOS-v1/scripts/install_resolve_scripts.py
```

`install_resolve_scripts.py` copies every `*.lua`/`*.py` under
`StudioOS-v1/scripts/resolve_scripts/` to Resolve's Utility scripts
directory. The Hotel-v3 Lua lives inside the per-report bundle, **not**
in `scripts/resolve_scripts/`, so we ship it via env var instead:

```powershell
# 3. Point Resolve at the bundle, then run from Workspace > Scripts.
$env:STUDIOOS_AIEDIT_BUNDLE = "R:/runway-hackathon-2026-ai-editor/output/resolve_bundle/canyons_100_miles_1_3"
```

Or — simpler — copy the generated `StudioOS_Import_AIEdit.lua` from the
bundle into Resolve's Utility scripts dir manually before running it.
The bundle path is hardcoded into the Lua at generation time so the env
var is only needed when iterating on multiple bundles.

## Tests

All 9 unit tests pass (run via the StudioOS .venv since pytest hangs in
the project's own .venv — same workaround Charlie/Echo/Foxtrot/Golf use):

```powershell
R:\--CODE--\StudioOS-v1\.venv\Scripts\python.exe tests/test_resolve_exporter.py
```

Coverage:

- Lua serialiser escapes strings + handles the `in` keyword via string-key syntax.
- `render_importer_lua` emits all required Resolve API calls (`bmd.scriptapp`, `ImportMedia`, `CreateEmptyTimeline`, `AppendToTimeline`, `AddTrack`, `AddSubFolder`).
- `discover_manifests` finds the four upstream manifests by convention; missing ones are recorded in `stats.missing_manifests` instead of crashing.
- Full-layout test exercises the long-scene atmosphere tiling, short-scene no-tile, hard-FX offset placement, and the brief-only-visualization drop.
- "All four manifests missing" test confirms the bundle still produces a runnable V1+A1 source-only timeline.
- `--no-copy` mode used in tests (writes empty placeholder asset files) and as a debugging aid.
- `--commentary` / etc. explicit-path overrides win over auto-discovery.

## What's *not* in scope

- **No Fusion comp generation.** Visualizations land as gen4_aleph MP4s; no fusion overlays, masks, or speed ramps.
- **No grading / colour matching.** The bundle drops clips raw onto the timeline.
- **No cross-fades / dissolves.** Every cut is a hard cut.
- **No Resolve project creation.** The importer assumes a project is already open. If `pm:GetCurrentProject()` is nil, it errors out with a clear message.
- **No source video re-import detection.** Each run re-imports — Resolve will create duplicate Media Pool entries on a second run. The subfolder is named `AI_Editor_<report_id>` so the duplicates at least co-locate; clean up by deleting the subfolder before re-running.

## Known footguns / decisions

1. **Source copy is unconditional by default.** A 2GB master cut copies
   to the bundle dir on every run. Hardlink would be faster on the same
   volume but cross-volume copy is robust everywhere. Use `--no-copy`
   for plumbing tests; live runs should accept the copy cost.
2. **The Lua importer hardcodes the bundle dir.** Generating the Lua
   bakes in the absolute path at generation time. Moving the bundle
   later breaks the import unless `STUDIOOS_AIEDIT_BUNDLE` is set.
3. **Track count assumption.** The default Resolve timeline has 1V/1A.
   We `AddTrack` up to 3V/4A. If the project's preferred default has
   more tracks, V2/V3/A2-A4 still land on indices 2/3/2/3/4 — that's
   fine, but the timeline may have empty trailing tracks. Visually
   harmless.
4. **Tiling tolerance.** The atmosphere tile loop terminates when
   `i * clip_dur >= target - 0.001`. Ten milliseconds of slack absorbs
   floating-point drift; trailing copies are trimmed to land exactly on
   `target`.
5. **`'in'` is a Lua keyword.** Manifest dict keys go through
   `_lua_key()`, which renders `in` as `["in"]` in the table literal.
   Other JSON-ish keys (e.g. `out`, `duration`, `timeline_in`) are
   regular identifiers.
6. **Unrelated test suites have pre-existing failures.** Running
   `tests/test_avatar_pipeline.py` after a prior run hits a stale
   cache; `tests/test_visualization_pipeline.py` lacks the
   `sys.path.insert` shim other Stage tests have. Both are out of
   scope for Hotel-v3 (the brief says "DO NOT TOUCH: Other ai_editor
   modules") and existed before this commit.
