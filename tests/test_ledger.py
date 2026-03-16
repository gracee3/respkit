from __future__ import annotations

import subprocess
from pathlib import Path

from respkit.ledger import ApplyPolicy, HumanDecision, LedgerQuery, LedgerStore, LedgerRow, MachineStatus
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
    assert commit
    return commit


def _build_store(tmp_path: Path) -> tuple[LedgerStore, str]:
    return LedgerStore(tmp_path / "ledger.jsonl"), "generic-task"


def test_row_creation_and_status_transitions(tmp_path):
    store, task_name = _build_store(tmp_path)

    proposal = store.record_proposal(
        task_name=task_name,
        item_id="doc-1",
        item_locator="docs/doc-1.txt",
        proposal_payload={"action": "rename"},
        proposal_result={"ok": True},
    )
    assert proposal.machine_status == MachineStatus.PROPOSED
    assert proposal.human_status == HumanDecision.NEEDS_REVIEW

    reviewed = store.record_review(
        task_name=task_name,
        item_id="doc-1",
        review_payload={"notes": "looks_ok"},
        review_result={"score": 0.9},
    )
    assert reviewed.machine_status == MachineStatus.REVIEWED

    approved = store.record_human_decision(
        task_name=task_name,
        item_id="doc-1",
        decision=HumanDecision.APPROVED,
        decision_payload={"reviewer": "alice"},
    )
    assert approved.machine_status == MachineStatus.APPLY_READY
    assert approved.human_status == HumanDecision.APPROVED

    applied = store.record_apply(
        task_name=task_name,
        item_id="doc-1",
        apply_payload={"operation": "noop"},
        apply_result={"applied": True},
        success=True,
    )
    assert applied.machine_status == MachineStatus.APPLIED
    assert applied.apply_payload == {"operation": "noop"}


def test_selective_rerun_filters(tmp_path):
    store, task_name = _build_store(tmp_path)

    store.record_proposal(
        task_name=task_name,
        item_id="resolved",
        proposal_payload={"action": "noop"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(
        task_name=task_name,
        item_id="resolved",
        decision=HumanDecision.APPROVED,
    )
    store.record_apply(
        task_name=task_name,
        item_id="resolved",
        apply_payload={"noop": True},
        apply_result={"applied": True},
        success=True,
    )

    store.record_proposal(
        task_name=task_name,
        item_id="needs-review",
        proposal_payload={"action": "review"},
        proposal_result={"ok": True},
    )

    store.record_proposal(
        task_name=task_name,
        item_id="provider-error",
        proposal_payload={"action": "fail"},
        proposal_result={"ok": False},
        machine_status=MachineStatus.PROVIDER_ERROR,
    )

    store.record_proposal(
        task_name=task_name,
        item_id="rejected",
        proposal_payload={"action": "reject"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(
        task_name=task_name,
        item_id="rejected",
        decision=HumanDecision.REJECTED,
    )

    store.record_proposal(
        task_name=task_name,
        item_id="old",
        proposal_payload={"action": "stale"},
        proposal_result={"ok": True},
    )
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


def test_provenance_fields_round_trip(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    store = LedgerStore(ledger_path)
    task_name = "provenance-task"

    store.record_proposal(
        task_name=task_name,
        item_id="doc",
        proposal_payload={"proposal": True},
        proposal_run_id="proposal-run",
        proposal_code_commit="1234567",
        extras={"source": "pipeline"},
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
    )
    store.record_apply(
        task_name=task_name,
        item_id="doc",
        apply_payload={"apply": True},
        apply_code_commit="4567890",
        applied_in_commit="5678901",
        success=True,
    )

    reloaded = LedgerStore(ledger_path).get_row(task_name, "doc")
    assert reloaded is not None
    assert reloaded.proposal_code_commit == "1234567"
    assert reloaded.review_code_commit == "2345678"
    assert reloaded.human_decision_code_commit == "3456789"
    assert reloaded.apply_code_commit == "4567890"
    assert reloaded.applied_in_commit == "5678901"
    assert reloaded.proposal_run_id == "proposal-run"
    assert reloaded.review_run_id == "review-run"


def test_clean_git_guard_blocks_non_dry_run_apply(tmp_path):
    repo = tmp_path / "repo"
    base_commit = _init_git_repo(repo)

    store, task_name = _build_store(tmp_path)
    item_id = "repo-item"
    store.record_proposal(
        task_name=task_name,
        item_id=item_id,
        item_locator="seed.txt",
        proposal_payload={"action": "write"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(
        task_name=task_name,
        item_id=item_id,
        decision=HumanDecision.APPROVED,
        decision_payload={"decision": "go"},
    )

    (repo / "seed.txt").write_text("dirty", encoding="utf-8")
    calls: list[str] = []

    def _callback(_row: LedgerRow, _dry_run: bool) -> tuple[dict[str, object], dict[str, object]]:
        calls.append("called")
        return ({"file": "seed.txt"}, {"updated": True})

    results = store.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=[item_id]),
        callback=_callback,
        dry_run=False,
        policy=ApplyPolicy(
            require_clean_working_tree=True,
            working_directory=repo,
            capture_apply_code_commit=False,
            capture_applied_in_commit=False,
        ),
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
        callback=_callback,
        dry_run=False,
        policy=ApplyPolicy(
            require_clean_working_tree=True,
            working_directory=repo,
            capture_apply_code_commit=True,
            capture_applied_in_commit=False,
        ),
    )

    assert calls == ["called"]
    assert results[0].success
    applied_row = store.get_row(task_name, item_id)
    assert applied_row is not None
    assert applied_row.machine_status == MachineStatus.APPLIED
    assert applied_row.apply_code_commit == base_commit


def test_apply_callback_and_ledger_update(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    store, task_name = _build_store(tmp_path)
    item_id = "callback-item"
    store.record_proposal(
        task_name=task_name,
        item_id=item_id,
        item_locator="seed.txt",
        proposal_payload={"action": "write"},
        proposal_result={"ok": True},
    )
    store.record_human_decision(
        task_name=task_name,
        item_id=item_id,
        decision=HumanDecision.APPROVED,
        decision_payload={"decision": "go"},
    )

    def _callback(_row: LedgerRow, _dry_run: bool) -> dict[str, object]:
        return {
            "apply_payload": {"target": "seed.txt"},
            "apply_result": {"status": "ok"},
            "success": True,
        }

    results = store.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=[item_id]),
        callback=_callback,
        dry_run=False,
        policy=ApplyPolicy(working_directory=repo, capture_apply_code_commit=False),
    )

    assert results[0].success
    assert results[0].apply_payload == {"target": "seed.txt"}
    refreshed = store.get_row(task_name, item_id)
    assert refreshed is not None
    assert refreshed.apply_payload == {"target": "seed.txt"}
    assert refreshed.apply_result == {"status": "ok"}


def test_extras_round_trip(tmp_path):
    store, task_name = _build_store(tmp_path)
    extras = {"team": "engineering", "tags": ["generic", "ledger"], "priority": 2}

    store.record_proposal(
        task_name=task_name,
        item_id="item-extra",
        proposal_payload={"action": "noop"},
        proposal_result={"ok": True},
        extras=dict(extras),
    )

    assert store.get_row(task_name, "item-extra").extras == extras

    reloaded = LedgerStore(tmp_path / "ledger.jsonl").get_row(task_name, "item-extra")
    assert reloaded is not None
    assert reloaded.extras == extras
