from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from respkit.actions import WriteMarkdownAction
from respkit.actions.base import ActionContext
from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.providers.base import LLMProvider, ProviderConfig, ProviderResponse
from respkit.runners import DirectoryBatchRunner, ReviewRunner, SingleInputRunner
from respkit.tasks import ReviewPolicy, TaskDefinition
from respkit.tasks.message import Message
from respkit.validators import EnumCaseNormalizer, FillDefaultsValidator, TrimWhitespaceValidator


class FakeLLMProvider(LLMProvider):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[Message], str, type | None, ProviderConfig | None]] = []

    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        response_model: type | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        self.calls.append((messages, model, response_model, config))
        payload = self._responses.pop(0)
        request_payload = {
            "model": model,
            "input": [message.to_api_payload() for message in messages],
            "temperature": config.temperature if config is not None else 0.0,
        }
        return ProviderResponse(
            request_payload=request_payload,
            raw_response={
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
            parsed_payload=payload,
            usage={"input_tokens": 1, "output_tokens": 2},
            status_code=200,
        )


class ScriptedLLMProvider(LLMProvider):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[Message], str, type | None, ProviderConfig | None, dict | None]] = []

    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        response_model: type | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        payload = self._responses.pop(0)
        request_payload = {
            "model": model,
            "input": [message.to_api_payload() for message in messages],
            "temperature": config.temperature if config is not None else 0.0,
        }
        self.calls.append((messages, model, response_model, config, payload))
        return ProviderResponse(
            request_payload=request_payload,
            raw_response=payload.get("raw_response", {}),
            parsed_payload=payload.get("parsed_payload"),
            usage=payload.get("usage"),
            status_code=payload.get("status_code"),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            discovered_models=payload.get("discovered_models"),
        )


class RenameProposal(BaseModel):
    kind: Literal["invoice", "legal", "other"]
    actor: str
    slug: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class RenameReview(BaseModel):
    decision: Literal["pass", "fail", "uncertain"]
    notes: str


class FileSummary(BaseModel):
    language: Literal["en", "es"]
    paragraph_count: int = Field(ge=1)
    notes: str = ""


def make_input(path: Path) -> NormalizedInput:
    return NormalizedInput(
        source_id=path.as_posix(),
        source_path=path,
        media_type="text/plain",
        decoded_text=path.read_text(encoding="utf-8"),
    )


def make_prompt(path: Path, text: str = "Return JSON") -> Path:
    prompt = path / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(text, encoding="utf-8")
    return prompt


def _markdown_action(context: ActionContext) -> str:
    output = context.validated_output
    return f"status={context.run_metadata.get('status')}\nkind={output.get('kind', '')}\n"


def test_single_success(tmp_path):
    prompt_path = make_prompt(tmp_path, "Create output")
    payload = {"kind": "Invoice", "actor": " alice ", "slug": " contract-v1 ", "confidence": 0.8}

    task = TaskDefinition(
        name="single_task",
        description="single",
        prompt_template_path=prompt_path,
        response_model=RenameProposal,
        provider_model="test",
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"kind": ["invoice", "legal", "other"]}),
            FillDefaultsValidator(defaults={"notes": ""}),
        ),
    )

    runner = SingleInputRunner(
        task=task,
        provider=FakeLLMProvider([payload]),
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )
    input_file = tmp_path / "a.txt"
    input_file.write_text("invoice text", encoding="utf-8")

    result = runner.run(make_input(input_file))

    assert result.status == "success"
    assert result.provider_request["model"] == "test"
    assert result.validated_output is not None
    assert result.validated_output.kind == "invoice"
    assert result.validated_output.actor == "alice"


def test_artifact_directory_contents(tmp_path):
    prompt_path = make_prompt(tmp_path, "Action")
    payload = {"kind": "invoice", "actor": "ops", "slug": "memo", "confidence": 0.5}

    task = TaskDefinition(
        name="action_task",
        description="actions",
        prompt_template_path=prompt_path,
        response_model=RenameProposal,
        provider_model="test",
        validators=(TrimWhitespaceValidator(),),
    )

    runner = SingleInputRunner(
        task=task,
        provider=FakeLLMProvider([payload]),
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "act.txt"
    input_file.write_text("content", encoding="utf-8")

    result = runner.run(make_input(input_file))

    artifact_dir = Path(result.artifacts_dir)
    expected = {
        "prompt_template.md",
        "prompt.txt",
        "provider_request.json",
        "raw_response.json",
        "parsed_response.json",
        "validation_report.json",
        "validated_response.json",
        "action_results.json",
        "run_metadata.json",
    }
    files = {path.name for path in artifact_dir.iterdir()}
    assert expected.issubset(files)


def test_batch_run(tmp_path):
    prompt_path = make_prompt(tmp_path / "batch", "Run batch")
    outputs = [
        {"kind": "invoice", "actor": "acme", "slug": "in-01", "confidence": 0.7},
        {"kind": "other", "actor": "acme", "slug": "note-02", "confidence": 0.9, "notes": ""},
    ]

    task = TaskDefinition(
        name="batch_task",
        description="batch",
        prompt_template_path=prompt_path,
        response_model=RenameProposal,
        provider_model="test",
        validators=(TrimWhitespaceValidator(), FillDefaultsValidator({"notes": ""})),
    )

    runner = SingleInputRunner(
        task=task,
        provider=FakeLLMProvider(outputs),
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )

    folder = tmp_path / "inputs"
    folder.mkdir()
    for name in ("one.txt", "two.txt"):
        (folder / name).write_text(f"content {name}", encoding="utf-8")

    results = DirectoryBatchRunner(single_runner=runner).run(folder)

    assert len(results) == 2
    assert len([line for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]) == 2
    manifest_rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest_rows[0]["task_name"] == "batch_task"
    assert manifest_rows[0]["validation_status"] in {"passed", "failed", "provider_error"}


def test_validation_failure(tmp_path):
    prompt_path = make_prompt(tmp_path, "Invalid confidence")
    payload = {"kind": "invoice", "actor": "acme", "slug": "bad", "confidence": 2.0}

    task = TaskDefinition(
        name="invalid",
        description="invalid",
        prompt_template_path=prompt_path,
        response_model=RenameProposal,
        provider_model="test",
    )

    runner = SingleInputRunner(
        task=task,
        provider=FakeLLMProvider([payload]),
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )
    input_file = tmp_path / "bad.txt"
    input_file.write_text("bad", encoding="utf-8")

    result = runner.run(make_input(input_file))

    assert result.status == "validation_failed"
    assert not result.validation_report.valid
    assert len(result.validation_report.errors or []) > 0


def test_manifest_append(tmp_path):
    prompt_path = make_prompt(tmp_path, "Manifest")
    payload = {"kind": "other", "actor": "ops", "slug": "memo", "confidence": 0.4}

    runner = SingleInputRunner(
        task=TaskDefinition(
            name="manifest",
            description="manifest",
            prompt_template_path=prompt_path,
            response_model=RenameProposal,
            provider_model="gpt-oss-20b",
        ),
        provider=FakeLLMProvider([payload]),
        artifacts_root=tmp_path / "artifacts",
        manifest_writer=ManifestWriter(tmp_path / "manifest.jsonl"),
    )
    input_file = tmp_path / "in.txt"
    input_file.write_text("text", encoding="utf-8")

    result = runner.run(make_input(input_file))

    rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == result.run_id
    assert rows[0]["task_name"] == "manifest"
    assert rows[0]["model"] == "gpt-oss-20b"
    assert rows[0]["artifact_dir"] == result.artifacts_dir
    assert rows[0]["timestamp"]


def test_action_execution(tmp_path):
    prompt_path = make_prompt(tmp_path, "Action")
    payload = {"kind": "invoice", "actor": "ops", "slug": "memo", "confidence": 0.5, "notes": "ok"}

    task = TaskDefinition(
        name="action_task",
        description="actions",
        prompt_template_path=prompt_path,
        response_model=RenameProposal,
        provider_model="test",
        validators=(TrimWhitespaceValidator(),),
        actions=(WriteMarkdownAction(filename="proposal.md", content_builder=_markdown_action),),
    )

    runner = SingleInputRunner(
        task=task,
        provider=FakeLLMProvider([payload]),
        artifacts_root=tmp_path / "artifacts",
    )
    input_file = tmp_path / "act.txt"
    input_file.write_text("content", encoding="utf-8")

    result = runner.run(make_input(input_file))
    action_path = Path(result.artifacts_dir) / "proposal.md"

    assert action_path.exists()
    assert action_path.read_text(encoding="utf-8").startswith("status=success")


def test_review_pass(tmp_path):
    proposal_prompt = make_prompt(tmp_path / "proposal", "Proposal")
    review_prompt = make_prompt(tmp_path / "review", "Review")

    review_task = TaskDefinition(
        name="review",
        description="review",
        prompt_template_path=review_prompt,
        response_model=RenameReview,
        provider_model="test",
        prompt_context_builder=lambda item: {
            "old_filename": item.metadata.get("old_filename", ""),
            "first_output": item.metadata.get("first_output", "{}"),
            "text": item.decoded_text,
        },
        validators=(
            EnumCaseNormalizer(field_values={"decision": ["pass", "fail", "uncertain"]}),
        ),
    )

    proposal_task = TaskDefinition(
        name="proposal",
        description="proposal",
        prompt_template_path=proposal_prompt,
        response_model=RenameProposal,
        provider_model="test",
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"kind": ["invoice", "legal", "other"]}),
            FillDefaultsValidator(defaults={"notes": ""}),
        ),
        review_policy=ReviewPolicy(
            task=review_task,
            context_builder=lambda original_item, first_output: {
                "old_filename": original_item.source_id,
                "first_output": json.dumps(first_output),
            },
        ),
    )

    first_provider = FakeLLMProvider(
        [
            {"kind": "invoice", "actor": "acme", "slug": "contract-1", "confidence": 0.91, "notes": ""}
        ]
    )
    review_provider = FakeLLMProvider([
        {"decision": "pass", "notes": "consistent with source text"}
    ])

    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=first_provider,
        artifacts_root=tmp_path / "first_artifacts",
    )
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
    )

    input_file = tmp_path / "contract.txt"
    input_file.write_text("contract source", encoding="utf-8")
    first_result = first_runner.run(make_input(input_file))

    review_result = ReviewRunner().run(
        first_result=first_result,
        original_item=make_input(input_file),
        policy=proposal_task.review_policy,
        single_runner=review_runner,
    )

    assert review_result.status == "success"
    assert review_result.review_output.decision == "pass"


@pytest.mark.parametrize(
    "decision,expected",
    [
        ("uncertain", "review_failed"),
        ("fail", "review_failed"),
    ],
)
def test_review_ambiguous_or_failed_case(tmp_path, decision: str, expected: str):
    proposal_prompt = make_prompt(tmp_path / "proposal", "Proposal")
    review_prompt = make_prompt(tmp_path / "review", "Review")

    review_task = TaskDefinition(
        name="review",
        description="review",
        prompt_template_path=review_prompt,
        response_model=RenameReview,
        provider_model="test",
        prompt_context_builder=lambda item: {
            "old_filename": item.metadata.get("old_filename", ""),
            "first_output": item.metadata.get("first_output", "{}"),
            "text": item.decoded_text,
        },
        validators=(
            EnumCaseNormalizer(field_values={"decision": ["pass", "fail", "uncertain"]}),
        ),
    )

    proposal_task = TaskDefinition(
        name="proposal",
        description="proposal",
        prompt_template_path=proposal_prompt,
        response_model=RenameProposal,
        provider_model="test",
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"kind": ["invoice", "legal", "other"]}),
            FillDefaultsValidator(defaults={"notes": ""}),
        ),
        review_policy=ReviewPolicy(
            task=review_task,
            context_builder=lambda original_item, first_output: {
                "old_filename": original_item.source_id,
                "first_output": json.dumps(first_output),
            },
        ),
    )

    first_provider = FakeLLMProvider(
        [
            {"kind": "invoice", "actor": "acme", "slug": "contract-1", "confidence": 0.91, "notes": ""},
        ]
    )
    review_provider = FakeLLMProvider(
        [{"decision": decision, "notes": "reviewed", "recommended_adjustments": "needs check"}]
    )

    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=first_provider,
        artifacts_root=tmp_path / "first_artifacts",
    )
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
    )

    input_file = tmp_path / "contract.txt"
    input_file.write_text("contract source", encoding="utf-8")
    first_result = first_runner.run(make_input(input_file))

    review_result = ReviewRunner().run(
        first_result=first_result,
        original_item=make_input(input_file),
        policy=proposal_task.review_policy,
        single_runner=review_runner,
    )

    assert review_result.status == expected
    assert review_result.review_output.decision == decision


def test_review_runner_skips_on_parse_error(tmp_path):
    proposal_prompt = make_prompt(tmp_path / "proposal", "Proposal")
    review_prompt = make_prompt(tmp_path / "review", "Review")
    proposal_task = TaskDefinition(
        name="proposal_with_review",
        description="proposal",
        prompt_template_path=proposal_prompt,
        response_model=RenameProposal,
        provider_model="test",
        review_policy=ReviewPolicy(
            task=TaskDefinition(
                name="review",
                description="review",
                prompt_template_path=review_prompt,
                response_model=RenameReview,
                provider_model="test",
                validators=(EnumCaseNormalizer(field_values={"decision": ["pass", "fail", "uncertain"]}),),
                prompt_context_builder=(
                    lambda original_item, first_output: {
                        "old_filename": original_item.source_id,
                        "first_output": json.dumps(first_output),
                    }
                ),
            ),
            context_builder=lambda original_item, first_output: {
                "old_filename": original_item.source_id,
                "first_output": json.dumps(first_output),
            },
        ),
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"kind": ["invoice", "legal", "other"]}),
            FillDefaultsValidator(defaults={"notes": ""}),
        ),
    )

    first_provider = ScriptedLLMProvider(
        [
            {
                "parsed_payload": None,
                "raw_response": {},
                "error_code": "invalid_payload",
                "error_message": "No parseable JSON payload found",
                "status_code": 200,
            }
        ]
    )
    review_provider = ScriptedLLMProvider([])

    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=first_provider,
        artifacts_root=tmp_path / "first_artifacts",
    )
    review_runner = SingleInputRunner(
        task=proposal_task.review_policy.task,  # type: ignore[union-attr]
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
    )

    input_file = tmp_path / "contract.txt"
    input_file.write_text("contract source", encoding="utf-8")
    first_result = first_runner.run(make_input(input_file))

    review_result = ReviewRunner().run(
        first_result=first_result,
        original_item=make_input(input_file),
        policy=proposal_task.review_policy,  # type: ignore[union-attr]
        single_runner=review_runner,
    )

    assert review_result.status == "not_run"
    assert review_result.review_status == "not_run"
    assert review_provider.calls == []

