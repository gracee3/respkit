"""Append-only JSONL manifest implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from dataclasses import field


@dataclass
class ManifestWriter:
    """Write one JSON object per line, never truncate."""

    manifest_path: Path
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self.manifest_path.touch()

    def append(self, row: dict[str, Any]) -> None:
        with self._lock:
            with self.manifest_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
