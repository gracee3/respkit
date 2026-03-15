"""Single-item execution runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from ..actions.base import ActionContext, ActionResult
from ..contracts import ContractViolation, ValidationReport
from ..inputs import NormalizedInput
from ..providers import LLMProvider, ProviderConfig, ProviderResponse
from ..prompts import PromptTemplate
from ..tasks.definition import TaskDefinition
from ..tasks.result import ExecutionResult
from ..validators.base import run_validators as run_validators
from ..artifacts import ArtifactWriter
from ..manifest import ManifestWriter
from ..tasks.message import Message
from ..utils import make_run_id


@dataclass
class SingleInputRunner:
    """Run one normalized item end-to-end."""

    task: TaskDefinition
    provider: LLMProvider
    artifacts_root: Path
    manifest_writer: ManifestWriter | None = None

    def run(self, item: NormalizedInput) -> ExecutionResult:
        run_id = make_run_id(item.source_id, str(item.source_path) if item.source_path else None, dict(item.metadata))
        run_dir = self.artifacts_root / self.task.name / run_id
        artifact_writer = ArtifactWriter(run_dir)

        run_metadata = {
            "task_name": self.task.name,
            "run_id": run_id,
            "source_id": item.source_id,
            "model": self.task.provider_model,
            "source_path": str(item.source_path) if item.source_path else None,
            "metadata_hash": item.metadata_hash(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        prompt_template = PromptTemplate.from_relative_path(str(self.task.prompt_template_path))
        prompt_variables = dict(self.task.prompt_context_builder(item))
        prompt_variables.setdefault("text", item.decoded_text)
        prompt_text = prompt_template.render(prompt_variables)

        if self.task.artifact_policy.include_prompt_snapshot:
            artifact_writer.write_prompt_snapshot(prompt_template.snapshot(), prompt_text)

        provider_response = self.provider.complete(
            messages=[Message(role="user", content=prompt_text)],
            model=self.task.provider_model,
            response_model=self.task.response_model,
            config=ProviderConfig(
                temperature=self.task.provider_config.temperature,
                timeout_s=self.task.provider_config.timeout_s,
                max_retries=self.task.provider_config.max_retries,
                additional_options=self.task.normalized_provider_options(),
            ),
        )

        if self.task.artifact_policy.include_provider_request_snapshot:
            artifact_writer.write_provider_request_snapshot(dict(provider_response.request_payload))

        if self.task.artifact_policy.include_raw_response:
            artifact_writer.write_raw_response(dict(provider_response.raw_response))

        if self.task.artifact_policy.include_parsed_response and provider_response.parsed_payload is not None:
            artifact_writer.write_parsed_response(provider_response.parsed_payload)

        validation_report, validated_output = self._validate(provider_response)
        status = (
            "provider_error"
            if provider_response.error_message is not None
            else ("success" if validation_report.valid else "validation_failed")
        )

        run_metadata["status"] = status
        run_metadata["finished_at"] = datetime.now(timezone.utc).isoformat()

        if self.task.artifact_policy.include_validation_report:
            artifact_writer.write_validation_report(validation_report.to_dict())

        if validated_output is not None and self.task.artifact_policy.include_validated_response:
            artifact_writer.write_validated_response(_to_mapping(validated_output))

        action_results = self._run_actions(
            item,
            provider_response,
            validated_output,
            validation_report,
            run_metadata,
            run_id,
            run_dir,
        )

        if self.task.artifact_policy.include_action_results:
            artifact_writer.write_action_results([r.__dict__ for r in action_results])

        if self.task.artifact_policy.include_run_metadata:
            artifact_writer.write_run_metadata(run_metadata)

        if self.manifest_writer is not None:
            manifest_row = self._build_manifest_row(
                run_id=run_id,
                run_dir=run_dir,
                item=item,
                validated_output=validated_output,
                validation_report=validation_report,
                provider_response=provider_response,
                status=status,
            )
            self.manifest_writer.append(manifest_row)

        return ExecutionResult(
            input=item,
            task_name=self.task.name,
            run_id=run_id,
            source_id=item.source_id,
            status=status,
            raw_prompt=prompt_text,
            provider_request=dict(provider_response.request_payload),
            provider_response=provider_response,
            validation_report=validation_report,
            validated_output=validated_output,
            action_results=[r.__dict__ for r in action_results],
            artifacts_dir=str(run_dir),
            run_metadata=run_metadata,
        )

    def _validate(self, provider_response: ProviderResponse) -> tuple[ValidationReport, BaseModel | None]:
        if provider_response.error_code is not None or provider_response.parsed_payload is None:
            errors = [
                ContractViolation(
                    path="provider",
                    message=provider_response.error_message or "Unable to parse structured payload",
                )
            ]
            return ValidationReport(valid=False, value=None, errors=errors), None

        try:
            raw_payload = dict(provider_response.parsed_payload)
        except TypeError as exc:
            return (
                ValidationReport(
                    valid=False,
                    value=None,
                    errors=[ContractViolation(path="provider", message=f"Provider payload was not a JSON object: {exc}")],
                ),
                None,
            )

        validator_report = run_validators(raw_payload, list(self.task.validators))
        if validator_report.errors:
            return (
                ValidationReport(
                    valid=False,
                    value=validator_report.payload,
                    errors=[ContractViolation(path="validator", message=message) for message in validator_report.errors],
                ),
                None,
            )

        try:
            validated = self.task.response_model.model_validate(validator_report.payload)
            return ValidationReport(valid=True, value=_to_dict_model(validated), errors=[]), validated
        except ValidationError as exc:
            errors = [
                ContractViolation(
                    path=".".join(str(location) for location in violation.get("loc", ())),
                    message=violation.get("msg", "validation_error"),
                )
                for violation in exc.errors()
            ]
            return ValidationReport(valid=False, value=validator_report.payload, errors=errors), None

    def _build_manifest_row(
        self,
        *,
        run_id: str,
        run_dir: Path,
        item: NormalizedInput,
        validated_output: BaseModel | None,
        validation_report: ValidationReport,
        provider_response: ProviderResponse,
        status: str,
    ) -> dict[str, Any]:
        validation_status = "passed" if status == "success" else ("provider_error" if provider_response.error_message else "failed")
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_name": self.task.name,
            "source_id": item.source_id,
            "source_path": str(item.source_path) if item.source_path else None,
            "run_id": run_id,
            "item_id": item.source_id,
            "model": self.task.provider_model,
            "status": status,
            "validation_status": validation_status,
            "artifact_dir": str(run_dir),
            "review_status": item.metadata.get("review_status"),
            "validation_errors": [v.message for v in (validation_report.errors or [])],
            "provider_status_code": provider_response.status_code,
            "provider_error": provider_response.error_message,
            "validation_passed": validation_report.valid,
            "has_validated_output": validated_output is not None,
        }

    def _run_actions(
        self,
        item: NormalizedInput,
        provider_response: ProviderResponse,
        validated_output: BaseModel | None,
        validation_report: ValidationReport,
        run_metadata: dict[str, Any],
        run_id: str,
        run_dir: Path,
    ) -> list[ActionResult]:
        results: list[ActionResult] = []
        for action in self.task.actions:
            try:
                result = action.execute(
                    ActionContext(
                        task_name=self.task.name,
                        run_id=run_id,
                        input=item,
                        provider=provider_response,
                        validated_output=_to_mapping(validated_output),
                        validation_report=validation_report,
                        artifacts_dir=run_dir,
                        run_metadata=run_metadata,
                    )
                )
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    ActionResult(
                        name=getattr(action, "name", action.__class__.__name__),
                        success=False,
                        message=f"Action failed: {exc}",
                    )
                )
        return results


def _to_dict_model(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}
