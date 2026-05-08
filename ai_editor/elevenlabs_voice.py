"""ElevenLabs direct-API voice synthesizer.

The seam where Sunday's recording swaps from the Runway preset Vincent
to the cloned-Jonny ElevenLabs voice. Drop-in for any synthesizer with
a (text, output_path) -> Path signature.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

DEFAULT_MODEL_ID = "eleven_multilingual_v2"
ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


@dataclass
class ElevenLabsVoice:
    voice_id: Optional[str] = None
    model_id: str = DEFAULT_MODEL_ID
    stability: float = 0.45
    similarity_boost: float = 0.85
    style: float = 0.0

    def __post_init__(self) -> None:
        load_dotenv()
        self._api_key = os.environ.get("ELEVEN_LABS_API_KEY")
        self._voice_id = self.voice_id or os.environ.get("ELEVEN_LABS_VOICE_ID")

    @staticmethod
    def is_available() -> bool:
        load_dotenv()
        return bool(os.environ.get("ELEVEN_LABS_API_KEY"))

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not self._api_key:
            raise RuntimeError("ELEVEN_LABS_API_KEY not set in environment")
        if not self._voice_id:
            raise RuntimeError("ELEVEN_LABS_VOICE_ID not set in environment")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
                "style": self.style,
            },
        }
        with httpx.Client(timeout=60) as client:
            response = client.post(
                ENDPOINT.format(voice_id=self._voice_id),
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            out.write_bytes(response.content)
        return out
