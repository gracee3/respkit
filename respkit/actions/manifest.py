"""Manifest append action."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..manifest.writer import ManifestWriter
from .base import Action, ActionContext, ActionResult
from ..utils import RunStatus


@dataclass
class AppendManifestAction(Action):
    """Append a manifest row as a concrete action."""

    manifest_writer: ManifestWriter
    name: str = "append_manifest"

    def execute(self, context: ActionContext) -> ActionResult:
        status = context.run_metadata.get("status", "unknown")
        status_values = {s.value for s in RunStatus}
        if status in status_values:
            normalized_status = RunStatus(status)
        else:
            normalized_status = RunStatus.PROVIDER_ERROR
        validation_status = (
            "passed"
            if normalized_status == RunStatus.SUCCESS
            else "provider_error"
            if normalized_status in {RunStatus.PROVIDER_ERROR, RunStatus.PARSE_ERROR, RunStatus.PREFLIGHT_MODEL_NOT_FOUND}
            else "failed"
        )
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_name": context.task_name,
            "source_id": context.input.source_id,
            "source_path": str(context.input.source_path) if context.input.source_path else None,
            "run_id": context.run_id,
            "item_id": context.input.source_id,
            "model": context.run_metadata.get("model"),
            "status": status,
            "validation_status": validation_status,
            "artifact_dir": str(context.artifacts_dir),
            "review_status": context.input.metadata.get("review_status"),
            "validation_errors": [v.get("message") for v in (context.validation_report.to_dict().get("errors") or [])],
            "provider_error": context.provider.error_message,
            "model_usage": context.provider.usage,
            "provider_request_available": bool(context.provider.request_payload),
        }
        self.manifest_writer.append(row)
        return ActionResult(
            name=self.name,
            success=True,
            message="Manifest row appended",
            details={"manifest_row": row},
        )
