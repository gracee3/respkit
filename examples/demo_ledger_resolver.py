"""Generic resolver example with a tiny task-specific hook."""

from __future__ import annotations

from pathlib import Path

from respkit.ledger import (
    DefaultResolverHooks,
    LedgerQuery,
    LedgerResolver,
    LedgerStore,
)


class ToyResolverHooks(DefaultResolverHooks):
    """Tiny task hook used by the demo to show extension points."""

    def render_summary(self, row):
        payload = row.to_dict()
        return (
            f"item={row.item_id}\n"
            f"  proposal={payload.get('proposal_payload')}\n"
            f"  review={payload.get('review_payload')}\n"
        )

    def preview_item(self, row):
        if row.item_locator:
            print(f"open://{row.item_locator}")

    def risk_flags(self, row):
        flags: list[str] = []
        if row.review_payload and isinstance(row.review_payload, dict):
            if row.review_payload.get("risk") == "high":
                flags.append("reviewed as high risk")
        return flags

    def prompt_edit(self, row, input_fn):
        raw = input_fn("  output_note (json or blank): ").strip()
        if not raw:
            return {}
        return {"note": raw}

    def validate_resolution(self, _row, edits):
        if edits is None:
            return True, None
        if not isinstance(edits, dict):
            return False, "edits must be an object"
        return True, None

    def derive_approved_output(self, _row, edits):
        return {"approved_output": edits or {"approved": True}}


def _scripted_input(values):
    iterator = iter(values)

    def _next(prompt: str) -> str:
        try:
            return str(next(iterator))
        except StopIteration:
            return "s"

    return _next


def main() -> None:
    ledger_path = Path(".demo_ledger_resolver.sqlite")
    if ledger_path.exists():
        ledger_path.unlink()

    store = LedgerStore(ledger_path)
    task_name = "generic-task"

    store.record_proposal(
        task_name=task_name,
        item_id="alpha",
        item_locator="notes/alpha.txt",
        proposal_payload={"op": "append"},
        proposal_result={"ok": True},
    )
    store.record_review(task_name=task_name, item_id="alpha", review_payload={"risk": "low"}, review_result={"accept": True})

    store.record_proposal(
        task_name=task_name,
        item_id="bravo",
        item_locator="notes/bravo.txt",
        proposal_payload={"op": "replace"},
        proposal_result={"ok": True},
    )
    store.record_review(
        task_name=task_name,
        item_id="bravo",
        review_payload={"risk": "high"},
        review_result={"accept": True},
    )

    # Force deterministic script: approve, approve-with-edit, then skip.
    scripted = _scripted_input(
        [
            "a",  # approve alpha
            "",  # notes
            "e",  # approve-with-edit bravo
            "keep-first-line",
            "",  # optional notes for bravo
        ]
    )

    resolver = LedgerResolver(store, hooks=ToyResolverHooks(), input_fn=scripted)
    resolver.resolve(query=LedgerQuery(task_name=task_name, unresolved_only=True, include_approved=False))

    # Show export formats for review-friendly output.
    store.export_csv(Path(".demo_ledger_resolver.csv"))
    store.export_jsonl(Path(".demo_ledger_resolver.jsonl"))
    store.export_markdown(Path(".demo_ledger_resolver.md"))

    print(
        [
            row.human_status.value
            for row in store.query_rows(LedgerQuery(task_name=task_name, include_approved=True))
        ]
    )


if __name__ == "__main__":
    main()
