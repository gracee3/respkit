"""Generic ledger store and execution helpers."""

from __future__ import annotations

import copy
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from ..utils import make_run_id
from .git import LedgerGitError, get_head_commit, require_clean_working_tree
from .models import HumanDecision, LedgerRow, MachineStatus
from .query import LedgerQuery


ApplyCallback = Callable[[LedgerRow, bool], Any]


@dataclass(frozen=True)
class ApplyPolicy:
    """Options for applying approved items."""

    require_clean_working_tree: bool = False
    working_directory: Path = Path.cwd()
    capture_apply_code_commit: bool = True
    capture_applied_in_commit: bool = True


@dataclass(frozen=True)
class ApplyResult:
    """Outcome from an apply callback execution for one ledger row."""

    task_name: str
    item_id: str
    success: bool
    dry_run: bool
    apply_payload: Any | None = None
    apply_result: Any | None = None
    apply_run_id: str | None = None
    apply_code_commit: str | None = None
    applied_in_commit: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class _ApplyCallbackResult:
    """Internal normalized callback return format."""

    success: bool
    apply_payload: Any | None = None
    apply_result: Any | None = None
    message: str | None = None


class LedgerStore:
    """Persist and query generic adjudication rows.

    Storage is JSONL append-only, with the newest row for each item kept in-memory.
    """

    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.touch(exist_ok=True)
        self._lock = Lock()
        self._rows: dict[tuple[str, str], LedgerRow] = {}
        self._hydrate_from_disk()

    def _hydrate_from_disk(self) -> None:
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                row = LedgerRow.from_dict(payload)
                self._rows[(row.task_name, row.item_id)] = row
            except Exception:
                # Corrupt rows are intentionally skipped to keep recovery simple.
                continue

    def _write_row(self, row: LedgerRow) -> None:
        serialized = row.to_dict()
        with self.ledger_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(serialized, ensure_ascii=False) + "\n")

    @staticmethod
    def _clone_row(row: LedgerRow) -> LedgerRow:
        return copy.deepcopy(row)

    def _lookup(self, task_name: str, item_id: str) -> LedgerRow | None:
        return self._rows.get((task_name, item_id))

    def _store(self, row: LedgerRow) -> LedgerRow:
        self._rows[(row.task_name, row.item_id)] = self._clone_row(row)
        self._write_row(row)
        return self._clone_row(row)

    def get_row(self, task_name: str, item_id: str) -> LedgerRow | None:
        row = self._lookup(task_name, item_id)
        return self._clone_row(row) if row is not None else None

    def query_rows(self, query: LedgerQuery | None = None) -> list[LedgerRow]:
        active_query = query or LedgerQuery()
        rows = [self._clone_row(row) for row in self._rows.values() if active_query.matches(row)]
        rows.sort(key=lambda row: (row.task_name, row.item_id))
        return rows

    def export_csv(self, destination: Path, query: LedgerQuery | None = None) -> None:
        rows = self.query_rows(query)
        headers = [
            "task_name",
            "item_id",
            "item_locator",
            "input_fingerprint",
            "machine_status",
            "human_status",
            "rerun_eligible",
            "proposal_payload",
            "review_payload",
            "apply_payload",
            "proposal_result",
            "review_result",
            "apply_result",
            "proposal_run_id",
            "review_run_id",
            "human_decision_run_id",
            "apply_run_id",
            "proposal_code_commit",
            "review_code_commit",
            "human_decision_code_commit",
            "apply_code_commit",
            "applied_in_commit",
            "proposal_recorded_at",
            "review_recorded_at",
            "human_decision_recorded_at",
            "apply_recorded_at",
            "created_at",
            "updated_at",
            "machine_status_updated_at",
            "human_status_updated_at",
            "extras",
        ]
        with destination.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                data = row.to_dict()
                writer.writerow(
                    {
                        key: (
                            json.dumps(value, ensure_ascii=False)
                            if isinstance(value, (dict, list, tuple, set))
                            else value
                            if isinstance(value, (str, int, float))
                            else str(value)
                            if value is not None
                            else ""
                        )
                        for key, value in data.items()
                        if key in headers
                    }
                )

    def upsert(self, row: LedgerRow) -> LedgerRow:
        """Persist an arbitrary row as the latest state for its item."""
        with self._lock:
            return self._store(row)

    def create_or_update_row(
        self,
        *,
        task_name: str,
        item_id: str,
        item_locator: str | None = None,
        input_fingerprint: str | None = None,
        rerun_eligible: bool = False,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Create a blank row or return a cloned existing row."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                row = LedgerRow(task_name=task_name, item_id=item_id)
            else:
                row = self._clone_row(row)

            if item_locator is not None:
                row.item_locator = item_locator
            if input_fingerprint is not None:
                row.input_fingerprint = input_fingerprint
            row.rerun_eligible = rerun_eligible
            row.extras.update(extras or {})

            now = _utcnow()
            row.updated_at = now
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            return self._store(row)

    def record_proposal(
        self,
        *,
        task_name: str,
        item_id: str,
        proposal_payload: Any,
        proposal_result: Any | None = None,
        machine_status: MachineStatus = MachineStatus.PROPOSED,
        item_locator: str | None = None,
        input_fingerprint: str | None = None,
        proposal_run_id: str | None = None,
        proposal_code_commit: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Record proposal output for an item."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                row = LedgerRow(task_name=task_name, item_id=item_id)
            else:
                row = self._clone_row(row)

            now = _utcnow()
            if item_locator is not None:
                row.item_locator = item_locator
            if input_fingerprint is not None:
                row.input_fingerprint = input_fingerprint

            row.proposal_payload = proposal_payload
            row.proposal_result = proposal_result
            row.machine_status = machine_status
            row.human_status = HumanDecision.NEEDS_REVIEW
            row.proposal_run_id = proposal_run_id or make_run_id(item_id, item_locator, {"stage": "proposal"})
            row.proposal_code_commit = proposal_code_commit
            row.proposal_recorded_at = now
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            row.rerun_eligible = machine_status in {
                MachineStatus.PROPOSED,
                MachineStatus.PROVIDER_ERROR,
            }
            row.updated_at = now
            row.extras.update(extras or {})

            return self._store(row)

    def record_review(
        self,
        *,
        task_name: str,
        item_id: str,
        review_payload: Any,
        review_result: Any | None = None,
        review_run_id: str | None = None,
        review_code_commit: str | None = None,
        machine_status: MachineStatus = MachineStatus.REVIEWED,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Record review output for an item."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                row = LedgerRow(task_name=task_name, item_id=item_id)
            else:
                row = self._clone_row(row)

            now = _utcnow()
            row.review_payload = review_payload
            row.review_result = review_result
            row.review_run_id = review_run_id or make_run_id(item_id, row.item_locator, {"stage": "review"})
            row.review_code_commit = review_code_commit
            row.review_recorded_at = now
            row.machine_status = machine_status
            row.machine_status_updated_at = now
            row.updated_at = now
            row.rerun_eligible = machine_status != MachineStatus.SUPERSEDED
            row.extras.update(extras or {})

            return self._store(row)

    def record_human_decision(
        self,
        *,
        task_name: str,
        item_id: str,
        decision: HumanDecision,
        decision_payload: Any | None = None,
        decision_run_id: str | None = None,
        decision_code_commit: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Record human decision and update machine/human status transitions."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                row = LedgerRow(task_name=task_name, item_id=item_id)
            else:
                row = self._clone_row(row)

            now = _utcnow()
            row.human_decision_run_id = decision_run_id or make_run_id(item_id, row.item_locator, {"stage": "human"})
            row.human_decision_code_commit = decision_code_commit
            row.human_status = decision
            row.human_status_updated_at = now
            row.human_decision_recorded_at = now

            if decision == HumanDecision.APPROVED:
                row.machine_status = MachineStatus.APPLY_READY
            elif decision == HumanDecision.REJECTED:
                row.machine_status = MachineStatus.REVIEWED
            else:
                row.machine_status = MachineStatus.PROPOSED

            if decision_payload is not None:
                row.review_payload = decision_payload

            row.rerun_eligible = decision != HumanDecision.APPROVED
            row.machine_status_updated_at = now
            row.updated_at = now
            row.extras.update(extras or {})

            return self._store(row)

    def record_apply(
        self,
        *,
        task_name: str,
        item_id: str,
        apply_payload: Any | None = None,
        apply_result: Any | None = None,
        apply_run_id: str | None = None,
        apply_code_commit: str | None = None,
        applied_in_commit: str | None = None,
        success: bool = True,
        applied: bool = True,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Record a completed apply attempt and machine status transition."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                row = LedgerRow(task_name=task_name, item_id=item_id)
            else:
                row = self._clone_row(row)

            now = _utcnow()
            row.apply_payload = apply_payload
            row.apply_result = apply_result
            row.apply_run_id = apply_run_id or make_run_id(item_id, row.item_locator, {"stage": "apply"})
            row.apply_code_commit = apply_code_commit
            row.applied_in_commit = applied_in_commit
            row.apply_recorded_at = now
            row.machine_status_updated_at = now
            row.updated_at = now

            if success:
                row.machine_status = MachineStatus.APPLIED if applied else MachineStatus.APPLY_READY
                row.human_status = HumanDecision.APPROVED
                row.human_status_updated_at = now
                row.rerun_eligible = not applied
            else:
                row.machine_status = MachineStatus.PROVIDER_ERROR
                row.rerun_eligible = True

            row.extras.update(extras or {})
            return self._store(row)

    def mark_superseded(self, *, task_name: str, item_id: str) -> LedgerRow:
        """Mark an item as superseded for historical bookkeeping."""

        with self._lock:
            row = self._lookup(task_name, item_id)
            if row is None:
                raise KeyError(f"missing ledger row {task_name}:{item_id}")

            row = self._clone_row(row)
            now = _utcnow()
            row.machine_status = MachineStatus.SUPERSEDED
            row.machine_status_updated_at = now
            row.updated_at = now
            row.rerun_eligible = False
            return self._store(row)

    def run_apply(
        self,
        *,
        query: LedgerQuery,
        callback: ApplyCallback,
        dry_run: bool,
        policy: ApplyPolicy = ApplyPolicy(),
    ) -> list[ApplyResult]:
        """Execute an optional apply callback over rows matching query."""

        selected_rows = self.query_rows(query)
        results: list[ApplyResult] = []

        for row in selected_rows:
            if row.machine_status != MachineStatus.APPLY_READY and row.human_status != HumanDecision.APPROVED:
                results.append(
                    ApplyResult(
                        task_name=row.task_name,
                        item_id=row.item_id,
                        success=False,
                        dry_run=dry_run,
                        message="row is not ready for apply",
                    )
                )
                continue

            apply_run_id = make_run_id(row.item_id, row.item_locator, {"stage": "apply", "dry_run": str(dry_run)})
            apply_code_commit: str | None = None
            applied_in_commit: str | None = None

            try:
                if not dry_run and policy.require_clean_working_tree:
                    require_clean_working_tree(policy.working_directory)
                if not dry_run and policy.capture_apply_code_commit:
                    apply_code_commit = get_head_commit(policy.working_directory)

                callback_result = callback(row, dry_run)
                outcome = _normalize_apply_callback_result(callback_result)

                if not dry_run and policy.capture_applied_in_commit and outcome.success:
                    applied_in_commit = get_head_commit(policy.working_directory)

                self.record_apply(
                    task_name=row.task_name,
                    item_id=row.item_id,
                    apply_payload=outcome.apply_payload,
                    apply_result=outcome.apply_result,
                    apply_run_id=apply_run_id,
                    apply_code_commit=apply_code_commit,
                    applied_in_commit=applied_in_commit,
                    success=outcome.success,
                    applied=not dry_run,
                )

                results.append(
                    ApplyResult(
                        task_name=row.task_name,
                        item_id=row.item_id,
                        success=outcome.success,
                        dry_run=dry_run,
                        apply_payload=outcome.apply_payload,
                        apply_result=outcome.apply_result,
                        apply_run_id=apply_run_id,
                        apply_code_commit=apply_code_commit,
                        applied_in_commit=applied_in_commit,
                        message=outcome.message,
                    )
                )
            except LedgerGitError as exc:
                self.record_apply(
                    task_name=row.task_name,
                    item_id=row.item_id,
                    apply_payload={"error": str(exc)},
                    apply_result=None,
                    apply_run_id=apply_run_id,
                    apply_code_commit=apply_code_commit,
                    success=False,
                )
                results.append(
                    ApplyResult(
                        task_name=row.task_name,
                        item_id=row.item_id,
                        success=False,
                        dry_run=dry_run,
                        apply_run_id=apply_run_id,
                        apply_code_commit=apply_code_commit,
                        apply_payload={"error": str(exc)},
                        message=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.record_apply(
                    task_name=row.task_name,
                    item_id=row.item_id,
                    apply_payload={"error": str(exc)},
                    apply_result=None,
                    apply_run_id=apply_run_id,
                    apply_code_commit=apply_code_commit,
                    success=False,
                )
                results.append(
                    ApplyResult(
                        task_name=row.task_name,
                        item_id=row.item_id,
                        success=False,
                        dry_run=dry_run,
                        apply_run_id=apply_run_id,
                        apply_code_commit=apply_code_commit,
                        apply_payload={"error": str(exc)},
                        message=str(exc),
                    )
                )

        return results


def _normalize_apply_callback_result(value: Any) -> _ApplyCallbackResult:
    """Normalize callback return value into an internal structure."""

    if isinstance(value, ApplyResult):
        return _ApplyCallbackResult(
            success=value.success,
            apply_payload=value.apply_payload,
            apply_result=value.apply_result,
            message=value.message,
        )

    if isinstance(value, tuple) and len(value) == 2:
        return _ApplyCallbackResult(success=True, apply_payload=value[0], apply_result=value[1])

    if isinstance(value, dict):
        return _ApplyCallbackResult(
            success=bool(value.get("success", True)),
            apply_payload=value.get("apply_payload"),
            apply_result=value.get("apply_result", value),
            message=value.get("message"),
        )

    if isinstance(value, bool):
        return _ApplyCallbackResult(success=value)

    return _ApplyCallbackResult(success=True, apply_payload=None, apply_result=value)


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
