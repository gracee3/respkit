"""Generic interactive resolver flow for adjudication ledgers."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .models import HumanDecision, LedgerRow
from .store import LedgerStore
from .query import LedgerQuery


class ResolverHooks(Protocol):
    """Task-extensible hooks used by the generic resolver."""

    def render_summary(self, row: LedgerRow) -> str:
        """Return a short summary block for one row."""

    def preview_item(self, row: LedgerRow) -> None:
        """Optional preview/open behavior for one row."""

    def prompt_edit(self, row: LedgerRow, input_fn: Callable[[str], str]) -> dict[str, Any]:
        """Collect task-specific edit fields for approve-with-edit."""

    def validate_resolution(self, row: LedgerRow, edits: dict[str, Any] | None) -> tuple[bool, str | None]:
        """Validate task-specific edits and return (is_valid, error_message)."""

    def derive_approved_output(self, row: LedgerRow, edits: dict[str, Any] | None) -> Any:
        """Compute any decision payload derived from edits."""

    def risk_flags(self, row: LedgerRow) -> list[str]:
        """Return optional risk/review flags for a row."""


class DefaultResolverHooks:
    """Minimal default hooks that keep the resolver task-agnostic."""

    def render_summary(self, row: LedgerRow) -> str:
        payload = row.to_dict()
        proposal = payload.get("proposal_payload")
        review = payload.get("review_payload")
        proposal_text = json.dumps(proposal, ensure_ascii=False) if isinstance(proposal, (dict, list)) else str(proposal)
        review_text = json.dumps(review, ensure_ascii=False) if isinstance(review, (dict, list)) else str(review)

        if row.item_locator:
            locator = row.item_locator
        else:
            locator = "-"

        return (
            f"{row.item_id} (task={row.task_name})\n"
            f"  locator: {locator}\n"
            f"  machine: {row.machine_status.value}\n"
            f"  human: {row.human_status.value}\n"
            f"  proposal: {proposal_text}\n"
            f"  review: {review_text}\n"
        )

    def preview_item(self, row: LedgerRow) -> None:
        if row.item_locator:
            print(f"preview: {row.item_locator}")

    def prompt_edit(self, row: LedgerRow, input_fn: Callable[[str], str]) -> dict[str, Any]:
        raw = input_fn("  edits (json or blank): ").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def validate_resolution(self, _row: LedgerRow, edits: dict[str, Any] | None) -> tuple[bool, str | None]:
        if edits is None:
            return True, None
        try:
            json.dumps(edits, ensure_ascii=False)
        except TypeError as exc:
            return False, str(exc)
        return True, None

    def derive_approved_output(self, _row: LedgerRow, edits: dict[str, Any] | None) -> Any:
        return {"edits": edits} if edits else None

    def risk_flags(self, _row: LedgerRow) -> list[str]:
        return []


@dataclass(frozen=True)
class ResolverDecision:
    """Resolved decision payload for one ledger row."""

    decision: HumanDecision
    decision_payload: Any | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ResolverResult:
    """Per-row resolver result."""

    task_name: str
    item_id: str
    status: str
    decision: HumanDecision | None = None


class LedgerResolver:
    """Interactive command-loop resolver over pending ledger rows."""

    def __init__(
        self,
        store: LedgerStore,
        hooks: ResolverHooks | None = None,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.store = store
        self.hooks = hooks or DefaultResolverHooks()
        self._input_fn = input_fn
        self._output_fn = output_fn

    @staticmethod
    def _action_prompt() -> str:
        return (
            "\nActions:\n"
            "  [a] approve\n"
            "  [e] approve with edits\n"
            "  [r] reject\n"
            "  [f] needs_review/follow-up\n"
            "  [s] skip\n"
            "  [p] preview/open\n"
            "  [q] quit\n"
            "  choose: "
        )

    def _resolve_action(self, row: LedgerRow) -> tuple[str | None, ResolverDecision | None]:
        while True:
            action = self._input_fn(self._action_prompt()).strip().lower()
            if action in {"q", "quit"}:
                return "quit", None
            if action in {"s", "skip"}:
                return "skip", None
            if action in {"p", "preview"}:
                self.hooks.preview_item(row)
                continue
            if action in {"a", "approve"}:
                notes = self._input_fn("  notes (optional): ").strip() or None
                return "approve", ResolverDecision(HumanDecision.APPROVED, notes=notes)
            if action in {"e", "edit", "approve_edit"}:
                try:
                    edits = self.hooks.prompt_edit(row, self._input_fn)
                except Exception as exc:  # noqa: BLE001
                    self._output_fn(f"  invalid edit input: {exc}")
                    continue
                valid, error = self.hooks.validate_resolution(row, edits)
                if not valid:
                    self._output_fn(f"  invalid edits: {error}")
                    continue
                approved_output = self.hooks.derive_approved_output(row, edits)
                return "approve_with_edit", ResolverDecision(
                    decision=HumanDecision.APPROVED,
                    decision_payload={
                        "edits": edits,
                        "approved_output": approved_output,
                    },
                    notes=self._input_fn("  notes (optional): ").strip() or None,
                )
            if action in {"r", "reject"}:
                notes = self._input_fn("  reason: ").strip() or None
                return "reject", ResolverDecision(HumanDecision.REJECTED, notes=notes)
            if action in {"f", "followup", "needs"}:
                notes = self._input_fn("  follow-up notes (optional): ").strip() or None
                return "follow", ResolverDecision(HumanDecision.NEEDS_REVIEW, notes=notes)

            self._output_fn("  unknown action; use one of a/e/r/f/s/p/q")

    def resolve(self, *, query: LedgerQuery, dry_run: bool = False) -> list[ResolverResult]:
        """Run the resolver loop against rows selected by query.

        Dry-run mode does not persist any decision and reports what would be saved.
        """

        results: list[ResolverResult] = []
        rows = self.store.query_rows(query)

        if not rows:
            self._output_fn("No rows match current query.")
            return results

        for row in rows:
            self._output_fn("\n" + "-" * 72)
            self._output_fn(self.hooks.render_summary(row))
            flags = self.hooks.risk_flags(row)
            if flags:
                self._output_fn(f"  risk_flags: {', '.join(flags)}")

            action, decision = self._resolve_action(row)

            if action == "quit":
                self._output_fn("Resolver stopped by user.")
                break
            if action in {"skip", "preview"} or decision is None:
                continue

            if dry_run:
                self._output_fn(
                    f"  [dry-run] would record {decision.decision.value} for {row.item_id}"
                    f"{' with payload' if decision.decision_payload is not None else ''}"
                )
                results.append(ResolverResult(row.task_name, row.item_id, "dry_run", decision.decision))
                continue

            saved = self.store.record_human_decision(
                task_name=row.task_name,
                item_id=row.item_id,
                decision=decision.decision,
                decision_payload=decision.decision_payload,
                notes=decision.notes,
            )
            results.append(ResolverResult(saved.task_name, saved.item_id, "saved", decision.decision))
            self._output_fn(f"  decision recorded: {decision.decision.value}")

        return results


def load_hook_class(hook_target: str) -> type[ResolverHooks]:
    """Load a resolver hook class from ``module:qualname``."""

    module_name, _, attr_name = hook_target.partition(":")
    if not module_name or not attr_name:
        raise ValueError(f"invalid hook target '{hook_target}', expected module:Class")
    module = importlib.import_module(module_name)
    hook = getattr(module, attr_name, None)
    if hook is None:
        raise AttributeError(f"missing hook '{attr_name}' in {module_name}")
    return hook
