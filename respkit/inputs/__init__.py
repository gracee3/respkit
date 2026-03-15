"""Input normalization primitives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedInput:
    """Normalized, task-agnostic input for a single SDK item.

    v1 is text-only but keeps room for future media bytes and metadata.
    """

    source_id: str
    source_path: Path | None
    media_type: str
    decoded_text: str
    raw_bytes: bytes | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    extra_payload: Mapping[str, Any] = field(default_factory=dict)

    def metadata_hash(self) -> str:
        """Deterministic hash of metadata for provenance and caching keys."""

        payload = str(sorted(self.metadata.items(), key=lambda item: item[0])).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def default_text_input(source_path: Path, decoded_text: str, *, source_id: str | None = None) -> NormalizedInput:
    """Create a normalized text input with a deterministic id."""

    resolved = source_path
    sid = source_id or resolved.as_posix()
    return NormalizedInput(
        source_id=sid,
        source_path=resolved,
        media_type="text/plain",
        decoded_text=decoded_text,
    )
