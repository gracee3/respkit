"""Execution result models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.base import ValidationReport
from ..providers.base import ProviderResponse
from ..inputs import NormalizedInput


@dataclass
class ExecutionResult:
    """Result from a single-item execution."""

    input: NormalizedInput
    task_name: str
    run_id: str
    source_id: str
    status: str
    raw_prompt: str
    provider_request: dict[str, Any]
    provider_response: ProviderResponse
    validation_report: ValidationReport
    validated_output: Any | None
    action_results: list[dict[str, Any]]
    artifacts_dir: str
    run_metadata: dict[str, Any]
    review: "ReviewExecutionResult | None" = None


@dataclass
class ReviewExecutionResult:
    """Result from a review step for a first pass execution."""

    run_id: str
    status: str
    provider_request: dict[str, Any]
    review_output: Any | None
    prompt: str
    provider_response: ProviderResponse
    validation_report: ValidationReport
    artifacts_dir: str
