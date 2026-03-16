"""Generic ledger service examples."""

from __future__ import annotations

from pathlib import Path

from respkit.ledger import LedgerStore, ResolverAction
from respkit.service import LedgerService, DefaultTaskServiceAdapter


class ToyAdapter(DefaultTaskServiceAdapter):
    """Small adapter used by the local demo."""

    def __init__(self) -> None:
        super().__init__(task_name="service-demo-task")

    def preview_item(self, row):
        return {"item_id": row.item_id, "locator": row.item_locator}


def _build_demo_ledger(path: Path) -> tuple[LedgerStore, str]:
    task_name = "service-demo-task"
    store = LedgerStore(path)
    store.record_proposal(
        task_name=task_name,
        item_id="item-a",
        item_locator="notes/item-a.txt",
        proposal_payload={"status": "propose"},
        proposal_result={"ok": True},
    )
    store.record_proposal(
        task_name=task_name,
        item_id="item-b",
        item_locator="notes/item-b.txt",
        proposal_payload={"status": "propose"},
        proposal_result={"ok": True},
    )
    return store, task_name


def _run_demo() -> None:
    path = Path(".demo_ledger_service.sqlite")
    if path.exists():
        path.unlink()
    store, task_name = _build_demo_ledger(path)

    service = LedgerService(path, adapters=[ToyAdapter()])

    print("ledger.summary =", service.summary({"task_name": task_name}))
    print("rows.list =", service.list_rows({"task_name": task_name, "unresolved_only": True})["rows"])
    print("row.get =", service.get_row({"task_name": task_name, "item_id": "item-a"})["row"])
    print("preview =", service.preview_row({"task_name": task_name, "item_id": "item-a"})["preview"])
    print("actions =", service.list_actions({"task_name": task_name})["actions"])
    print(
        "invoke-action =", 
        service.invoke_action(
            {
                "task_name": task_name,
                "action": "approve",
                "item_ids": ["item-a"],
                "decision_note": "demo pre-apply",
                "decision_source": "agent",
                "decision_actor": "service-demo",
            }
        ),
    )

    recommend = service.decide(
        {
            "task_name": task_name,
            "item_id": "item-a",
            "action": ResolverAction.APPROVE_WITH_EDIT.value,
            "apply": False,
            "edits": {"approved": True},
            "decision_source": "policy-agent",
            "decision_actor": "cli-service",
        }
    )
    print("recommendation-only =", recommend["status"])
    applied = service.decide(
        {
            "task_name": task_name,
            "item_id": "item-a",
            "action": ResolverAction.APPROVE.value,
            "apply": True,
            "decision_source": "agent",
            "decision_actor": "service-client",
            "decision_code_commit": "0000000",
        }
    )
    print("applied =", applied["status"])
    detail = service.get_row({"task_name": task_name, "item_id": "item-a"})["row"]
    print("decision_source =", detail["decision_source"], "decision_actor =", detail["decision_actor"])

    print("export-jsonl =", service.export({"task_name": task_name, "format": "jsonl"})["format"])
    service.close()
    store.close()


if __name__ == "__main__":
    _run_demo()
