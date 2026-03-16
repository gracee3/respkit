"""Tests for ledger/store/resolver behavior."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any
import pytest

from respkit.ledger import (
    DefaultResolverHooks,
    LedgerResolver,
    ResolverAction,
    ResolverRowView,
    ResolverSession,
    ValidationResult,
    HumanDecision,
    ApplyPolicy,
    LedgerQuery,
    LedgerRow,
    LedgerStore,
    MachineStatus,
)
from respkit.ledger import cli as ledger_cli
from respkit.ledger.git import get_head_commit


def _init_git_repo(repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_root), "init"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.email", "ledger@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.name", "Ledger Test"], check=True)
    seed = repo_root / "seed.txt"
    seed.write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "init"], check=True)
    commit = get_head_commit(repo_root)
    assert commit is not None
    return commit


def _scripted_input(values: list[str]):
    items = iter(values)

    def _next(prompt: str) -> str:
        try:
            return next(items)
        except StopIteration:
            return "s"

    return _next


def test_row_creation_and_status_transitions(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"

    proposal = store.record_proposal(
        task_name=task_name,
        item_id="doc-1",
        item_locator="docs/doc-1.txt",
        proposal_payload={"action": "noop"},
        proposal_result={"ok": True},
    )
    assert proposal.machine_status == MachineStatus.PROPOSED
    assert proposal.human_status == HumanDecision.NEEDS_REVIEW
    assert proposal.version == 1

    reviewed = store.record_review(
        task_name=task_name,
        item_id="doc-1",
        review_payload={"notes": "looks_ok"},
        review_result={"score": 0.9},
    )
    assert reviewed.version == 2
    assert reviewed.machine_status == MachineStatus.REVIEWED

    approved = store.record_human_decision(
        task_name=task_name,
        item_id="doc-1",
        decision=HumanDecision.APPROVED,
    )
    assert approved.version == 3
    assert approved.machine_status == MachineStatus.APPLY_READY
    assert approved.human_status == HumanDecision.APPROVED

    applied = store.record_apply(task_name=task_name, item_id="doc-1", apply_payload={"operation": "noop"}, apply_result={"applied": True})
    assert applied.version == 4
    assert applied.machine_status == MachineStatus.APPLIED


def test_selective_rerun_filters(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"

    store.record_proposal(task_name=task_name, item_id="resolved", proposal_payload={"action": "noop"}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="resolved", decision=HumanDecision.APPROVED)
    store.record_apply(task_name=task_name, item_id="resolved", apply_payload={"noop": True}, apply_result={"applied": True}, success=True)

    store.record_proposal(task_name=task_name, item_id="needs-review", proposal_payload={"action": "review"}, proposal_result={"ok": True})
    store.record_proposal(task_name=task_name, item_id="provider-error", proposal_payload={"action": "fail"}, proposal_result={"ok": False}, machine_status=MachineStatus.PROVIDER_ERROR)
    store.record_proposal(task_name=task_name, item_id="rejected", proposal_payload={"action": "reject"}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="rejected", decision=HumanDecision.REJECTED)

    store.record_proposal(task_name=task_name, item_id="old", proposal_payload={"action": "stale"}, proposal_result={"ok": True})
    store.mark_superseded(task_name=task_name, item_id="old")

    assert {row.item_id for row in store.query_rows(LedgerQuery(task_name=task_name, unresolved_only=True))} == {
        "needs-review",
        "provider-error",
        "rejected",
    }
    assert [row.item_id for row in store.query_rows(LedgerQuery(task_name=task_name, provider_error_only=True))] == ["provider-error"]
    assert [row.item_id for row in store.query_rows(LedgerQuery(task_name=task_name, rejected_only=True))] == ["rejected"]
    assert {row.item_id for row in store.query_rows(LedgerQuery(task_name=task_name, not_approved_only=True))} == {
        "needs-review",
        "provider-error",
        "rejected",
    }
    assert all(
        row.item_id != "resolved" for row in store.query_rows(LedgerQuery(task_name=task_name, include_approved=False))
    )
    assert any(row.item_id == "old" for row in store.query_rows(LedgerQuery(task_name=task_name, include_superseded=True)))


def test_sticky_approved_rows_defaulted_from_resolver_query(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"

    store.record_proposal(task_name=task_name, item_id="approved-item", proposal_payload={"action": "noop"}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="approved-item", decision=HumanDecision.APPROVED)
    store.record_proposal(task_name=task_name, item_id="needs-review", proposal_payload={"action": "revise"}, proposal_result={"ok": True})

    unresolved = store.query_rows(LedgerQuery(task_name=task_name, unresolved_only=True, include_approved=False))
    assert {row.item_id for row in unresolved} == {"needs-review"}


def test_provenance_fields_round_trip(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "provenance-task"

    store.record_proposal(
        task_name=task_name,
        item_id="doc",
        proposal_payload={"proposal": True},
        proposal_run_id="proposal-run",
        proposal_code_commit="1234567",
        proposal_result={"ok": True},
    )
    store.record_review(
        task_name=task_name,
        item_id="doc",
        review_payload={"review": True},
        review_code_commit="2345678",
        review_run_id="review-run",
        review_result={"score": 1},
    )
    store.record_human_decision(
        task_name=task_name,
        item_id="doc",
        decision=HumanDecision.APPROVED,
        decision_code_commit="3456789",
        decision_payload={"reviewer": "alice"},
        notes="approved by script",
    )
    store.record_apply(
        task_name=task_name,
        item_id="doc",
        apply_payload={"apply": True},
        apply_code_commit="4567890",
        applied_in_commit="5678901",
        success=True,
    )

    reloaded = LedgerStore(tmp_path / "ledger.sqlite").get_row(task_name, "doc")
    assert reloaded is not None
    assert reloaded.proposal_code_commit == "1234567"
    assert reloaded.review_code_commit == "2345678"
    assert reloaded.human_decision_code_commit == "3456789"
    assert reloaded.apply_code_commit == "4567890"
    assert reloaded.applied_in_commit == "5678901"
    assert reloaded.proposal_run_id == "proposal-run"
    assert reloaded.review_run_id == "review-run"
    assert reloaded.human_decision_payload == {"reviewer": "alice"}
    assert reloaded.human_notes == "approved by script"


def test_clean_git_guard_blocks_non_dry_run_apply(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base_commit = _init_git_repo(repo)

    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"
    item_id = "repo-item"
    store.record_proposal(
        task_name=task_name,
        item_id=item_id,
        item_locator="seed.txt",
        proposal_payload={"action": "write"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(task_name=task_name, item_id=item_id, decision=HumanDecision.APPROVED)

    (repo / "seed.txt").write_text("dirty", encoding="utf-8")
    calls: list[str] = []

    def callback(_row, _dry_run: bool) -> tuple[dict[str, object], dict[str, object]]:
        calls.append("called")
        return ({"file": "seed.txt"}, {"updated": True})

    results = store.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=[item_id]),
        callback=callback,
        dry_run=False,
        policy=ApplyPolicy(require_clean_working_tree=True, working_directory=repo, capture_apply_code_commit=False, capture_applied_in_commit=False),
    )

    assert calls == []
    assert results[0].success is False
    assert "working tree has uncommitted changes" in (results[0].message or "")
    locked_row = store.get_row(task_name, item_id)
    assert locked_row is not None
    assert locked_row.machine_status == MachineStatus.PROVIDER_ERROR

    subprocess.run(["git", "-C", str(repo), "checkout", "--", "seed.txt"], check=True)
    results = store.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=[item_id]),
        callback=callback,
        dry_run=False,
        policy=ApplyPolicy(require_clean_working_tree=True, working_directory=repo, capture_apply_code_commit=True, capture_applied_in_commit=False),
    )

    assert calls == ["called"]
    assert results[0].success
    applied_row = store.get_row(task_name, item_id)
    assert applied_row is not None
    assert applied_row.machine_status == MachineStatus.APPLIED
    assert applied_row.apply_code_commit == base_commit


def test_apply_callback_and_ledger_update(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"
    item_id = "callback-item"
    store.record_proposal(
        task_name=task_name,
        item_id=item_id,
        item_locator="seed.txt",
        proposal_payload={"action": "write"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(task_name=task_name, item_id=item_id, decision=HumanDecision.APPROVED)

    def callback(_row, _dry_run: bool) -> dict[str, object]:
        return {"apply_payload": {"target": "seed.txt"}, "apply_result": {"status": "ok"}, "success": True}  # type: ignore[return-value]

    results = store.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=[item_id]),
        callback=callback,
        dry_run=False,
        policy=ApplyPolicy(working_directory=repo, capture_apply_code_commit=False),
    )

    assert results[0].success
    refreshed = store.get_row(task_name, item_id)
    assert refreshed is not None
    assert refreshed.apply_payload == {"target": "seed.txt"}
    assert refreshed.apply_result == {"status": "ok"}


def test_extras_round_trip(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "generic-task"
    extras = {"team": "engineering", "tags": ["generic", "ledger"], "priority": 2}

    store.record_proposal(
        task_name=task_name,
        item_id="item-extra",
        proposal_payload={"action": "noop"},
        proposal_result={"ok": True},
        extras=dict(extras),
    )

    assert store.get_row(task_name, "item-extra").extras == extras
    reloaded = LedgerStore(tmp_path / "ledger.sqlite").get_row(task_name, "item-extra")
    assert reloaded is not None
    assert reloaded.extras == extras


def test_history_versions_are_preserved(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "history-task"

    store.record_proposal(task_name=task_name, item_id="a", proposal_payload={"step": "1"})
    store.record_review(task_name=task_name, item_id="a", review_payload={"step": "2"})
    store.record_human_decision(task_name=task_name, item_id="a", decision=HumanDecision.REJECTED)

    versions = store.get_row_history(task_name, "a")
    assert len(versions) == 3
    assert versions[0].version == 1
    assert versions[1].version == 2
    assert versions[2].version == 3
    assert versions[0].machine_status == MachineStatus.PROPOSED
    assert versions[2].human_status == HumanDecision.REJECTED


def test_concurrent_write_transactions(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "concurrency-task"

    import threading

    def update_worker(index: int) -> None:
        store.record_proposal(
            task_name=task_name,
            item_id="same-item",
            proposal_payload={"worker": index},
            proposal_result={"ok": True},
        )

    threads = [threading.Thread(target=update_worker, args=(i,)) for i in range(1, 8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    row = store.get_row(task_name, "same-item")
    assert row is not None
    assert row.version == 7
    history = store.get_row_history(task_name, "same-item")
    assert len(history) == 7


def test_export_helpers(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "export-task"

    store.record_proposal(task_name=task_name, item_id="item-1", proposal_payload={"op": "a"}, proposal_result={"ok": True})
    store.record_proposal(task_name=task_name, item_id="item-2", proposal_payload={"op": "b"}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="item-2", decision=HumanDecision.REJECTED)

    csv_path = tmp_path / "rows.csv"
    jsonl_path = tmp_path / "rows.jsonl"
    md_path = tmp_path / "rows.md"
    store.export_csv(csv_path)
    store.export_jsonl(jsonl_path)
    store.export_markdown(md_path, query=LedgerQuery(task_name=task_name, unresolved_only=True))

    with csv_path.open("r", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 2
    assert {row["item_id"] for row in rows} == {"item-1", "item-2"}

    json_rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(json_rows) == 2
    assert json_rows[0]["task_name"] == task_name

    md_text = md_path.read_text(encoding="utf-8")
    assert "item-1" in md_text
    assert "item-2" in md_text


def test_import_jsonl_to_sqlite(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "legacy.jsonl"
    task_name = "legacy-task"
    legacy_rows = [
        LedgerRow(
            task_name=task_name,
            item_id="a",
            machine_status=MachineStatus.PROPOSED,
            human_status=HumanDecision.NEEDS_REVIEW,
            proposal_payload={"legacy": 1},
        ).to_dict(),
        LedgerRow(
            task_name=task_name,
            item_id="a",
            machine_status=MachineStatus.REVIEWED,
            human_status=HumanDecision.NEEDS_REVIEW,
            review_payload={"legacy": 2},
        ).to_dict(),
        LedgerRow(
            task_name=task_name,
            item_id="b",
            machine_status=MachineStatus.REVIEWED,
            human_status=HumanDecision.NEEDS_REVIEW,
            review_payload={"legacy": 3},
        ).to_dict(),
    ]
    with jsonl_path.open("w", encoding="utf-8") as fp:
        for row in legacy_rows:
            fp.write(json.dumps(row) + "\n")

    store = LedgerStore(tmp_path / "imported.sqlite")
    imported = store.import_jsonl(jsonl_path)
    assert imported == 3

    current = store.get_row(task_name, "a")
    assert current is not None
    assert current.version == 2
    assert current.review_payload == {"legacy": 2}
    assert store.get_row(task_name, "b") is not None

    history = store.get_row_history(task_name, "a")
    assert len(history) == 2
    assert history[0].version == 1
    assert history[1].version == 2


class _ToyHooks(DefaultResolverHooks):
    def prompt_edit(self, row: LedgerRow, input_fn) -> dict[str, Any]:
        text = input_fn("  approval_output_json: ").strip()
        if not text:
            return {}
        return json.loads(text)


def test_resolver_loop_and_hooks(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "resolver-task"

    store.record_proposal(
        task_name=task_name,
        item_id="i1",
        proposal_payload={"plan": "one"},
        proposal_result={"ok": True},
    )
    store.record_review(task_name=task_name, item_id="i1", review_payload={"risk": "low"}, review_result={"ok": True})
    store.record_proposal(
        task_name=task_name,
        item_id="i2",
        proposal_payload={"plan": "two"},
        proposal_result={"ok": True},
    )
    store.record_review(task_name=task_name, item_id="i2", review_payload={"risk": "low"}, review_result={"ok": True})

    scripted = _scripted_input(["e", "{\"approved\": true}", "keep-first", "r", "not enough context"])
    resolver = LedgerResolver(
        store=store,
        hooks=_ToyHooks(),
        input_fn=scripted,
        output_fn=lambda _message: None,
    )
    results = resolver.resolve(query=LedgerQuery(task_name=task_name, unresolved_only=True, include_approved=False))
    assert [r.item_id for r in results if r.status == "saved"] == ["i1", "i2"]

    row = store.get_row(task_name, "i1")
    assert row is not None
    assert row.human_status == HumanDecision.APPROVED
    assert row.human_decision_payload == {"edits": {"approved": True}, "approved_output": {"edits": {"approved": True}}}

    row2 = store.get_row(task_name, "i2")
    assert row2 is not None
    assert row2.human_status == HumanDecision.REJECTED


def test_resolver_dry_run_does_not_persist(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "resolver-task"
    store.record_proposal(task_name=task_name, item_id="i1", proposal_payload={"plan": "one"}, proposal_result={"ok": True})
    store.record_review(task_name=task_name, item_id="i1", review_payload={"risk": "low"}, review_result={"ok": True})

    scripted = _scripted_input(["a", "quick note"])
    resolver = LedgerResolver(
        store=store,
        hooks=DefaultResolverHooks(),
        input_fn=scripted,
        output_fn=lambda _message: None,
    )
    resolver.resolve(query=LedgerQuery(task_name=task_name, unresolved_only=True), dry_run=True)

    row = store.get_row(task_name, "i1")
    assert row is not None
    assert row.human_status == HumanDecision.NEEDS_REVIEW


class _ProgrammaticHooks(DefaultResolverHooks):
    def render_summary(self, row: LedgerRow) -> str:
        return f"{row.item_id}:{row.machine_status.value}:{row.human_status.value}"

    def preview_item(self, row: LedgerRow) -> dict[str, Any]:
        return {"preview": row.item_id}

    def validate_resolution(self, row: LedgerRow, edits: Any | None) -> ValidationResult:
        if row.item_id == "bad":
            if not isinstance(edits, dict) or edits.get("ok") is not True:
                return ValidationResult(valid=False, errors=["missing_ok"])
        if edits is None:
            return ValidationResult(valid=True)
        if isinstance(edits, dict):
            return ValidationResult(valid=True)
        return ValidationResult(valid=False, errors=["edits must be an object"])

    def derive_approved_output(self, row: LedgerRow, edits: Any | None) -> dict[str, Any]:
        if edits is None:
            return {"approved": True, "item_id": row.item_id}
        return {"approved": True, "edits": edits}


def test_resolver_session_pending_listing_and_get_row(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "programmatic-task"

    store.record_proposal(task_name=task_name, item_id="pending", proposal_payload={"a": 1}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="pending", decision=HumanDecision.NEEDS_REVIEW)

    store.record_proposal(task_name=task_name, item_id="approved", proposal_payload={"a": 2}, proposal_result={"ok": True})
    store.record_human_decision(task_name=task_name, item_id="approved", decision=HumanDecision.APPROVED)
    store.record_apply(task_name=task_name, item_id="approved", apply_payload={"applied": True}, apply_result={"status": "ok"})

    session = ResolverSession(store=store, hooks=_ProgrammaticHooks())
    pending = session.list_pending(LedgerQuery(task_name=task_name, unresolved_only=True))
    assert [row.item_id for row in pending] == ["pending"]

    pending_row = session.get_row(task_name, "pending")
    assert pending_row is not None
    assert isinstance(pending_row, ResolverRowView)


def test_resolver_session_preview_and_row_structure(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "programmatic-task"
    store.record_proposal(task_name=task_name, item_id="item-x", proposal_payload={"op": "x"}, proposal_result={"ok": True})
    session = ResolverSession(store=store, hooks=_ProgrammaticHooks())

    view = session.get_next(LedgerQuery(task_name=task_name, unresolved_only=True))
    assert view is not None
    assert view.item_id == "item-x"
    assert view.machine_status == "proposed"
    assert view.human_status == "needs_review"
    assert view.decision_source is None
    assert view.rendered_summary.startswith("item-x")
    assert session.preview_row(view) == {"preview": "item-x"}


def test_resolver_session_recommendation_only_does_not_persist(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "programmatic-task"
    store.record_proposal(task_name=task_name, item_id="item-1", proposal_payload={"op": "x"}, proposal_result={"ok": True})

    session = ResolverSession(store=store, hooks=_ProgrammaticHooks())
    view = session.get_row(task_name, "item-1")
    assert view is not None
    rec = session.build_recommendation(
        view,
        ResolverAction.APPROVE_WITH_EDIT,
        edits={"approved_output": True},
        note="policy pass",
        decision_source="agent",
        decision_actor="agent-0",
        decision_code_commit="abc123",
    )
    result = session.apply_recommendation(rec, apply=False)
    assert result.status == "recommendation_only"

    row = store.get_row(task_name, "item-1")
    assert row is not None
    assert row.human_status == HumanDecision.NEEDS_REVIEW


def test_resolver_session_apply_mode_persists_with_provenance(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "programmatic-task"
    store.record_proposal(task_name=task_name, item_id="item-2", proposal_payload={"op": "x"}, proposal_result={"ok": True})

    session = ResolverSession(store=store, hooks=_ProgrammaticHooks())
    row = session.get_row(task_name, "item-2")
    assert row is not None
    rec = session.build_recommendation(
        row,
        ResolverAction.APPROVE,
        note="approved by policy",
        decision_source="policy",
        decision_actor="policy-bot",
        decision_code_commit="def456",
    )
    saved = session.apply_recommendation(rec)
    assert saved.status == "applied"
    persisted = store.get_row(task_name, "item-2")
    assert persisted is not None
    assert persisted.human_status == HumanDecision.APPROVED
    assert persisted.human_decision_source == "policy"
    assert persisted.human_decision_actor == "policy-bot"
    assert persisted.human_decision_code_commit == "def456"


def test_resolver_validation_failures_block_apply(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.sqlite")
    task_name = "programmatic-task"
    store.record_proposal(task_name=task_name, item_id="bad", proposal_payload={"op": "x"}, proposal_result={"ok": True})

    session = ResolverSession(store=store, hooks=_ProgrammaticHooks())
    view = session.get_row(task_name, "bad")
    assert view is not None
    rec = session.build_recommendation(view, ResolverAction.APPROVE_WITH_EDIT, edits={"ok": "no"})
    assert rec.validation.valid is False
    applied = session.apply_recommendation(rec)
    assert applied.status == "validation_failed"

    row = store.get_row(task_name, "bad")
    assert row is not None
    assert row.human_status == HumanDecision.NEEDS_REVIEW


def test_resolver_cli_resolve_runs_with_no_matches(tmp_path: Path, capsys) -> None:
    ledger_path = tmp_path / "ledger.sqlite"
    result = ledger_cli.main(
        [
            "resolve",
            "--ledger",
            str(ledger_path),
            "--task-name",
            "empty-task",
            "--dry-run",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "No rows match current query." in output
