"""Filesystem convenience functions."""

from __future__ import annotations

from pathlib import Path


def list_text_files(directory: Path) -> list[Path]:
    return [path for path in sorted(directory.iterdir()) if path.is_file() and path.suffix.lower() in {".txt", ".md", ".text"}]


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")
