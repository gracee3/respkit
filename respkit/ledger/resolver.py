"""Programmatic and interactive adjudication resolution abstractions."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from .models import HumanDecision, LedgerRow
from .query import LedgerQuery
from .git import get_head_commit
from .store import LedgerStore


class ResolverAction(str, Enum):
    """Action taken for one ledger row."""

    APPROVE = "approve"
    APPROVE_WITH_EDIT = "approve_with_edit"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"
    SKIP = "skip"


class ResolverHooks(Protocol):
    """Task-extensible hooks used by the generic resolver."""

    def render_summary(self, row: LedgerRow) -> str:
        """Return a short summary block for one row."""

    def preview_item(self, row: LedgerRow) -> Any | None:
        """Optional preview/open behavior for one row."""

    def prompt_edit(self, row: LedgerRow, input_fn: Callable[[str], str]) -> Any:
        """Collect task-specific edit fields for approve-with-edit."""

    def validate_resolution(self, row: LedgerRow, edits: Any | None) -> tuple[bool, str | None] | "ValidationResult":
        """Validate task-specific edits."""

    def derive_approved_output(self, row: LedgerRow, edits: Any | None) -> Any:
        """Compute approved output from task edits."""

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
            return {"path": row.item_locator}
        return None

    def prompt_edit(self, row: LedgerRow, input_fn: Callable[[str], str]) -> Any:
        raw = input_fn("  edits (json or blank): ").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def validate_resolution(self, _row: LedgerRow, edits: Any | None) -> tuple[bool, str | None]:
        if edits is None:
            return True, None
        try:
            json.dumps(edits, ensure_ascii=False)
        except TypeError as exc:  # noqa: BLE001
            return False, str(exc)
        return True, None

    def derive_approved_output(self, _row: LedgerRow, edits: Any | None) -> Any:
        return {"edits": edits} if edits else None

    def risk_flags(self, _row: LedgerRow) -> list[str]:
        return []


@dataclass(frozen=True)
class ValidationResult:
    """Structured validation output for edits or recommendations."""

    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolverRowView:
    """Structured row payload for programmatic resolution."""

    task_name: str
    item_id: str
    item_locator: str | None
    machine_status: str
    human_status: str
    rerun_eligible: bool
    proposal_payload: Any | None
    review_payload: Any | None
    apply_payload: Any | None
    human_decision_payload: Any | None
    extras: dict[str, Any]
    risk_flags: list[str]
    approved_output: Any | None = None
    rendered_summary: str = ""
    decision_source: str | None = None
    decision_actor: str | None = None
    decision_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.human_status == HumanDecision.APPROVED.value

    @property
    def key(self) -> tuple[str, str]:
        return (self.task_name, self.item_id)


@dataclass(frozen=True)
class ResolverRecommendation:
    """Task-agnostic representation of a suggested decision."""

    task_name: str
    item_id: str
    action: ResolverAction
    edits: Any | None = None
    note: str | None = None
    decision_source: str | None = None
    decision_actor: str | None = None
    decision_metadata: dict[str, Any] | None = None
    decision_payload: Any | None = None
    approved_output: Any | None = None
    decision_code_commit: str | None = None
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(valid=True))


@dataclass(frozen=True)
class ResolverResult:
    """Per-row resolver action result."""

    task_name: str
    item_id: str
    status: str
    decision: HumanDecision | None = None
    action: ResolverAction | None = None


@dataclass(frozen=True)
class ResolverApplyResult:
    """Result of applying a recommendation to the ledger."""

    task_name: str
    item_id: str
    status: str
    decision: HumanDecision | None = None
    action: ResolverAction | None = None
    row: ResolverRowView | None = None
    message: str | None = None


@dataclass(frozen=True)
class ResolverDecision:
    """Legacy decision object for compatibility with interactive flows."""

    decision: HumanDecision
    decision_payload: Any | None = None
    notes: str | None = None


def _normalize_validation(result: tuple[bool, str | None] | ValidationResult | None) -> ValidationResult:
    if isinstance(result, ValidationResult):
        return result
    if result is None:
        return ValidationResult(valid=True)
    if isinstance(result, tuple) and len(result) == 2:
        valid, raw_error = result
        return ValidationResult(valid=bool(valid), errors=[raw_error] if raw_error else [])
    return ValidationResult(valid=True)


class ResolverSession:
    """Session abstraction for programmatic adjudication workflows."""

    def __init__(
        self,
        *,
        store: LedgerStore,
        hooks: ResolverHooks | None = None,
        query: LedgerQuery | None = None,
        decision_source: str = "human",
        decision_actor: str = "cli-user",
    ) -> None:
        self.store = store
        self.hooks = hooks or DefaultResolverHooks()
        self.base_query = query or LedgerQuery(unresolved_only=True, include_approved=False)
        self.decision_source = decision_source
        self.decision_actor = decision_actor
        self._seen: set[tuple[str, str]] = set()

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

    @staticmethod
    def _row_match(query: LedgerQuery, row: LedgerRow) -> bool:
        return query.matches(row)

    @staticmethod
    def _row_to_view(row: LedgerRow, hooks: ResolverHooks) -> ResolverRowView:
        risk_flags = hooks.risk_flags(row)
        payload = row.to_dict()
        approved_output = None
        human_payload = payload.get("human_decision_payload")
        if isinstance(human_payload, dict):
            approved_output = human_payload.get("approved_output")

        return ResolverRowView(
            task_name=row.task_name,
            item_id=row.item_id,
            item_locator=row.item_locator,
            machine_status=row.machine_status.value,
            human_status=row.human_status.value,
            rerun_eligible=row.rerun_eligible,
            proposal_payload=row.proposal_payload,
            review_payload=row.review_payload,
            apply_payload=row.apply_payload,
            human_decision_payload=row.human_decision_payload,
            extras=row.extras,
            risk_flags=risk_flags,
            approved_output=approved_output,
            rendered_summary=hooks.render_summary(row),
            decision_source=row.human_decision_source,
            decision_actor=row.human_decision_actor,
            decision_metadata=row.human_decision_metadata,
        )

    def list_pending(self, query: LedgerQuery | None = None) -> list[ResolverRowView]:
        active = query or self.base_query
        rows = self.store.query_rows(active)
        return [self._row_to_view(row, self.hooks) for row in rows if self._row_match(active, row)]

    def get_row(self, task_name: str, item_id: str) -> ResolverRowView | None:
        row = self.store.get_row(task_name, item_id)
        if row is None:
            return None
        return self._row_to_view(row, self.hooks)

    def peek_next(self, query: LedgerQuery | None = None) -> ResolverRowView | None:
        active = query or self.base_query
        rows = self.store.query_rows(active)
        for row in rows:
            if (row.task_name, row.item_id) in self._seen:
                continue
            if self._row_match(active, row):
                return self._row_to_view(row, self.hooks)
        return None

    def get_next(self, query: LedgerQuery | None = None) -> ResolverRowView | None:
        row = self.peek_next(query=query)
        if row is None:
            return None
        # If status changed between listing and fetch, confirm pending and continue.
        current = self.store.get_row(row.task_name, row.item_id)
        active = query or self.base_query
        if current is None or not self._row_match(active, current):
            self._seen.add(row.key)
            return self.get_next(query=active)
        self._seen.add(row.key)
        return self._row_to_view(current, self.hooks)

    def preview_row(self, row: ResolverRowView | tuple[str, str]) -> Any:
        if isinstance(row, ResolverRowView):
            key = row.key
        else:
            key = row
        full_row = self.store.get_row(*key)
        if full_row is None:
            return None
        return self.hooks.preview_item(full_row)

    def validate_recommendation(self, row: ResolverRowView, edits: Any | None) -> ValidationResult:
        full_row = self.store.get_row(row.task_name, row.item_id)
        if full_row is None:
            return ValidationResult(valid=False, errors=["row_missing"])
        raw_result = self.hooks.validate_resolution(full_row, edits)
        return _normalize_validation(raw_result)

    def derive_approved_output(self, row: ResolverRowView, edits: Any | None) -> Any:
        full_row = self.store.get_row(row.task_name, row.item_id)
        if full_row is None:
            return None
        return self.hooks.derive_approved_output(full_row, edits)

    def build_recommendation(
        self,
        row: ResolverRowView | tuple[str, str],
        action: ResolverAction | str,
        *,
        edits: Any | None = None,
        note: str | None = None,
        decision_source: str | None = None,
        decision_actor: str | None = None,
        decision_metadata: dict[str, Any] | None = None,
        decision_code_commit: str | None = None,
    ) -> ResolverRecommendation:
        if isinstance(action, str):
            action = ResolverAction(action)

        if isinstance(row, ResolverRowView):
            key = row.key
            source_view = row
        else:
            source_view = self.get_row(*row)
            if source_view is None:
                raise KeyError(f"missing ledger row {row[0]}:{row[1]}")
            key = row

        validation = ValidationResult(valid=True)
        approved_output = None
        if action == ResolverAction.APPROVE_WITH_EDIT and edits is None:
            edits = {}
        if action in {ResolverAction.APPROVE, ResolverAction.APPROVE_WITH_EDIT}:
            validation = self.validate_recommendation(source_view, edits)
            approved_output = self.derive_approved_output(source_view, edits)
        elif action in {ResolverAction.REJECT, ResolverAction.NEEDS_REVIEW}:
            # Non-edit paths preserve existing validation behavior.
            validation = ValidationResult(valid=True)
            approved_output = self.derive_approved_output(source_view, edits)
        elif action == ResolverAction.SKIP:
            validation = ValidationResult(valid=True)

        decision_payload = None
        if action in {ResolverAction.APPROVE, ResolverAction.APPROVE_WITH_EDIT}:
            decision_payload = {"edits": edits or None, "approved_output": approved_output}
        elif action in {ResolverAction.REJECT, ResolverAction.NEEDS_REVIEW}:
            if note is not None:
                decision_payload = {"note": note}

        return ResolverRecommendation(
            task_name=key[0],
            item_id=key[1],
            action=action,
            edits=edits,
            note=note,
            decision_source=decision_source or self.decision_source,
            decision_actor=decision_actor or self.decision_actor,
            decision_metadata=decision_metadata or {},
            decision_payload=decision_payload,
            approved_output=approved_output,
            decision_code_commit=decision_code_commit,
            validation=validation,
        )

    def apply_recommendation(
        self,
        recommendation: ResolverRecommendation,
        *,
        force_apply: bool = False,
        apply: bool = True,
    ) -> ResolverApplyResult:
        if recommendation.action == ResolverAction.SKIP:
            return ResolverApplyResult(
                task_name=recommendation.task_name,
                item_id=recommendation.item_id,
                status="skipped",
                action=recommendation.action,
            )

        if not force_apply and not recommendation.validation.valid:
            return ResolverApplyResult(
                task_name=recommendation.task_name,
                item_id=recommendation.item_id,
                status="validation_failed",
                action=recommendation.action,
                message="; ".join(recommendation.validation.errors),
            )

        if not apply:
            return ResolverApplyResult(
                task_name=recommendation.task_name,
                item_id=recommendation.item_id,
                status="recommendation_only",
                action=recommendation.action,
            )

        mapped: HumanDecision | None
        if recommendation.action in {ResolverAction.APPROVE, ResolverAction.APPROVE_WITH_EDIT}:
            mapped = HumanDecision.APPROVED
        elif recommendation.action == ResolverAction.REJECT:
            mapped = HumanDecision.REJECTED
        elif recommendation.action == ResolverAction.NEEDS_REVIEW:
            mapped = HumanDecision.NEEDS_REVIEW
        else:
            mapped = None

        if mapped is None:
            return ResolverApplyResult(
                task_name=recommendation.task_name,
                item_id=recommendation.item_id,
                status="noop",
                action=recommendation.action,
            )

        updated = self.store.record_human_decision(
            task_name=recommendation.task_name,
            item_id=recommendation.item_id,
            decision=mapped,
            decision_payload=recommendation.decision_payload,
            decision_source=recommendation.decision_source,
            decision_actor=recommendation.decision_actor,
            decision_metadata=recommendation.decision_metadata,
            decision_code_commit=recommendation.decision_code_commit,
            notes=recommendation.note,
        )
        return ResolverApplyResult(
            task_name=updated.task_name,
            item_id=updated.item_id,
            status="applied",
            action=recommendation.action,
            decision=mapped,
            row=self._row_to_view(updated, self.hooks),
        )

    def resolve_interactive(
        self,
        *,
        query: LedgerQuery | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        dry_run: bool = False,
        decision_source: str = "human",
        decision_actor: str = "cli-user",
        capture_decision_code_commit: bool = False,
        decision_code_working_directory: Path | str | None = None,
    ) -> list[ResolverResult]:
        active = query or self.base_query
        results: list[ResolverResult] = []
        while True:
            view = self.get_next(query=active)
            if view is None:
                if not results:
                    output_fn("No rows match current query.")
                break

            output_fn("\n" + "-" * 72)
            output_fn(view.rendered_summary)
            if view.risk_flags:
                output_fn(f"  risk_flags: {', '.join(view.risk_flags)}")

            action = None
            while action is None:
                raw_action = input_fn(self._action_prompt()).strip().lower()
                if raw_action in {"q", "quit"}:
                    output_fn("Resolver stopped by user.")
                    return results
                if raw_action in {"p", "preview"}:
                    self.preview_row(view)
                    continue
                if raw_action in {"s", "skip"}:
                    action = ResolverAction.SKIP
                elif raw_action in {"a", "approve"}:
                    action = ResolverAction.APPROVE
                    note = None
                elif raw_action in {"e", "edit", "approve_edit"}:
                    action = ResolverAction.APPROVE_WITH_EDIT
                elif raw_action in {"r", "reject"}:
                    action = ResolverAction.REJECT
                elif raw_action in {"f", "followup", "needs"}:
                    action = ResolverAction.NEEDS_REVIEW
                else:
                    output_fn("  unknown action; use one of a/e/r/f/s/p/q")

            note: str | None = None
            edits: Any | None = None
            if action == ResolverAction.APPROVE:
                note = input_fn("  notes (optional): ").strip() or None
            elif action == ResolverAction.APPROVE_WITH_EDIT:
                full_row = self.store.get_row(view.task_name, view.item_id)
                if full_row is None:
                    output_fn("  row vanished; skipping")
                    continue
                try:
                    edits = self.hooks.prompt_edit(full_row, input_fn)
                except Exception as exc:  # noqa: BLE001
                    output_fn(f"  invalid edit input: {exc}")
                    continue
                note = input_fn("  notes (optional): ").strip() or None
            elif action == ResolverAction.REJECT:
                note = input_fn("  reason: ").strip() or None
            elif action == ResolverAction.NEEDS_REVIEW:
                note = input_fn("  follow-up notes (optional): ").strip() or None

            recommendation = self.build_recommendation(
                view,
                action=action,
                edits=edits,
                note=note,
                decision_source=decision_source,
                decision_actor=decision_actor,
            )
            # Capture code commitment for agent/CLI decisions at the time they are applied
            # when requested. This remains optional and only when explicitly enabled because
            # not all agents run from a git checkout.
            decision_code_commit = None
            if capture_decision_code_commit:
                commit = get_head_commit(Path(decision_code_working_directory or Path.cwd()))
                if commit:
                    decision_code_commit = commit

            if decision_code_commit:
                recommendation = ResolverRecommendation(
                    task_name=recommendation.task_name,
                    item_id=recommendation.item_id,
                    action=recommendation.action,
                    edits=recommendation.edits,
                    note=recommendation.note,
                    decision_source=recommendation.decision_source,
                    decision_actor=recommendation.decision_actor,
                    decision_metadata=recommendation.decision_metadata,
                    decision_payload=recommendation.decision_payload,
                    approved_output=recommendation.approved_output,
                    decision_code_commit=decision_code_commit,
                    validation=recommendation.validation,
                )

            if dry_run:
                output_fn(
                    f"  [dry-run] would record {recommendation.action.value} for {recommendation.item_id}"
                    f"{' with payload' if recommendation.decision_payload is not None else ''}"
                )
                results.append(
                    ResolverResult(
                        task_name=view.task_name,
                        item_id=view.item_id,
                        status="dry_run",
                        action=recommendation.action,
                    )
                )
                continue

            applied = self.apply_recommendation(recommendation, apply=True)
            if applied.status == "applied":
                results.append(
                    ResolverResult(
                        task_name=view.task_name,
                        item_id=view.item_id,
                        status="saved",
                        decision=applied.decision,
                        action=applied.action,
                    )
                )
                output_fn(f"  decision recorded: {recommendation.action.value}")
                continue
            if applied.status == "skipped":
                results.append(
                    ResolverResult(
                        task_name=view.task_name,
                        item_id=view.item_id,
                        status="skipped",
                    )
                )
            elif applied.status == "validation_failed":
                output_fn(f"  validation failed: {applied.message}")
                results.append(
                    ResolverResult(
                        task_name=view.task_name,
                        item_id=view.item_id,
                        status="validation_failed",
                    )
                )
                # Re-queue by removing from seen only after manual retry path.
                self._seen.discard(view.key)
            else:
                output_fn(f"  decision not applied: {applied.message}")
                results.append(
                    ResolverResult(
                        task_name=view.task_name,
                        item_id=view.item_id,
                        status=applied.status,
                    )
                )

        return results

class LedgerResolver:
    """Backwards-compatible interactive resolver wrapper."""

    def __init__(
        self,
        store: LedgerStore,
        hooks: ResolverHooks | None = None,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._session = ResolverSession(store=store, hooks=hooks)
        self._input_fn = input_fn
        self._output_fn = output_fn

    def resolve(self, *, query: LedgerQuery, dry_run: bool = False) -> list[ResolverResult]:
        return self._session.resolve_interactive(query=query, dry_run=dry_run, input_fn=self._input_fn, output_fn=self._output_fn)


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
