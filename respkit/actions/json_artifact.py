"""JSON artifact action."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .base import Action, ActionContext, ActionResult


@dataclass
class WriteJSONArtifactAction(Action):
    """Write an arbitrary JSON payload as an artifact file."""

    filename: str
    payload_builder: Callable[[ActionContext], Any]
    name: str = "write_json_artifact"

    def execute(self, context: ActionContext) -> ActionResult:
        target = context.artifacts_dir / self.filename
        payload = self.payload_builder(context)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ActionResult(
            name=self.name,
            success=True,
            message=f"Wrote JSON artifact to {target}",
            artifact_path=str(target),
        )
