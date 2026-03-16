"""Tests for the ledger service API and stdio transport."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from respkit.ledger import HumanDecision, LedgerRow, LedgerStore, ResolverAction
from respkit.service import (
    ActionDescriptor,
    ActionResult,
    DefaultTaskServiceAdapter,
    LedgerService,
    LedgerServiceBackend,
)
from respkit.ledger.resolver import ValidationResult


class ToyServiceAdapter(DefaultTaskServiceAdapter):
    """Adapter used to validate preview/validation/custom action behavior."""

    def __init__(self, task_name: str = "service-task") -> None:
        super().__init__(task_name=task_name)
        self.called = []

    def preview_item(self, row: Any) -> dict[str, str]:
        self.called.append(("preview", row.item_id))
        return {"preview": row.item_id}

    def validate_resolution(self, row: LedgerRow, edits: Any | None):
        self.called.append(("validate", row.item_id, edits))
        if row.item_id == "bad" and edits != {"ok": True}:
            return ValidationResult(valid=False, errors=["bad edit"])
        return ValidationResult(valid=True)

    def derive_approved_output(self, row: LedgerRow, edits: Any | None) -> dict[str, Any]:
        self.called.append(("derive", row.item_id, edits))
        return {"derived_from": row.item_id, "edits": edits}

    def available_actions(self, row: LedgerRow) -> list[ActionDescriptor]:
        return super().available_actions(row) + [
            ActionDescriptor(name="mark_checked", description="mark row checked", requires_edits=False),
        ]

    def execute_action(self, *, row: LedgerRow, action: str, params: dict[str, Any] | None, store: Any) -> ActionResult:
        self.called.append(("action", row.item_id, action, params))
        if action == "mark_checked":
            store.record_human_decision(
                task_name=row.task_name,
                item_id=row.item_id,
                decision=HumanDecision.NEEDS_REVIEW,
                decision_payload={"mark_checked": True},
                decision_source="action",
                decision_actor="adapter",
                notes="marked by action",
            )
            return ActionResult(success=True, message="marked", payload={"marked": True})
        return super().execute_action(row=row, action=action, params=params, store=store)


def _run_request(
    service: LedgerServiceBackend,
    request: dict[str, Any],
) -> dict[str, Any]:
    output = io.StringIO()
    service.output = output
    service.input = io.StringIO(json.dumps(request) + "\n")
    service.run()
    if request.get("id") is None:
        return {}
    return json.loads(output.getvalue().strip())


def test_dispatch_unknown_method_returns_error(tmp_path: Path) -> None:
    _ = LedgerStore(tmp_path / "ledger.sqlite")
    service = LedgerService(tmp_path / "ledger.sqlite")
    backend = LedgerServiceBackend(tmp_path / "ledger.sqlite", adapters=[], input_stream=io.StringIO(), output_stream=io.StringIO())
    response = _run_request(
        backend,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "does_not_exist",
        },
    )
    assert response["error"]["code"] == -32601
    service.close()
    backend.close()


def test_service_summary_and_rows_list(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})
    store.record_review(task_name=task_name, item_id="a", review_payload={"r": 1}, review_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="a", decision=HumanDecision.APPROVED)
    store.record_proposal(task_name=task_name, item_id="b", proposal_payload={"x": 2})

    service = LedgerService(tmp_path / "ledger.sqlite")
    summary = service.summary({"task_name": task_name})
    assert summary["counts"]["total"] == 2
    assert summary["counts"]["approved"] == 1
    listed = service.list_rows({"task_name": task_name})
    assert len(listed["rows"]) == 2
    service.close()


def test_service_row_detail_and_history(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})
    store.record_review(task_name=task_name, item_id="a", review_payload={"y": 2})

    service = LedgerService(tmp_path / "ledger.sqlite")
    row_payload = service.get_row({"task_name": task_name, "item_id": "a"})["row"]
    assert row_payload["item_id"] == "a"
    history = service.get_row_history({"task_name": task_name, "item_id": "a"})["events"]
    assert len(history) == 2
    assert history[0]["event_type"] == "record_proposal"
    assert history[1]["event_type"] == "record_review"
    service.close()


def test_service_preview_validate_and_derive(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})
    store.record_proposal(task_name=task_name, item_id="bad", proposal_payload={"x": 2})

    adapter = ToyServiceAdapter(task_name=task_name)
    service = LedgerService(tmp_path / "ledger.sqlite", adapters=[adapter])

    assert service.preview_row({"task_name": task_name, "item_id": "a"})["preview"] == {"preview": "a"}
    valid = service.validate({"task_name": task_name, "item_id": "a", "edits": {"ok": True}})
    assert valid["valid"] is True
    invalid = service.validate({"task_name": task_name, "item_id": "bad", "edits": {"ok": False}})
    assert invalid["valid"] is False
    derive = service.derive({"task_name": task_name, "item_id": "a", "edits": {"ok": True}})
    assert derive["approved_output"]["edits"] == {"ok": True}
    service.close()


def test_recommendation_mode_vs_apply_mode_and_provenance(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})

    service = LedgerService(tmp_path / "ledger.sqlite")
    rec = service.decide(
        {
            "task_name": task_name,
            "item_id": "a",
            "action": ResolverAction.APPROVE.value,
            "apply": False,
        }
    )
    assert rec["status"] == "recommendation_only"
    assert store.get_row(task_name, "a").human_status.value == "needs_review"

    applied = service.decide(
        {
            "task_name": task_name,
            "item_id": "a",
            "action": ResolverAction.APPROVE.value,
            "apply": True,
            "decision_source": "agent",
            "decision_actor": "agent-1",
            "decision_code_commit": "abc123",
        }
    )
    assert applied["status"] == "applied"
    persisted = store.get_row(task_name, "a")
    assert persisted.human_decision_source == "agent"
    assert persisted.human_decision_actor == "agent-1"
    assert persisted.human_decision_code_commit == "abc123"
    service.close()


def test_service_decision_aliases_and_offset_limit(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    for index in range(3):
        store.record_proposal(task_name=task_name, item_id=f"item-{index}", proposal_payload={"index": index})

    service = LedgerService(tmp_path / "ledger.sqlite")
    first = service.list_rows({"task_name": task_name, "limit": 2, "offset": 0})["rows"]
    second = service.list_rows({"task_name": task_name, "limit": 2, "offset": 1})["rows"]
    assert len(first) == 2
    assert len(second) == 2
    assert {row["item_id"] for row in first} != set()
    assert {row["item_id"] for row in second} != set()

    rec = service.decide(
        {
            "task_name": task_name,
            "item_id": "item-0",
            "action": ResolverAction.REJECT.value,
            "apply": False,
            "decision_note": "manual note",
            "decision_source": "agent",
            "decision_actor": "policy-bot",
        }
    )
    assert rec["status"] == "recommendation_only"
    assert rec["decision_note"] == "manual note"
    assert rec["decision_source"] == "agent"
    assert rec["decision_actor"] == "policy-bot"

    applied = service.decide(
        {
            "task_name": task_name,
            "item_id": "item-1",
            "action": ResolverAction.APPROVE.value,
            "apply": True,
            "decision_note": "approve now",
            "decision_source": "agent",
            "decision_actor": "policy-bot",
        }
    )
    assert applied["status"] == "applied"
    applied_row = store.get_row(task_name, "item-1")
    assert applied_row is not None
    assert applied_row.human_notes == "approve now"
    assert applied_row.human_decision_source == "agent"
    assert applied_row.human_decision_actor == "policy-bot"
    service.close()


def test_service_actions_list_and_invoke_adapter_action(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})
    store.record_proposal(task_name=task_name, item_id="b", proposal_payload={"x": 2})

    adapter = ToyServiceAdapter(task_name=task_name)
    service = LedgerService(tmp_path / "ledger.sqlite", adapters=[adapter])
    actions = service.list_actions({"task_name": task_name})["actions"]
    assert any(action["name"] == "mark_checked" for action in actions)
    result = service.invoke_action(
        {
            "task_name": task_name,
            "action": "mark_checked",
            "item_ids": ["a", "b"],
        }
    )
    assert result["results"][0]["status"] == "ok"
    assert result["results"][1]["status"] == "ok"
    refreshed = store.get_row(task_name, "a")
    assert refreshed.human_decision_payload == {"mark_checked": True}
    service.close()


def test_service_export_methods(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task = "service-task"
    store.record_proposal(task_name=task, item_id="a", proposal_payload={"x": 1})
    store.record_proposal(task_name=task, item_id="b", proposal_payload={"x": 2})
    service = LedgerService(tmp_path / "ledger.sqlite")
    jsonl = service.export({"task_name": task, "format": "jsonl"})
    assert jsonl["format"] == "jsonl"
    assert len(jsonl["data"]) == 2
    csv = service.export({"task_name": task, "format": "csv"})
    assert csv["format"] == "csv"
    assert "item_id" in csv["data"]
    md = service.export({"task_name": task, "format": "markdown"})
    assert "a" in md["data"]
    service.close()


def test_stdio_transport_dispatches_results(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "service-task"
    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"x": 1})

    input_payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "rows.get",
                    "params": {"task_name": task_name, "item_id": "a"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ledger.summary", "params": {"task_name": task_name}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "system.shutdown"}),
        ]
    )
    in_stream = io.StringIO(input_payload)
    out_stream = io.StringIO()
    backend = LedgerServiceBackend(
        ledger_path=tmp_path / "ledger.sqlite",
        adapters=[],
        input_stream=in_stream,
        output_stream=out_stream,
    )
    backend.run()
    backend.close()

    responses = [json.loads(line) for line in out_stream.getvalue().splitlines() if line.strip()]
    assert len(responses) == 3
    assert responses[0]["id"] == 1 and responses[0]["result"]["row"]["task_name"] == task_name
    assert responses[1]["id"] == 2
    assert responses[2]["id"] == 3
    assert responses[2]["result"]["status"] == "ok"
