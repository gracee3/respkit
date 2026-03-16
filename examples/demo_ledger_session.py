"""Programmatic ledger resolver session demo.

This example is intentionally generic and task-agnostic.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from respkit.ledger import (
    DefaultResolverHooks,
    LedgerQuery,
    LedgerRow,
    LedgerStore,
    ResolverAction,
    ResolverSession,
)


class ToyResolverHooks(DefaultResolverHooks):
    """Tiny hook set used for the demo."""

    def render_summary(self, row: LedgerRow) -> str:
        return f"item={row.item_id} machine={row.machine_status.value} human={row.human_status.value}"

    def preview_item(self, row: LedgerRow) -> dict[str, str]:
        return {"path": row.item_locator or "<no-locator>"}

    def validate_resolution(self, row: LedgerRow, edits: dict[str, object] | None):
        if row.item_id == "bad-item":
            if not edits or edits.get("approve") is not True:
                return False, "bad-item requires {\"approve\": true}"
        return True, None

    def derive_approved_output(self, row: LedgerRow, edits: dict[str, object] | None):
        return {"approved_item_id": row.item_id, "edits": edits or {}}


def _build_store(path: Path) -> tuple[LedgerStore, str]:
    task_name = "session-demo-task"
    store = LedgerStore(path)
    store.record_proposal(
        task_name=task_name,
        item_id="item-a",
        item_locator="notes/item-a.txt",
        proposal_payload={"op": "append-note"},
        proposal_result={"ok": True},
        extras={"priority": 1},
    )
    store.record_review(
        task_name=task_name,
        item_id="item-a",
        review_payload={"review": "ok"},
        review_result={"accept": True},
    )
    store.record_proposal(
        task_name=task_name,
        item_id="bad-item",
        item_locator="notes/bad-item.txt",
        proposal_payload={"op": "append-note"},
        proposal_result={"ok": True},
        extras={"priority": 2},
    )
    store.record_review(
        task_name=task_name,
        item_id="bad-item",
        review_payload={"review": "risky"},
        review_result={"accept": True},
    )
    return store, task_name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default=Path(".demo_ledger_session.sqlite"), type=Path)
    args = parser.parse_args()

    if args.ledger.exists():
        args.ledger.unlink()
    store, task_name = _build_store(args.ledger)
    hooks = ToyResolverHooks()
    session = ResolverSession(store=store, hooks=hooks, decision_source="agent", decision_actor="session-demo")

    print("pending rows:", [row.item_id for row in session.list_pending(LedgerQuery(task_name=task_name, unresolved_only=True))])

    # Recommendation mode: suggest a decision and do not persist it.
    row = session.get_row(task_name, "item-a")
    if row is not None:
        recommendation = session.build_recommendation(
            row,
            ResolverAction.APPROVE_WITH_EDIT,
            edits={"approve": True},
            note="dry-run policy approval",
            decision_source="policy",
            decision_actor="policy-bot",
        )
        rec_result = session.apply_recommendation(recommendation, apply=False)
        print("recommendation-only:", rec_result.status, rec_result.action.value if rec_result.action else None)
        print("still pending:", store.get_row(task_name, "item-a").human_status.value)

    # Apply decision path: persist with provenance metadata.
    if row is not None:
        recommendation = session.build_recommendation(
            row,
            ResolverAction.APPROVE_WITH_EDIT,
            edits={"approve": True},
            note="persisted policy approval",
            decision_source="agent",
            decision_actor="session-demo",
            decision_code_commit="0000000",
        )
        session.apply_recommendation(recommendation)
        print("persisted:", store.get_row(task_name, "item-a").human_status.value)

    # Process remaining rows and show hook-based previews.
    next_row = session.get_next(LedgerQuery(task_name=task_name, unresolved_only=True))
    while next_row is not None:
        preview = session.preview_row(next_row)
        print("preview:", preview)
        rec = session.build_recommendation(
            next_row,
            ResolverAction.REJECT,
            note="agent rejects by rule",
            decision_source="agent",
            decision_actor="session-demo",
        )
        out = session.apply_recommendation(rec)
        print(out.task_name, out.item_id, out.status, out.decision)
        next_row = session.get_next(LedgerQuery(task_name=task_name, unresolved_only=True))

    # Final query, including approved rows.
    final_rows = store.query_rows(LedgerQuery(task_name=task_name, include_approved=True))
    print("final human statuses:", [row.human_status.value for row in final_rows])


if __name__ == "__main__":
    main()
