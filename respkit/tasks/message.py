"""Prompt message primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Message:
    """Single message to send through provider adapters."""

    role: str
    content: str

    def to_api_payload(self) -> Mapping[str, Any]:
        return {"role": self.role, "content": self.content}
