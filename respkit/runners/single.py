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
from ..utils import RunStatus, make_run_id


@dataclass
class SingleInputRunner:
    """Run one normalized item end-to-end."""

    task: TaskDefinition
    provider: LLMProvider
    artifacts_root: Path
    manifest_writer: ManifestWriter | None = None

    def _request_payload(self, prompt_text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.task.provider_model,
            "input": [Message(role="user", content=prompt_text).to_api_payload()],
            "temperature": self.task.provider_config.temperature,
        }
        if self.task.provider_config.additional_options:
            payload.update(dict(self.task.provider_config.additional_options))
        if task_options := self.task.normalized_provider_options():
            payload.update(task_options)
        return payload

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

        provider_payload = self._request_payload(prompt_text)

        if self.task.artifact_policy.include_prompt_snapshot:
            artifact_writer.write_prompt_snapshot(prompt_template.snapshot(), prompt_text)

        preflight_errors = self._validate_input_preconditions(item)
        if preflight_errors:
            provider_response = ProviderResponse(
                request_payload=provider_payload,
                raw_response={
                    "error": "input_preflight_validation_failed",
                    "errors": preflight_errors,
                    "input_length": len(item.decoded_text.strip()),
                },
                parsed_payload=None,
                usage=None,
                status_code=400,
            )
            validation_report = ValidationReport(
                valid=False,
                value={"input_length": len(item.decoded_text.strip()), "preflight_errors": preflight_errors},
                errors=[ContractViolation(path="input", message=message) for message in preflight_errors],
            )
            validated_output = None
            status = RunStatus.VALIDATION_FAILED
            if self.task.artifact_policy.include_raw_response:
                artifact_writer.write_raw_response(dict(provider_response.raw_response))
            if self.task.artifact_policy.include_provider_request_snapshot:
                artifact_writer.write_provider_request_snapshot(dict(provider_payload))
            if self.task.artifact_policy.include_parsed_response:
                artifact_writer.write_parsed_response(None)
            if self.task.artifact_policy.include_validation_report:
                artifact_writer.write_validation_report(validation_report.to_dict())
            if self.task.artifact_policy.include_validated_response:
                artifact_writer.write_validated_response(None)
            if self.task.artifact_policy.include_action_results:
                action_results = self._run_actions(
                    item,
                    provider_response,
                    validated_output,
                    validation_report,
                    run_metadata,
                    run_id,
                    run_dir,
                )
                artifact_writer.write_action_results([r.__dict__ for r in action_results])
            else:
                action_results = []

            status = self._merge_action_failures(status, action_results)

            run_metadata["status"] = status.value
            run_metadata["finished_at"] = datetime.now(timezone.utc).isoformat()

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
                    status=status.value,
                )
                self.manifest_writer.append(manifest_row)

            return ExecutionResult(
                input=item,
                task_name=self.task.name,
                run_id=run_id,
                source_id=item.source_id,
                status=status.value,
                raw_prompt=prompt_text,
                provider_request=dict(provider_payload),
                provider_response=provider_response,
                validation_report=validation_report,
                validated_output=validated_output,
                action_results=[r.__dict__ for r in action_results],
                artifacts_dir=str(run_dir),
                run_metadata=run_metadata,
            )

        provider_response = self.provider.complete(
            messages=[Message(role="user", content=prompt_text)],
            model=self.task.provider_model,
            response_model=self.task.response_model,
            config=ProviderConfig(
                temperature=self.task.provider_config.temperature,
                timeout_s=self.task.provider_config.timeout_s,
                max_retries=self.task.provider_config.max_retries,
                additional_options=self.task.normalized_provider_options(),
                enable_model_preflight=self.task.provider_config.enable_model_preflight,
            ),
        )

        if provider_response.discovered_models is not None:
            artifact_writer.write_discovered_models(provider_response.discovered_models)

        if self.task.artifact_policy.include_provider_request_snapshot:
            artifact_writer.write_provider_request_snapshot(dict(provider_response.request_payload))

        if self.task.artifact_policy.include_raw_response:
            artifact_writer.write_raw_response(dict(provider_response.raw_response))

        if self.task.artifact_policy.include_parsed_response:
            artifact_writer.write_parsed_response(dict(provider_response.parsed_payload or {}))

        validation_report, validated_output = self._validate(provider_response, item)
        status = self._compute_status(
            provider_response=provider_response,
            validation_report=validation_report,
        )

        run_metadata["status"] = status.value
        run_metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        if provider_response.discovered_models is not None:
            run_metadata["discovered_models"] = provider_response.discovered_models

        if self.task.artifact_policy.include_validation_report:
            artifact_writer.write_validation_report(validation_report.to_dict())

        if self.task.artifact_policy.include_validated_response:
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
        status = self._merge_action_failures(status, action_results)
        run_metadata["status"] = status.value

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
                status=status.value,
            )
            self.manifest_writer.append(manifest_row)

        return ExecutionResult(
            input=item,
            task_name=self.task.name,
            run_id=run_id,
            source_id=item.source_id,
            status=status.value,
            raw_prompt=prompt_text,
            provider_request=dict(provider_response.request_payload),
            provider_response=provider_response,
            validation_report=validation_report,
            validated_output=validated_output,
            action_results=[r.__dict__ for r in action_results],
            artifacts_dir=str(run_dir),
            run_metadata=run_metadata,
        )

    def _compute_status(self, *, provider_response: ProviderResponse, validation_report: ValidationReport) -> RunStatus:
        if provider_response.error_code == "preflight_model_not_found":
            return RunStatus.PREFLIGHT_MODEL_NOT_FOUND
        if provider_response.error_code in {"invalid_json", "invalid_payload"}:
            return RunStatus.PARSE_ERROR
        if provider_response.error_code is not None:
            return RunStatus.PROVIDER_ERROR
        return RunStatus.SUCCESS if validation_report.valid else RunStatus.VALIDATION_FAILED

    def _merge_action_failures(self, status: RunStatus, action_results: list[ActionResult]) -> RunStatus:
        if any(result.success is False for result in action_results):
            return RunStatus.ACTION_FAILED
        return status

    @staticmethod
    def _as_status(status: str | RunStatus) -> RunStatus:
        if isinstance(status, RunStatus):
            return status
        for value in RunStatus:
            if value == status:
                return value
        return RunStatus.PROVIDER_ERROR

    def _validate(
        self, provider_response: ProviderResponse, item: NormalizedInput
    ) -> tuple[ValidationReport, BaseModel | None]:
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

        transformed_payload = validator_report.payload
        try:
            for transform in self.task.response_transforms:
                transformed_payload = transform(dict(transformed_payload), item)
        except Exception as exc:  # noqa: BLE001
            return (
                ValidationReport(
                    valid=False,
                    value=transformed_payload,
                    errors=[ContractViolation(path="transform", message=f"Response transform failed: {exc}")],
                ),
                None,
            )

        try:
            validated = self.task.response_model.model_validate(transformed_payload)
            return ValidationReport(valid=True, value=_to_dict_model(validated), errors=[]), validated
        except ValidationError as exc:
            errors = [
                ContractViolation(
                    path=".".join(str(location) for location in violation.get("loc", ())),
                    message=violation.get("msg", "validation_error"),
                )
                for violation in exc.errors()
            ]
            return ValidationReport(valid=False, value=transformed_payload, errors=errors), None

    def _validate_input_preconditions(self, item: NormalizedInput) -> list[str]:
        if self.task.min_input_chars is None:
            return []

        text = item.decoded_text.strip()
        if len(text) < self.task.min_input_chars:
            return [
                f"Input text length {len(text)} below minimum {self.task.min_input_chars} for task '{self.task.name}'"
            ]
        return []

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
        status_enum = self._as_status(status)
        if status_enum == RunStatus.SUCCESS:
            validation_status = "passed"
        elif status_enum in {RunStatus.PROVIDER_ERROR, RunStatus.PARSE_ERROR, RunStatus.PREFLIGHT_MODEL_NOT_FOUND}:
            validation_status = "provider_error"
        else:
            validation_status = "failed"
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
            "validated_output_summary": self._summarize_payload(validated_output),
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

    @staticmethod
    def _summarize_payload(payload: BaseModel | None) -> Mapping[str, Any]:
        if payload is None:
            return {}

        value = payload.model_dump() if isinstance(payload, BaseModel) else payload
        if not isinstance(value, dict):
            return {}

        summary: dict[str, Any] = {}
        for key, field_value in value.items():
            if isinstance(field_value, (str, int, float, bool)):
                summary[key] = field_value
        return summary


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
