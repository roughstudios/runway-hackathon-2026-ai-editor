# AI Editor — Runway ML API Hackathon 2026 Submission

> *"I needed feedback that would push me. I gave up looking. So I built it."*

An AI documentary editor that watches your cut and tells you what's wrong — in my voice, with my likeness — and when it suggests a missing shot, Runway generates that shot inline so you see what was meant.

**Submission window**: May 8 09:00 ET → May 11 09:00 ET 2026 (~72 hours)
**Built by**: Jonny von Wallström (Rough Studios)
**Public demo**: https://makeadocumentary.ai/analysis-preview/[id]

---

## What this is

A documentary editor's AI collaborator. Three modalities, one character:

1. **Voice**: cloned from 13 minutes of my real interview audio. The AI editor speaks in my voice.
2. **Avatar**: generated via Runway's Characters API from my photos. Appears in the player UI during commentary.
3. **Editorial brain**: a film analyzer trained over two years on documentary patterns — hook scoring, dramaturgy, emotional beats, "what would land better with X" suggestions.

When you upload a documentary cut, the brain analyzes it, the voice narrates the analysis time-aligned to playback, the avatar provides visual identity, and **for every "this beat needs X" suggestion the editor makes, Runway generates X inline** so you see the missing footage instead of just imagining it.

## Architecture — four specialized brains in collaboration

```
[INPUT]  Documentary cut (5–12 min mp4)
                     ↓
[1] Film Analyzer (existing StudioOS infrastructure)
    Story, dramaturgy, hook scoring, emotional beats
                     ↓
[2] Suggestion Classifier (NEW, in this repo)
    Triages each note: observation vs suggestion-with-visualization
                     ↓
[3] Visual Director (NEW, multimodal)
    Reads frames around each suggestion's timecode + brand context
    Translates editorial intent → Runway-executable visual brief
                     ↓
[4] Runway (eight endpoints)
    text_to_speech in cloned voice (via ElevenLabs direct — see below)
    avatars + avatar_videos for the AI editor's visual identity
    image_to_video / text_to_video / character_performance / video_to_video
    for visualizing each suggested missing shot
                     ↓
[OUTPUT] Augmented player on /analysis-preview
    Audio commentary track + AI Jonny avatar + 8–12 inline visualizations
```

## Runway endpoints used

| Endpoint | Use |
|---|---|
| `image_to_video` (Gen 4.5) | Visualize character-anchored suggestions, conditioned on real interview frames |
| `text_to_video` (Gen 4.5) | Visualize environmental / atmospheric suggestions |
| `character_performance` (Act-Two) | Reanimate emotional peak suggestions |
| `video_to_video` (Aleph) | Visualize color/style transfer suggestions |
| `extend_video` | Stretch a successful visualization for continuity |
| `avatars` | Create the AI editor's visual identity (Jonny avatar) |
| `avatar_videos` | Animate the avatar during commentary playback |
| `uploads.create_ephemeral` | Character reference uploads, last-frame extraction |
| `tasks.retrieve` | Async polling across all endpoints |
| `organization.retrieve` | Credit budget tracking |

## Engineering choices — honest framing

This project uses three best-in-class AI tools, each for what it does best:

- **Runway**: video generation, character avatars, performance reanimation, video-to-video transformations
- **ElevenLabs (direct API)**: voice cloning + text-to-speech with custom voices. Runway's public TTS endpoint resells ElevenLabs but doesn't yet expose custom voice IDs; we go to the source. Same model, full feature access.
- **Anthropic**: editorial reasoning (Visual Director's multimodal analysis, suggestion classification, story understanding)

This is the right architecture, not a workaround. Each tool is featured for its strength.

## Pre-existing infrastructure used as private library

This repo is the **new layer** built within the May 8–11 window. It depends on:

**StudioOS** (https://github.com/roughstudios/StudioOS-v1, private) — pre-existing infrastructure including:
- The film analyzer (~14 modules, two years of training on documentary patterns)
- Media Brain footage indexing
- Brand cycle preview (story discovery from accumulated footage)
- Channel-specific brand identities (craft, jordlivet, strategiveckan, filmmaking)
- Existing analysis on Canyons 100 Miles 1.3 (the demo input)

The hackathon submission is the AI-editor commentary + visualization layer that wraps this brain in an experience.

## Where this is going

Today's release is **step one** of a multi-year arc toward interactive documentary:

1. **Today (this submission)**: AI editor reads notes in my voice, time-aligned to your cut, with inline visualizations of suggestions
2. **Next**: AI editor converses with you — you ask "why is the hook weak?" and it answers in my voice
3. **Later**: AI editor as collaborator inside finished documentaries — you ask about choices, hear reasoning
4. **Future**: AI editor as co-director in adaptive narratives shaped by your choices, VR-captured footage. Runway just shipped `realtime_sessions`. We'll meet you there.

---

## Repo structure

```
ai_editor/                — new code, written in window
  commentary_synthesizer.py   → analyzer report → time-aligned TTS commentary
  visual_director.py          → multimodal LLM: editorial note → visual brief
  visualization_pipeline.py   → executes visual briefs via Runway
  suggestion_classifier.py    → triages observations vs visualization-needing suggestions
  runway_client.py            → thin wrapper / pinned client config
  elevenlabs_voice.py         → voice cloning + TTS via ElevenLabs direct
  avatar_creator.py           → Runway Characters API integration
  player_state.py             → time-aligned commentary playback state

web/                      — analysis-preview player UI extensions
tests/                    — verification + smoke tests
docs/                     — submission notes, architecture
```

## Running locally

```bash
# Install with StudioOS-v1 as editable dependency
pip install -e .
pip install -e ../StudioOS-v1   # private dependency

# Run end-to-end on the Canyons cut
python -m ai_editor.cli analyze --report data/film_analyzer/reports/canyons_100_miles_1_3.json
```

## License

(TBD post-hackathon)

---

_Built between May 8 09:00 ET and May 11 09:00 ET 2026 for the Runway ML API Hackathon. Jonny von Wallström — Rough Studios._
