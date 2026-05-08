"""ElevenLabs direct-API voice synthesizer.

STAGE 1 STUB. Sunday's recording session swaps from the Runway preset
Vincent to the cloned-Jonny ElevenLabs voice; this module is the seam
where that swap lands.

Public surface mirrors StudioOS's ElevenLabsBackend
(R:/--CODE--/StudioOS-v1/agents/voice_over/backends/elevenlabs_backend.py)
so the swap in commentary_synthesizer is a one-line change: pass an
instance of this class as the synthesizer instead of RunwayClient.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_MODEL_ID = "eleven_multilingual_v2"


@dataclass
class ElevenLabsVoice:
    voice_id: Optional[str] = None
    model_id: str = DEFAULT_MODEL_ID
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0

    def __post_init__(self) -> None:
        self._api_key = os.environ.get("ELEVEN_LABS_API_KEY")
        self._voice_id = self.voice_id or os.environ.get("ELEVEN_LABS_VOICE_ID")

    @staticmethod
    def is_available() -> bool:
        return bool(os.environ.get("ELEVEN_LABS_API_KEY"))

    def synthesize(self, text: str, output_path: Path) -> Path:
        raise NotImplementedError(
            "ElevenLabs direct synthesis is wired Saturday/Sunday. "
            "For Friday's audio commentary build, use the Runway preset path "
            "via ai_editor.runway_client.RunwayClient.text_to_speech()."
        )
