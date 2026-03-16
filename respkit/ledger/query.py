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
    unresolved_only: bool = False
    provider_error_only: bool = False
    rejected_only: bool = False
    not_approved_only: bool = False
    include_approved: bool = True
    include_superseded: bool = False
    rerun_eligible_only: bool = False
    machine_statuses: set[MachineStatus] | None = None
    human_statuses: set[HumanDecision] | None = None

    def matches(self, row: LedgerRow) -> bool:
        """Return True when a row matches this query."""

        if self.task_name is not None and row.task_name != self.task_name:
            return False
        if self.item_ids is not None and row.item_id not in set(self.item_ids):
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
