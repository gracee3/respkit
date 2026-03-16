"""Generic example of the reusable adjudication ledger abstraction."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from respkit.ledger import ApplyPolicy, HumanDecision, LedgerQuery, LedgerStore, LedgerRow


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_root), "init"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.email", "ledger@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.name", "Ledger Example"], check=True)

    notes_root = repo_root / "notes"
    notes_root.mkdir(exist_ok=True)
    (notes_root / "item-a.txt").write_text("original-a\n", encoding="utf-8")
    (notes_root / "item-b.txt").write_text("original-b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "add", "notes"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "seed note corpus"], check=True)


def _apply_callback(repo_root: Path, *, fail_on_item: str | None = None):
    def _callback(row: LedgerRow, dry_run: bool):
        if row.item_locator is None:
            return {"error": "missing locator"}, {"applied": False}
        if row.item_id == fail_on_item and not dry_run:
            return {"error": "forced failure"}, {"applied": False}

        target = repo_root / row.item_locator
        if not dry_run:
            current = target.read_text(encoding="utf-8")
            target.write_text(current + "applied-by-ledger\n", encoding="utf-8")
        return {"target": str(target), "dry_run": dry_run}, {"applied": not dry_run}

    return _callback


def build_ledger(ledger_path: Path, repo_root: Path) -> tuple[LedgerStore, str]:
    store = LedgerStore(ledger_path)
    task_name = "generic-corpus-task"

    for item_id in ("item-a", "item-b"):
        store.record_proposal(
            task_name=task_name,
            item_id=item_id,
            item_locator=f"notes/{item_id}.txt",
            proposal_payload={"operation": "append-note"},
            proposal_result={"ok": True},
            extras={"priority": 1},
        )

        store.record_review(
            task_name=task_name,
            item_id=item_id,
            review_payload={"reviewer": "example-reviewer"},
            review_result={"approve_if_possible": True},
        )

        if item_id == "item-b":
            store.record_human_decision(
                task_name=task_name,
                item_id=item_id,
                decision=HumanDecision.REJECTED,
                decision_payload={"reason": "manual rejection"},
            )
        else:
            store.record_human_decision(task_name=task_name, item_id=item_id, decision=HumanDecision.APPROVED)

    # Show reload safety and extension payload visibility.
    reloaded = LedgerStore(ledger_path)
    for row in reloaded.query_rows(LedgerQuery(task_name=task_name)):
        print(f"seeded: {row.item_id}, machine={row.machine_status.value}, human={row.human_status.value}, extras={row.extras}")

    # Demonstrate selective rerun filtering.
    unresolved = reloaded.query_rows(LedgerQuery(task_name=task_name, unresolved_only=True))
    print("unresolved:", [row.item_id for row in unresolved])
    not_approved = reloaded.query_rows(LedgerQuery(task_name=task_name, not_approved_only=True))
    print("not_approved:", [row.item_id for row in not_approved])

    # Dry-run apply never mutates files and does not require clean-tree checks.
    dry_results = reloaded.run_apply(
        query=LedgerQuery(task_name=task_name, machine_statuses=None, human_statuses={HumanDecision.APPROVED}),
        callback=_apply_callback(repo_root),
        dry_run=True,
        policy=ApplyPolicy(require_clean_working_tree=False, working_directory=repo_root),
    )
    print("dry-run results:", [(r.item_id, r.success, r.apply_result) for r in dry_results])

    # Guarded non-dry-run apply path in action:
    # first run with a dirty tree to show guard behavior.
    (repo_root / "notes" / "item-a.txt").write_text("locally-modified\n", encoding="utf-8")
    guarded_failures = reloaded.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=["item-a"]),
        callback=_apply_callback(repo_root),
        dry_run=False,
        policy=ApplyPolicy(
            require_clean_working_tree=True,
            working_directory=repo_root,
            capture_apply_code_commit=True,
            capture_applied_in_commit=True,
        ),
    )
    print("guard-failed:", [(r.item_id, r.success, r.message) for r in guarded_failures])

    # Restore cleanliness and apply for real.
    subprocess.run(["git", "-C", str(repo_root), "checkout", "--", "notes/item-a.txt"], check=True)
    apply_results = reloaded.run_apply(
        query=LedgerQuery(task_name=task_name, item_ids=["item-a"]),
        callback=_apply_callback(repo_root),
        dry_run=False,
        policy=ApplyPolicy(
            require_clean_working_tree=True,
            working_directory=repo_root,
            capture_apply_code_commit=True,
            capture_applied_in_commit=True,
        ),
    )
    print("apply results:", [(r.item_id, r.success, r.apply_code_commit, r.applied_in_commit) for r in apply_results])

    return reloaded, task_name


def show_final_rows(store: LedgerStore, task_name: str) -> None:
    for row in store.query_rows(LedgerQuery(task_name=task_name)):
        print(
            "final",
            row.item_id,
            row.machine_status.value,
            row.human_status.value,
            row.apply_code_commit,
            row.applied_in_commit,
            row.extras,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generic ledger abstraction example")
    parser.add_argument("--repo", default=".ledger_demo_repo", type=Path)
    parser.add_argument("--ledger", default=".ledger_demo.sqlite", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    ledger_path = args.ledger.resolve()

    if repo.exists():
        shutil.rmtree(repo, ignore_errors=True)
    if ledger_path.exists():
        ledger_path.unlink(missing_ok=True)
    _init_repo(repo)

    store, task_name = build_ledger(ledger_path=ledger_path, repo_root=repo)
    show_final_rows(LedgerStore(ledger_path), task_name)


if __name__ == "__main__":
    main()
