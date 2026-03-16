"""Task adapter abstractions for the service layer."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..ledger.models import LedgerRow
from ..ledger.resolver import ResolverHooks, DefaultResolverHooks, ValidationResult
from ..ledger.store import LedgerStore


@dataclass(frozen=True)
class ActionDescriptor:
    """Metadata for a task action exposed through the service."""

    name: str
    description: str
    requires_edits: bool = False
    builtin: bool = False


@dataclass(frozen=True)
class ActionResult:
    """Result of invoking a task-specific action."""

    success: bool
    message: str | None = None
    payload: dict[str, Any] | None = None


@runtime_checkable
class TaskServiceAdapter(Protocol):
    """Service-side hook surface for task-specific behavior."""

    task_name: str | None

    def supports_task(self, task_name: str) -> bool:
        ...

    def render_summary(self, row: LedgerRow) -> str:
        ...

    def preview_item(self, row: LedgerRow) -> Any:
        ...

    def validate_resolution(self, row: LedgerRow, edits: Any | None) -> ValidationResult | tuple[bool, str | None]:
        ...

    def derive_approved_output(self, row: LedgerRow, edits: Any | None) -> Any:
        ...

    def risk_flags(self, row: LedgerRow) -> list[str]:
        ...

    def row_categories(self, row: LedgerRow) -> list[str]:
        """Return optional task-local categories for rows."""

    def available_actions(self, row: LedgerRow) -> list[ActionDescriptor]:
        """Return actions exposed to clients for this row."""

    def execute_action(
        self,
        *,
        row: LedgerRow,
        action: str,
        params: dict[str, Any] | None,
        store: LedgerStore,
    ) -> ActionResult:
        """Execute a task-local action."""


class _ResolverHooksAdapter:
    """Bridge generic resolver hooks into the task service adapter protocol."""

    task_name: str | None = None

    def __init__(self, hooks: ResolverHooks) -> None:
        self.hooks = hooks

    def supports_task(self, task_name: str) -> bool:
        return True if self.task_name is None else self.task_name == task_name

    def render_summary(self, row: LedgerRow) -> str:
        return self.hooks.render_summary(row)

    def preview_item(self, row: LedgerRow) -> Any:
        return self.hooks.preview_item(row)

    def validate_resolution(self, row: LedgerRow, edits: Any | None) -> ValidationResult | tuple[bool, str | None]:
        return self.hooks.validate_resolution(row, edits)

    def derive_approved_output(self, row: LedgerRow, edits: Any | None) -> Any:
        return self.hooks.derive_approved_output(row, edits)

    def risk_flags(self, row: LedgerRow) -> list[str]:
        return self.hooks.risk_flags(row)

    def row_categories(self, row: LedgerRow) -> list[str]:
        del row
        return []

    def available_actions(self, row: LedgerRow) -> list[ActionDescriptor]:
        del row
        return [
            ActionDescriptor(name="approve", description="Approve the row", builtin=True),
            ActionDescriptor(name="approve_with_edit", description="Approve with task-specific edits", requires_edits=True, builtin=True),
            ActionDescriptor(name="reject", description="Reject this row", builtin=True),
            ActionDescriptor(name="needs_review", description="Mark as needs review/follow-up", builtin=True),
            ActionDescriptor(name="skip", description="Skip row without persisting", builtin=True),
        ]

    def execute_action(
        self,
        *,
        row: LedgerRow,
        action: str,
        params: dict[str, Any] | None,
        store: LedgerStore,
    ) -> ActionResult:
        del row, params, store
        return ActionResult(success=False, message=f"unsupported custom action: {action}")


class DefaultTaskServiceAdapter(_ResolverHooksAdapter):
    """Default adapter using generic SDK hooks."""

    def __init__(self, hooks: ResolverHooks | None = None, task_name: str | None = None) -> None:
        super().__init__(hooks or DefaultResolverHooks())
        self.task_name = task_name


def load_task_adapter(target: str) -> type[TaskServiceAdapter]:
    """Load a task adapter by ``module:qualname``."""

    module_name, _, attr_name = target.partition(":")
    if not module_name or not attr_name:
        raise ValueError(f"invalid task adapter target '{target}', expected module:Class")
    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, attr_name, None)
    if adapter_cls is None:
        raise AttributeError(f"missing adapter '{attr_name}' in {module_name}")
    if not isinstance(adapter_cls, type):
        raise TypeError(f"adapter target '{target}' is not a class")
    return adapter_cls
