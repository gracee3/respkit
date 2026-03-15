"""Action protocol and execution results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

from ..inputs import NormalizedInput
from ..contracts.base import ValidationReport
from ..providers.base import ProviderResponse

if TYPE_CHECKING:  # pragma: no cover
    from ..tasks.result import ExecutionResult


@dataclass
class ActionContext:
    """Context passed into callbacks."""

    task_name: str
    run_id: str
    input: NormalizedInput
    provider: ProviderResponse
    validated_output: Any
    validation_report: ValidationReport
    artifacts_dir: Path
    run_metadata: dict[str, Any]
    run_result: "ExecutionResult | None" = None


@dataclass
class ActionResult:
    """Simple deterministic action result."""

    name: str
    success: bool
    message: str
    artifact_path: str | None = None
    details: dict[str, Any] | None = None


class Action:
    """Action callback interface."""

    name: str = "action"

    def execute(self, context: ActionContext) -> ActionResult:
        raise NotImplementedError
