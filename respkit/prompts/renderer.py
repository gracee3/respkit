"""Simple markdown prompt templating."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PromptTemplate:
    """Load and render markdown prompt templates with simple format interpolation."""

    template_path: Path

    def render(self, variables: Mapping[str, Any]) -> str:
        text = self.template_path.read_text(encoding="utf-8")
        try:
            return text.format(**dict(variables))
        except KeyError as exc:
            missing = exc.args[0]
            raise KeyError(f"Missing template variable: {missing}")

    @classmethod
    def from_relative_path(cls, path: str) -> "PromptTemplate":
        """Load prompt from a filesystem path."""

        return cls(Path(path))

    def snapshot(self) -> str:
        """Return original template text for artifact capture."""

        return self.template_path.read_text(encoding="utf-8")
