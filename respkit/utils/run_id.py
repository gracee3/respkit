"""Run identifier helpers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Mapping


def make_run_id(source_id: str, source_path: str | None, metadata: Mapping[str, str] | None = None) -> str:
    """Create a deterministic-ish run id for traceability."""

    seed = f"{source_id}|{source_path or ''}|{datetime.now(timezone.utc).isoformat()}"
    if metadata:
        for key, value in sorted(metadata.items()):
            seed += f"|{key}:{value}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
