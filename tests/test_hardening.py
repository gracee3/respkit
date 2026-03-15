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
from respkit.runners import ReviewRunner
from respkit.tasks import ReviewPolicy, TaskDefinition
from respkit.validators import EnumCaseNormalizer, FillDefaultsValidator, TrimWhitespaceValidator
from respkit.utils import list_text_files
from respkit.utils.filesystem import read_text_file


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
            discovered_models=response.get("discovered_models"),
        )


def _proposal_task(tmp_path: Path, *, min_input_chars: int | None = None, action: object | None = None) -> TaskDefinition:
    prompt = tmp_path / "proposal.md"
    prompt.write_text("Run rename proposal\n{text}", encoding="utf-8")

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

    assert result.status == "parse_error"
    assert result.validation_report.valid is False
    assert any("Could not parse JSON content" in e.message for e in result.validation_report.errors or [])


def test_schema_invalid_output_is_validation_failed(tmp_path):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {"kind": "invoice", "actor": 123, "slug": "x", "confidence": 0.99, "notes": ""},
                "raw_response": {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
                    ]
                },
                "status_code": 200,
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "type.txt"
    input_file.write_text("some text", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="type",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="some text",
        )
    )

    assert result.status == "validation_failed"
    assert not result.validation_report.valid
    assert any("Input should be a valid string" in e.message for e in result.validation_report.errors or [])


def test_partial_structured_output_is_validation_failed(tmp_path):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {"kind": "invoice", "slug": "x", "confidence": 0.99},
                "raw_response": {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
                    ]
                },
                "status_code": 200,
            }
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "partial.txt"
    input_file.write_text("some text", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="partial",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="some text",
        )
    )

    assert result.status == "validation_failed"
    assert not result.validation_report.valid
    assert any(getattr(e, "path", None) == "actor" for e in result.validation_report.errors or [])


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


def test_batch_summary_is_written_and_printed(tmp_path, capsys):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {"kind": "invoice", "actor": "Alice", "slug": "a1", "confidence": 0.9, "notes": "ok"},
                "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                "status_code": 200,
            },
            {
                "parsed_payload": {"kind": "invalid", "actor": "Bob", "slug": "bad", "confidence": 1.0, "notes": "bad kind"},
                "raw_response": {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
                    ]
                },
                "status_code": 200,
            },
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    for name in ("clean_easy.txt", "ambiguous_actor.txt"):
        (input_dir / name).write_text(f"content for {name}", encoding="utf-8")

    results = DirectoryBatchRunner(single_runner=runner, output_root=tmp_path / "summary_out").run(input_dir)
    assert len(results) == 2
    summary_path = tmp_path / "summary_out" / "batch_summary.json"
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total"] == 2
    assert summary["status_counts"]["success"] == 1
    assert summary["status_counts"]["validation_failed"] == 1

    captured = capsys.readouterr().out
    assert "Batch run complete" in captured
    assert "success=1" in captured
    assert "validation_failed=1" in captured


def test_preflight_model_not_found_artifacts_and_manifest_status(tmp_path):
    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "raw_response": {"discovered_models": ["other-model"]},
                "error_code": "preflight_model_not_found",
                "error_message": "requested_model=gpt-oss-20b; discovered_models=['other-model']",
                "status_code": 404,
                "discovered_models": ["other-model"],
            }
        ]
    )
    manifest = ManifestWriter(tmp_path / "manifest.jsonl")
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=manifest,
    )
    input_file = tmp_path / "txt.txt"
    input_file.write_text("Some decently long text for preflight behavior.", encoding="utf-8")

    result = runner.run(
        NormalizedInput(
            source_id="preflight",
            source_path=input_file,
            media_type="text/plain",
            decoded_text="Some decently long text for preflight behavior.",
        )
    )

    assert result.status == "preflight_model_not_found"
    artifact_dir = Path(result.artifacts_dir)
    assert (artifact_dir / ArtifactWriter.DISCOVERED_MODELS_FILE).exists()
    manifest_rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest_rows[-1]["status"] == "preflight_model_not_found"


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


def test_batch_with_max_concurrency_is_supported_and_stable(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    for idx in range(12):
        (input_dir / f"doc_{idx}.txt").write_text(f"document {idx}", encoding="utf-8")

    task = _proposal_task(tmp_path)
    responses = [
        {
            "parsed_payload": {
                "kind": "other",
                "actor": "Ops",
                "slug": f"item-{idx}",
                "confidence": 0.87,
                "notes": "",
            },
            "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
            "status_code": 200,
        }
        for idx in range(12)
    ]
    provider = _ScriptedLLMProvider(responses=responses)
    manifest_path = tmp_path / "manifest.jsonl"
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )

    results = DirectoryBatchRunner(single_runner=runner, max_concurrency=4).run(input_dir)

    assert len(results) == 12
    assert len(results) == provider.calls.__len__()
    manifest_rows = [line for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(manifest_rows) == 12
    assert all("status" in json.loads(line) for line in manifest_rows)


def test_concurrent_batch_writes_isolated_artifact_dirs(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    file_count = 8
    for idx in range(file_count):
        (input_dir / f"file_{idx}.txt").write_text(f"content {idx}", encoding="utf-8")

    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {
                    "kind": "invoice",
                    "actor": "Ops",
                    "slug": f"slug-{idx}",
                    "confidence": 0.66,
                    "notes": "",
                },
                "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                "status_code": 200,
            }
            for idx in range(file_count)
        ]
    )
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
    )
    results = DirectoryBatchRunner(single_runner=runner, max_concurrency=4).run(input_dir)
    assert len(results) == file_count

    artifact_dirs = [Path(result.artifacts_dir) for result in results]
    assert len(set(artifact_dirs)) == file_count
    for artifact_dir in artifact_dirs:
        assert artifact_dir.exists()
        assert artifact_dir.is_dir()
        assert (artifact_dir / ArtifactWriter.PROVIDER_REQUEST_FILE).exists()


def test_manifest_jsonl_is_valid_after_concurrent_batch(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    for idx in range(6):
        (input_dir / f"line_{idx}.txt").write_text(f"text {idx}", encoding="utf-8")

    task = _proposal_task(tmp_path)
    provider = _ScriptedLLMProvider(
        responses=[
            {
                "parsed_payload": {
                    "kind": "other",
                    "actor": "Team",
                    "slug": f"item-{idx}",
                    "confidence": 0.77,
                    "notes": "",
                },
                "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                "status_code": 200,
            }
            for idx in range(6)
        ]
    )
    manifest_path = tmp_path / "manifest.jsonl"
    runner = SingleInputRunner(
        task=task,
        provider=provider,
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )

    DirectoryBatchRunner(single_runner=runner, max_concurrency=3).run(input_dir)

    rows = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        rows.append(parsed)
    assert len(rows) == 6
    assert all(isinstance(row, dict) for row in rows)


def test_review_still_skips_parse_errors_with_concurrent_batch(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    for idx, text in enumerate(["ok", "broken", "also-ok"]):
        (input_dir / f"sample_{idx}.txt").write_text(text, encoding="utf-8")

    task = _proposal_task(tmp_path)
    class _ConcurrentRoutingProvider(_ScriptedLLMProvider):
        def complete(
            self,
            *,
            messages: list[Any],
            model: str,
            response_model: type[BaseModel] | None = None,
            config: ProviderConfig | None = None,
        ) -> ProviderResponse:
            prompt_text = messages[0].content if messages else ""
            if "broken" in prompt_text:
                response = {
                    "parsed_payload": None,
                    "raw_response": {"output": [{"type": "message", "content": "not-json"}]},
                    "error_code": "invalid_payload",
                    "error_message": "Could not parse JSON content",
                    "status_code": 200,
                }
            elif "also" in prompt_text:
                response = {
                    "parsed_payload": {
                        "kind": "invoice",
                        "actor": "Ops",
                        "slug": "third",
                        "confidence": 0.9,
                        "notes": "",
                    },
                    "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                    "status_code": 200,
                }
            else:
                response = {
                    "parsed_payload": {
                        "kind": "other",
                        "actor": "Ops",
                        "slug": "first",
                        "confidence": 0.8,
                        "notes": "",
                    },
                    "raw_response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]},
                    "status_code": 200,
                }
            return ProviderResponse(
                request_payload={
                    "model": model,
                    "input": [message.to_api_payload() for message in messages],
                    "temperature": config.temperature if config is not None else 0.0,
                },
                raw_response=response.get("raw_response", {}),
                parsed_payload=response.get("parsed_payload"),
                usage=response.get("usage"),
                status_code=response.get("status_code"),
                error_code=response.get("error_code"),
                error_message=response.get("error_message"),
                discovered_models=response.get("discovered_models"),
            )

    first_provider = _ConcurrentRoutingProvider(responses=[{}])
    first_runner = SingleInputRunner(
        task=task,
        provider=first_provider,
        artifacts_root=tmp_path / "first_artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )

    first_results = DirectoryBatchRunner(single_runner=first_runner, max_concurrency=2).run(input_dir)
    statuses_by_source = {result.source_id: result.status for result in first_results}
    assert set(statuses_by_source.values()) == {"success", "parse_error"}
    assert len([status for status in statuses_by_source.values() if status == "parse_error"]) == 1
    assert any(status == "parse_error" for status in statuses_by_source.values())

    class _ReviewModel(BaseModel):
        decision: str
        notes: str
        recommended_adjustments: str = ""

    review_task = TaskDefinition(
        name="review_task",
        description="review",
        prompt_template_path=task.prompt_template_path,
        response_model=_ReviewModel,
        provider_model="gpt-oss-20b",
    )
    review_policy = ReviewPolicy(
        task=review_task,
        context_builder=lambda original_item, first_output: {"first_pass_output": first_output},
    )
    review_provider = _ScriptedLLMProvider(
        responses=[
            {"parsed_payload": {"decision": "pass", "notes": "looks good", "recommended_adjustments": ""}, "raw_response": {}},
            {"parsed_payload": {"decision": "pass", "notes": "also good", "recommended_adjustments": ""}, "raw_response": {}},
        ]
    )
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
    )

    reviewed = 0
    for path in list_text_files(input_dir):
        first_result = next(
            result for result in first_results if result.source_id == path.as_posix()
        )
        original = NormalizedInput(
            source_id=path.as_posix(),
            source_path=path,
            media_type="text/plain",
            decoded_text=read_text_file(path),
        )
        if first_result.status != "success":
            continue
        review_result = ReviewRunner().run(
            first_result=first_result,
            original_item=original,
            policy=review_policy,
            single_runner=review_runner,
        )
        reviewed += 1
        assert review_result.status == "success"

    assert reviewed == 2
    assert len(review_provider.calls) == 2
