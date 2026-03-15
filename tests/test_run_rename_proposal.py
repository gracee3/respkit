from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from pydantic import BaseModel

from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.providers import LLMProvider, ProviderConfig, ProviderResponse
from respkit.runners import DirectoryBatchRunner, SingleInputRunner
from examples.rename_file_proposal import build_tasks
from examples.run_rename_proposal import run_review_batch


class _ScriptedReviewProvider(LLMProvider):
    def __init__(self, responses: list[dict], delay_s: float = 0.0) -> None:
        self._responses = list(responses)
        self.delay_s = delay_s
        self.calls: list[tuple[str, float]] = []
        self._active = 0
        self._max_active = 0
        self._lock = threading.Lock()

    def complete(
        self,
        *,
        messages: list[BaseModel],
        model: str,
        response_model: type[BaseModel] | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        with self._lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
            self.calls.append((model, time.time()))

        if self.delay_s:
            time.sleep(self.delay_s)

        response = self._responses.pop(0)

        with self._lock:
            self._active -= 1

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

    @property
    def max_active_requests(self) -> int:
        return self._max_active


def _build_proposal_responses(count: int, *, status: str = "success") -> list[dict]:
    if status == "parse_error":
        return [
            {
                "parsed_payload": None,
                "raw_response": {"output": []},
                "error_code": "invalid_payload",
                "error_message": "Could not parse JSON content",
                "status_code": 200,
            }
        ] + [
            {
                "parsed_payload": {
                    "kind": "correspondence",
                    "actor": "parent",
                    "slug": f"item-{idx}",
                    "confidence": 0.92,
                    "notes": "ok",
                },
                "raw_response": {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "{\"kind\": \"correspondence\", \"actor\": \"parent\", \"slug\": \"ok\", \"confidence\": 0.9, \"notes\": \"\"}"}],
                        }
                    ]
                },
                "status_code": 200,
            }
            for idx in range(count - 1)
        ]

    return [
        {
            "parsed_payload": {
                "kind": "correspondence",
                "actor": "parent",
                "slug": f"item-{idx}",
                "confidence": 0.92,
                "notes": "ok",
            },
            "raw_response": {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "kind": "correspondence",
                                        "actor": "parent",
                                        "slug": f"item-{idx}",
                                        "confidence": 0.92,
                                        "notes": "ok",
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
            "status_code": 200,
        }
        for idx in range(count)
    ]


def _review_payloads(count: int) -> list[dict]:
    return [
        {
            "parsed_payload": {
                "decision": "pass",
                "notes": "reviewed",
                "recommended_adjustments": "",
            },
            "raw_response": {"output": []},
            "status_code": 200,
        }
        for _ in range(count)
    ]


def _make_input_dir(tmp_path: Path, file_count: int) -> Path:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    for idx in range(file_count):
        payload = (
            "document content with enough characters to satisfy proposal "
            f"length checks for item {idx}. "
            "This text is intentionally long."
        )
        (input_dir / f"doc_{idx}.txt").write_text(payload, encoding="utf-8")
    return input_dir


def test_run_review_batch_preserves_row_counts_and_manifest_jsonl(tmp_path):
    input_dir = _make_input_dir(tmp_path, 4)
    manifest_path = tmp_path / "manifest.jsonl"

    proposal_task, review_task = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")

    proposal_provider = _ScriptedReviewProvider(_build_proposal_responses(4))
    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=proposal_provider,
        artifacts_root=tmp_path / "proposal_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )
    first_results = DirectoryBatchRunner(single_runner=first_runner, max_concurrency=2).run(input_dir)
    assert len(first_results) == 4

    review_provider = _ScriptedReviewProvider(_review_payloads(4))
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )

    run_review_batch(
        directory=input_dir,
        first_results=first_results,
        reviewer=review_runner,
        review_policy=proposal_task.review_policy,
        review_max_concurrency=3,
    )

    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all(isinstance(row, dict) for row in rows)
    proposal_rows = [row for row in rows if row["task_name"] == "rename_file_proposal"]
    review_rows = [row for row in rows if row["task_name"] == "rename_file_review"]

    assert len(proposal_rows) == 4
    assert len(review_rows) == 4
    assert {row["status"] for row in review_rows} <= {"success", "review_failed", "not_run", "provider_error"}


def test_concurrent_review_runs_isolated_artifact_dirs(tmp_path):
    input_dir = _make_input_dir(tmp_path, 6)
    manifest_path = tmp_path / "manifest.jsonl"

    proposal_task, review_task = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")
    proposal_provider = _ScriptedReviewProvider(_build_proposal_responses(6))
    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=proposal_provider,
        artifacts_root=tmp_path / "proposal_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )
    first_results = DirectoryBatchRunner(single_runner=first_runner, max_concurrency=2).run(input_dir)

    review_provider = _ScriptedReviewProvider(_review_payloads(6), delay_s=0.05)
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )
    run_review_batch(
        directory=input_dir,
        first_results=first_results,
        reviewer=review_runner,
        review_policy=proposal_task.review_policy,
        review_max_concurrency=3,
    )

    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    review_rows = [row for row in rows if row["task_name"] == "rename_file_review"]
    dirs = [Path(row["artifact_dir"]) for row in review_rows]
    assert len(dirs) == 6
    assert len(set(str(d) for d in dirs)) == 6
    for d in dirs:
        assert d.is_dir()

    assert review_provider.max_active_requests >= 2


def test_review_skipped_on_failed_first_pass(tmp_path):
    input_dir = _make_input_dir(tmp_path, 3)
    manifest_path = tmp_path / "manifest.jsonl"

    proposal_task, review_task = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")

    proposal_provider = _ScriptedReviewProvider(
        responses=[
            _build_proposal_responses(1)[0],
            _build_proposal_responses(1, status="parse_error")[0],
            _build_proposal_responses(1)[0],
        ]
    )
    first_runner = SingleInputRunner(
        task=proposal_task,
        provider=proposal_provider,
        artifacts_root=tmp_path / "proposal_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )
    first_results = DirectoryBatchRunner(single_runner=first_runner).run(input_dir)

    failed = [result for result in first_results if result.source_id.endswith("doc_1.txt")][0]

    review_provider = _ScriptedReviewProvider(_review_payloads(2))
    review_runner = SingleInputRunner(
        task=review_task,
        provider=review_provider,
        artifacts_root=tmp_path / "review_artifacts",
        manifest_writer=ManifestWriter(manifest_path),
    )
    run_review_batch(
        directory=input_dir,
        first_results=first_results,
        reviewer=review_runner,
        review_policy=proposal_task.review_policy,
        review_max_concurrency=2,
    )

    skipped_payload = json.loads((Path(failed.artifacts_dir) / "review_status.json").read_text(encoding="utf-8"))
    assert skipped_payload["review_status"] == "not_run"

    assert review_provider.calls.count != 0
    assert len(review_provider.calls) == 2
