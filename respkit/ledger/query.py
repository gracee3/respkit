"""Selection helpers for rerun and apply filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import HumanDecision, LedgerRow, MachineStatus


@dataclass
class LedgerQuery:
    """Composable ledger selection filters."""

    task_name: str | None = None
    item_ids: Sequence[str] | None = None
    item_id_prefix: str | None = None
    item_locator: str | None = None
    item_locator_prefix: str | None = None
    unresolved_only: bool = False
    provider_error_only: bool = False
    rejected_only: bool = False
    not_approved_only: bool = False
    include_approved: bool = True
    include_superseded: bool = False
    rerun_eligible_only: bool = False
    machine_statuses: set[MachineStatus] | None = None
    human_statuses: set[HumanDecision] | None = None
    limit: int | None = None

    def matches(self, row: LedgerRow) -> bool:
        """Return True when a row matches this query."""

        if self.task_name is not None and row.task_name != self.task_name:
            return False
        if self.item_ids is not None and row.item_id not in set(self.item_ids):
            return False
        if self.item_id_prefix is not None and not row.item_id.startswith(self.item_id_prefix):
            return False
        if self.item_locator is not None and row.item_locator != self.item_locator:
            return False
        if self.item_locator_prefix is not None:
            if row.item_locator is None or not row.item_locator.startswith(self.item_locator_prefix):
                return False
        if self.machine_statuses is not None and row.machine_status not in self.machine_statuses:
            return False
        if self.human_statuses is not None and row.human_status not in self.human_statuses:
            return False
        if self.provider_error_only:
            if row.machine_status != MachineStatus.PROVIDER_ERROR:
                return False
        elif self.unresolved_only and not row.is_unresolved():
            return False
        if self.rejected_only and row.human_status != HumanDecision.REJECTED:
            return False
        if self.not_approved_only and row.human_status == HumanDecision.APPROVED:
            return False
        if not self.include_approved and row.human_status == HumanDecision.APPROVED:
            return False
        if not self.include_superseded and row.machine_status == MachineStatus.SUPERSEDED:
            return False
        if self.rerun_eligible_only and not row.rerun_eligible:
            return False
        return True

    def to_sql_where(self, prefix: str = "lr") -> tuple[str, list[str | None], str]:
        """Build a SQL WHERE clause and bindings for the current query."""

        clauses: list[str] = []
        params: list[str | None] = []

        if self.task_name is not None:
            clauses.append(f"{prefix}.task_name = ?")
            params.append(self.task_name)

        if self.item_ids is not None:
            placeholders = ", ".join("?" for _ in set(self.item_ids))
            clauses.append(f"{prefix}.item_id IN ({placeholders})")
            params.extend(list(dict.fromkeys(self.item_ids)))

        if self.item_id_prefix is not None:
            clauses.append(f"{prefix}.item_id LIKE ?")
            params.append(f"{self.item_id_prefix}%")

        if self.item_locator is not None:
            clauses.append(f"{prefix}.item_locator = ?")
            params.append(self.item_locator)

        if self.item_locator_prefix is not None:
            clauses.append(f"{prefix}.item_locator LIKE ?")
            params.append(f"{self.item_locator_prefix}%")

        if self.machine_statuses is not None:
            if self.machine_statuses:
                placeholders = ", ".join("?" for _ in self.machine_statuses)
                clauses.append(f"{prefix}.machine_status IN ({placeholders})")
                params.extend([status.value for status in self.machine_statuses])
            else:
                clauses.append("1 = 0")

        if self.human_statuses is not None:
            if self.human_statuses:
                placeholders = ", ".join("?" for _ in self.human_statuses)
                clauses.append(f"{prefix}.human_status IN ({placeholders})")
                params.extend([status.value for status in self.human_statuses])
            else:
                clauses.append("1 = 0")

        if self.provider_error_only:
            clauses.append(f"{prefix}.machine_status = '{MachineStatus.PROVIDER_ERROR.value}'")
        elif self.unresolved_only:
            clauses.append(f"{prefix}.machine_status NOT IN ('{MachineStatus.APPLIED.value}', '{MachineStatus.SUPERSEDED.value}')")

        if self.rejected_only:
            clauses.append(f"{prefix}.human_status = '{HumanDecision.REJECTED.value}'")

        if self.not_approved_only:
            clauses.append(f"{prefix}.human_status != '{HumanDecision.APPROVED.value}'")

        if not self.include_approved:
            clauses.append(f"{prefix}.human_status != '{HumanDecision.APPROVED.value}'")

        if not self.include_superseded:
            clauses.append(f"{prefix}.machine_status != '{MachineStatus.SUPERSEDED.value}'")

        if self.rerun_eligible_only:
            clauses.append(f"{prefix}.rerun_eligible = 1")

        where_clause = " AND ".join(clauses) if clauses else "1 = 1"
        order_by = f"ORDER BY {prefix}.task_name ASC, {prefix}.item_id ASC"
        if self.limit is not None:
            order_by += f" LIMIT {int(self.limit)}"

        return where_clause, params, order_by
