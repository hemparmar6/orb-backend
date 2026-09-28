"""Provider interface + response DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AIResponse:
    content: Dict[str, Any]
    text: str = ""
    provider: str = ""
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    cached: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "meta": self.meta,
        }


class AIProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def generate(
        self, prompt_name: str, prompt_version: str, payload: Dict[str, Any]
    ) -> AIResponse:
        raise NotImplementedError
