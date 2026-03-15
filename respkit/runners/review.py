"""Optional review runner that runs a review task after first pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..inputs import NormalizedInput
from ..tasks.definition import ReviewPolicy
from ..tasks.result import ExecutionResult, ReviewExecutionResult
from .single import SingleInputRunner
from ..utils import RunStatus


@dataclass
class ReviewRunner:
    """Run an explicit review pass for an already executed first pass."""

    def run(
        self,
        first_result: ExecutionResult,
        original_item: NormalizedInput,
        policy: ReviewPolicy,
        single_runner: SingleInputRunner,
    ) -> ReviewExecutionResult:
        first_output = first_result.validated_output
        if isinstance(first_output, dict):
            first_payload = dict(first_output)
        else:
            first_payload = getattr(first_output, "model_dump", lambda: {})()

        extra = policy.context_builder(original_item, first_payload)
        review_metadata = dict(original_item.metadata)
        review_metadata["first_pass_output"] = first_payload
        review_metadata["review_status"] = "requested"
        review_metadata.update(extra)

        review_input = NormalizedInput(
            source_id=f"{original_item.source_id}#review",
            source_path=original_item.source_path,
            media_type=original_item.media_type,
            decoded_text=original_item.decoded_text,
            raw_bytes=original_item.raw_bytes,
            metadata=review_metadata,
            extra_payload=original_item.extra_payload,
        )

        review_task_result = single_runner.run(review_input)
        try:
            status = RunStatus(review_task_result.status)
        except ValueError:
            status = RunStatus.PROVIDER_ERROR
        review_output = review_task_result.validated_output
        review_payload: Mapping[str, Any] = {}
        if isinstance(review_output, dict):
            review_payload = dict(review_output)
        elif review_output is not None and hasattr(review_output, "model_dump"):
            review_payload = review_output.model_dump()
        if status == RunStatus.SUCCESS:
            decision = review_payload.get("decision", "").lower()
            if decision in {"fail", "uncertain"}:
                status = RunStatus.REVIEW_FAILED

        return ReviewExecutionResult(
            run_id=review_task_result.run_id,
            status=status.value,
            provider_request=review_task_result.provider_request,
            review_output=review_task_result.validated_output,
            prompt=review_task_result.raw_prompt,
            provider_response=review_task_result.provider_response,
            validation_report=review_task_result.validation_report,
            artifacts_dir=review_task_result.artifacts_dir,
        )
