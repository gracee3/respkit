from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from respkit.actions import WriteMarkdownAction
from respkit.artifacts import ArtifactWriter
from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.providers import LLMProvider, ProviderConfig, ProviderResponse
from respkit.runners import DirectoryBatchRunner, SingleInputRunner
from respkit.tasks import TaskDefinition
from respkit.validators import EnumCaseNormalizer, FillDefaultsValidator, TrimWhitespaceValidator


class RenameProposal(BaseModel):
    kind: str
    actor: str
    slug: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str


class _ScriptedLLMProvider(LLMProvider):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[tuple[str, str]], str, type[BaseModel] | None, ProviderConfig | None]] = []

    def complete(
        self,
        *,
        messages: list[Any],
        model: str,
        response_model: type[BaseModel] | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        response = self._responses.pop(0)
        self.calls.append(([ (message.role, message.content) for message in messages], model, response_model, config))

        return ProviderResponse(
            request_payload={
                "model": model,
                "input": [message.to_api_payload() for message in messages],
                "temperature": config.temperature if config is not None else 0.0,
                "response_format": response.get("response_format"),
            },
            raw_response=response.get("raw_response", {}),
            parsed_payload=response.get("parsed_payload"),
            usage=response.get("usage"),
            status_code=response.get("status_code"),
            error_code=response.get("error_code"),
            error_message=response.get("error_message"),
        )


def _proposal_task(tmp_path: Path, *, min_input_chars: int | None = None, action: object | None = None) -> TaskDefinition:
    prompt = tmp_path / "proposal.md"
    prompt.write_text("Run rename proposal", encoding="utf-8")

    actions = () if action is None else (action,)
    validators = (
        TrimWhitespaceValidator(),
        EnumCaseNormalizer(field_values={"kind": ["invoice", "legal", "other"]}),
        FillDefaultsValidator(defaults={"notes": ""}),
    )
    return TaskDefinition(
        name="proposal_hardening",
        description="proposal hardening",
        prompt_template_path=prompt,
        response_model=RenameProposal,
        provider_model="gpt-oss-20b",
        min_input_chars=min_input_chars,
        validators=validators,
        actions=actions,
        provider_config=ProviderConfig(),
    )


def test_empty_input_is_stable_failure(tmp_path):
    task = _proposal_task(tmp_path, min_input_chars=25)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {"kind": "invoice", "actor": "ops", "slug": "x", "confidence": 0.99, "notes": ""},
                "raw_response": {"output": []},
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )

    input_item = NormalizedInput(
        source_id="near-empty",
        source_path=tmp_path / "near-empty.txt",
        media_type="text/plain",
        decoded_text="  \n",
    )

    result = runner.run(input_item)

    assert result.status == "validation_failed"
    assert provider.calls == []
    assert result.validation_report.valid is False


def test_invalid_structured_output_is_captured_as_validation_failure(tmp_path):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": None,
                "raw_response": {
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}]
                },
                "error_code": "invalid_payload",
                "error_message": "Could not parse JSON content",
                "status_code": 200,
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )

    input_file = tmp_path / "bad.txt"
    input_file.write_text("some text", encoding="utf-8")
    result = runner.run(
        NormalizedInput(
            source_id="bad",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="some text",
        )
    )

    assert result.status == "provider_error"
    assert result.validation_report.valid is False
    assert any("Could not parse JSON" in e.message for e in result.validation_report.errors or [])


def test_provider_success_artifact_files_are_complete(tmp_path):
    output = {
        "kind": "invoice",
        "actor": "Legal Ops",
        "slug": "2024-invoice",
        "confidence": 0.91,
        "notes": "ok",
    }
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": output,
                "raw_response": {
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}],
                    "usage": {"input_tokens": 3, "output_tokens": 8},
                },
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "clean.txt"
    input_file.write_text("Invoice line item", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="clean",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="Invoice line item",
        )
    )

    artifact_dir = Path(result.artifacts_dir)
    expected = {
        ArtifactWriter.PROMPT_TEMPLATE_FILE,
        ArtifactWriter.PROMPT_RENDERED_FILE,
        ArtifactWriter.PROVIDER_REQUEST_FILE,
        ArtifactWriter.RAW_RESPONSE_FILE,
        ArtifactWriter.PARSED_RESPONSE_FILE,
        ArtifactWriter.VALIDATION_REPORT_FILE,
        ArtifactWriter.VALIDATED_RESPONSE_FILE,
        ArtifactWriter.ACTION_RESULTS_FILE,
        ArtifactWriter.RUN_METADATA_FILE,
    }
    files = {path.name for path in artifact_dir.iterdir()}
    assert expected.issubset(files)


def test_provider_error_writes_artifacts(tmp_path):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": None,
                "raw_response": {"error": "transport_down"},
                "error_code": "request_failed",
                "error_message": "provider unavailable",
                "status_code": 503,
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )
    input_file = tmp_path / "fail.txt"
    input_file.write_text("needs handling", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="fail",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="needs handling",
        )
    )

    artifact_dir = Path(result.artifacts_dir)
    for filename in (
        ArtifactWriter.PROVIDER_REQUEST_FILE,
        ArtifactWriter.RAW_RESPONSE_FILE,
        ArtifactWriter.PARSED_RESPONSE_FILE,
        ArtifactWriter.VALIDATION_REPORT_FILE,
        ArtifactWriter.VALIDATED_RESPONSE_FILE,
        ArtifactWriter.RUN_METADATA_FILE,
        ArtifactWriter.ACTION_RESULTS_FILE,
    ):
        assert (artifact_dir / filename).exists()

    assert result.status == "provider_error"
    manifest_rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(manifest_rows) == 1
    assert manifest_rows[0]["status"] == "provider_error"


def test_batch_run_with_mixed_success_and_failure_inputs(tmp_path):
    input_root = Path("tests/fixtures/rename_inputs")
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()

    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {"kind": "invoice", "actor": "Alice", "slug": "a1", "confidence": 0.9, "notes": "ok"},
                "raw_response": {"output": [{"type": "message", "content": []}]},
                "status_code": 200,
            },
            {
                "parsed_payload": {"kind": "invalid", "actor": "Bob", "slug": "bad", "confidence": 1.0, "notes": "bad kind"},
                "raw_response": {"output": []},
                "status_code": 200,
            },
            {
                "parsed_payload": None,
                "raw_response": {"error": "transient"},
                "error_code": "request_failed",
                "error_message": "timeout",
                "status_code": 503,
            },
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )

    for path in ["clean_easy.txt", "ambiguous_actor.txt", "near_empty.txt"]:
        (input_dir / path).write_text((input_root / path).read_text(encoding="utf-8"), encoding="utf-8")

    results = DirectoryBatchRunner(single_runner=runner).run(input_dir)
    assert len(results) == 3
    assert [r.status for r in results] == ["success", "validation_failed", "provider_error"]

    manifest_lines = [line for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(manifest_lines) == 3
    statuses = [json.loads(row)["status"] for row in manifest_lines]
    assert statuses == ["success", "validation_failed", "provider_error"]


def _write_task_markdown(context):
    output = context.validated_output
    return f"status={context.run_metadata.get('status')}\n"


def test_action_execution_is_deterministic_and_logged(tmp_path):
    action = WriteMarkdownAction(filename="proposal_row.md", content_builder=_write_task_markdown)
    task = _proposal_task(tmp_path, action=action)
    runner = SingleInputRunner(
        task=task,
        provider=_ScriptedLLMProvider(
            [
                {
                    "parsed_payload": {"kind": "other", "actor": "Ops", "slug": "ok", "confidence": 0.7, "notes": ""},
                    "raw_response": {"output": []},
                }
            ]
        ),
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "txt.txt"
    input_file.write_text("alpha", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="alpha",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="alpha",
        )
    )

    action_file = Path(result.artifacts_dir) / "proposal_row.md"
    assert action_file.exists()
    assert action_file.read_text(encoding="utf-8").startswith("status=")
