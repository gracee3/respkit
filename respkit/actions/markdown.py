"""Markdown file action."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .base import Action, ActionContext, ActionResult


@dataclass
class WriteMarkdownAction(Action):
    """Write text output into a markdown artifact."""

    filename: str
    content_builder: Callable[[ActionContext], str]
    name: str = "write_markdown"

    def execute(self, context: ActionContext) -> ActionResult:
        target = context.artifacts_dir / self.filename
        target.write_text(self.content_builder(context), encoding="utf-8")
        return ActionResult(
            name=self.name,
            success=True,
            message=f"Wrote markdown artifact to {target}",
            artifact_path=str(target),
        )
