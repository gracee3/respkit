"""SQLite-backed ledger store for generic proposal/review/adjudication flows."""

from __future__ import annotations

import csv
import json
import sqlite3
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
    """Options for applying approved rows."""

    require_clean_working_tree: bool = False
    working_directory: Path = Path.cwd()
    capture_apply_code_commit: bool = True
    capture_applied_in_commit: bool = True


@dataclass(frozen=True)
class ApplyResult:
    """Outcome from a single apply attempt."""

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
    """Normalized callback output."""

    success: bool
    apply_payload: Any | None = None
    apply_result: Any | None = None
    message: str | None = None


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _serialize_datetime(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_datetime(value: str | None) -> Any:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _serialize_payload(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_payload(raw: str | None) -> Any:
    if raw is None:
        return None
    if raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _event_type_for_method(method: str) -> str:
    return method


def _row_to_payload(row: LedgerRow) -> dict[str, Any]:
    data = row.to_dict()
    return data


def _payload_to_row(data: dict[str, Any]) -> LedgerRow:
    return LedgerRow.from_dict(data)


def _normalize_machine_status(raw: str | MachineStatus) -> MachineStatus:
    if isinstance(raw, MachineStatus):
        return raw
    return MachineStatus(raw)


def _normalize_human_status(raw: str | HumanDecision) -> HumanDecision:
    if isinstance(raw, HumanDecision):
        return raw
    return HumanDecision(raw)


class LedgerStore:
    """Persist ledger rows in SQLite with a current-state table and history table."""

    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.ledger_path),
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._lock = Lock()
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LedgerStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_rows (
                    task_name TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    machine_status TEXT NOT NULL,
                    human_status TEXT NOT NULL,
                    rerun_eligible INTEGER NOT NULL,
                    proposal_payload TEXT,
                    review_payload TEXT,
                    human_decision_payload TEXT,
                    apply_payload TEXT,
                    proposal_result TEXT,
                    review_result TEXT,
                    apply_result TEXT,
                    item_locator TEXT,
                    input_fingerprint TEXT,
                    proposal_run_id TEXT,
                    review_run_id TEXT,
                    human_decision_run_id TEXT,
                    apply_run_id TEXT,
                    proposal_code_commit TEXT,
                    review_code_commit TEXT,
                    human_decision_code_commit TEXT,
                    apply_code_commit TEXT,
                    applied_in_commit TEXT,
                    human_notes TEXT,
                    proposal_recorded_at TEXT,
                    review_recorded_at TEXT,
                    human_decision_recorded_at TEXT,
                    apply_recorded_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    machine_status_updated_at TEXT NOT NULL,
                    human_status_updated_at TEXT NOT NULL,
                    extras TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(task_name, item_id)
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    machine_status TEXT NOT NULL,
                    human_status TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    event_payload TEXT NOT NULL,
                    FOREIGN KEY(task_name, item_id) REFERENCES ledger_rows(task_name, item_id)
                        ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_rows_task_item
                ON ledger_rows(task_name, item_id);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_rows_status
                ON ledger_rows(task_name, machine_status, human_status);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_events_task_item_version
                ON ledger_events(task_name, item_id, version);
                """
            )
            self._conn.execute("COMMIT;")

    def _clone_row(self, row: LedgerRow) -> LedgerRow:
        return LedgerRow.from_dict(row.to_dict())

    def _load_state(self, task_name: str, item_id: str) -> LedgerRow | None:
        cursor = self._conn.execute(
            """
            SELECT * FROM ledger_rows
            WHERE task_name = ? AND item_id = ?;
            """,
            (task_name, item_id),
        )
        payload = cursor.fetchone()
        if payload is None:
            return None
        return self._deserialize_state_row(payload)

    def _deserialize_state_row(self, row: sqlite3.Row) -> LedgerRow:
        return LedgerRow(
            task_name=row["task_name"],
            item_id=row["item_id"],
            version=int(row["version"]),
            machine_status=_normalize_machine_status(row["machine_status"]),
            human_status=_normalize_human_status(row["human_status"]),
            rerun_eligible=bool(row["rerun_eligible"]),
            proposal_payload=_parse_payload(row["proposal_payload"]),
            review_payload=_parse_payload(row["review_payload"]),
            apply_payload=_parse_payload(row["apply_payload"]),
            human_decision_payload=_parse_payload(row["human_decision_payload"]),
            proposal_result=_parse_payload(row["proposal_result"]),
            review_result=_parse_payload(row["review_result"]),
            apply_result=_parse_payload(row["apply_result"]),
            item_locator=row["item_locator"],
            input_fingerprint=row["input_fingerprint"],
            proposal_run_id=row["proposal_run_id"],
            review_run_id=row["review_run_id"],
            human_decision_run_id=row["human_decision_run_id"],
            apply_run_id=row["apply_run_id"],
            proposal_code_commit=row["proposal_code_commit"],
            review_code_commit=row["review_code_commit"],
            human_decision_code_commit=row["human_decision_code_commit"],
            apply_code_commit=row["apply_code_commit"],
            applied_in_commit=row["applied_in_commit"],
            human_notes=row["human_notes"],
            proposal_recorded_at=_parse_datetime(row["proposal_recorded_at"]),
            review_recorded_at=_parse_datetime(row["review_recorded_at"]),
            human_decision_recorded_at=_parse_datetime(row["human_decision_recorded_at"]),
            apply_recorded_at=_parse_datetime(row["apply_recorded_at"]),
            created_at=_parse_datetime(row["created_at"]) or _utcnow(),
            updated_at=_parse_datetime(row["updated_at"]) or _utcnow(),
            machine_status_updated_at=_parse_datetime(row["machine_status_updated_at"]) or _utcnow(),
            human_status_updated_at=_parse_datetime(row["human_status_updated_at"]) or _utcnow(),
            extras=_parse_payload(row["extras"]) or {},
        )

    def _serialize_state_values(self, row: LedgerRow) -> tuple[Any, ...]:
        return (
            row.task_name,
            row.item_id,
            row.version,
            row.machine_status.value,
            row.human_status.value,
            int(row.rerun_eligible),
            _serialize_payload(row.proposal_payload),
            _serialize_payload(row.review_payload),
            _serialize_payload(row.human_decision_payload),
            _serialize_payload(row.apply_payload),
            _serialize_payload(row.proposal_result),
            _serialize_payload(row.review_result),
            _serialize_payload(row.apply_result),
            row.item_locator,
            row.input_fingerprint,
            row.proposal_run_id,
            row.review_run_id,
            row.human_decision_run_id,
            row.apply_run_id,
            row.proposal_code_commit,
            row.review_code_commit,
            row.human_decision_code_commit,
            row.apply_code_commit,
            row.applied_in_commit,
            row.human_notes,
            _serialize_datetime(row.proposal_recorded_at),
            _serialize_datetime(row.review_recorded_at),
            _serialize_datetime(row.human_decision_recorded_at),
            _serialize_datetime(row.apply_recorded_at),
            _serialize_datetime(row.created_at),
            _serialize_datetime(row.updated_at),
            _serialize_datetime(row.machine_status_updated_at),
            _serialize_datetime(row.human_status_updated_at),
            _serialize_payload(row.extras),
        )

    def _insert_state(self, row: LedgerRow) -> None:
        self._conn.execute(
            """
            INSERT INTO ledger_rows (
                task_name, item_id, version, machine_status, human_status,
                rerun_eligible, proposal_payload, review_payload, human_decision_payload,
                apply_payload, proposal_result, review_result, apply_result, item_locator,
                input_fingerprint, proposal_run_id, review_run_id, human_decision_run_id,
                apply_run_id, proposal_code_commit, review_code_commit, human_decision_code_commit,
                apply_code_commit, applied_in_commit, human_notes, proposal_recorded_at,
                review_recorded_at, human_decision_recorded_at, apply_recorded_at, created_at,
                updated_at, machine_status_updated_at, human_status_updated_at, extras
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._serialize_state_values(row),
        )

    def _update_state(self, row: LedgerRow) -> None:
        self._conn.execute(
            """
            UPDATE ledger_rows SET
                version = ?,
                machine_status = ?,
                human_status = ?,
                rerun_eligible = ?,
                proposal_payload = ?,
                review_payload = ?,
                human_decision_payload = ?,
                apply_payload = ?,
                proposal_result = ?,
                review_result = ?,
                apply_result = ?,
                item_locator = ?,
                input_fingerprint = ?,
                proposal_run_id = ?,
                review_run_id = ?,
                human_decision_run_id = ?,
                apply_run_id = ?,
                proposal_code_commit = ?,
                review_code_commit = ?,
                human_decision_code_commit = ?,
                apply_code_commit = ?,
                applied_in_commit = ?,
                human_notes = ?,
                proposal_recorded_at = ?,
                review_recorded_at = ?,
                human_decision_recorded_at = ?,
                apply_recorded_at = ?,
                created_at = ?,
                updated_at = ?,
                machine_status_updated_at = ?,
                human_status_updated_at = ?,
                extras = ?
            WHERE task_name = ? AND item_id = ?;
            """,
            (
                row.version,
                row.machine_status.value,
                row.human_status.value,
                int(row.rerun_eligible),
                _serialize_payload(row.proposal_payload),
                _serialize_payload(row.review_payload),
                _serialize_payload(row.human_decision_payload),
                _serialize_payload(row.apply_payload),
                _serialize_payload(row.proposal_result),
                _serialize_payload(row.review_result),
                _serialize_payload(row.apply_result),
                row.item_locator,
                row.input_fingerprint,
                row.proposal_run_id,
                row.review_run_id,
                row.human_decision_run_id,
                row.apply_run_id,
                row.proposal_code_commit,
                row.review_code_commit,
                row.human_decision_code_commit,
                row.apply_code_commit,
                row.applied_in_commit,
                row.human_notes,
                _serialize_datetime(row.proposal_recorded_at),
                _serialize_datetime(row.review_recorded_at),
                _serialize_datetime(row.human_decision_recorded_at),
                _serialize_datetime(row.apply_recorded_at),
                _serialize_datetime(row.created_at),
                _serialize_datetime(row.updated_at),
                _serialize_datetime(row.machine_status_updated_at),
                _serialize_datetime(row.human_status_updated_at),
                _serialize_payload(row.extras),
                row.task_name,
                row.item_id,
            ),
        )

    def _append_history(self, row: LedgerRow, event_type: str) -> None:
        event_payload = json.dumps(_row_to_payload(row), ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            """
            INSERT INTO ledger_events (
                task_name,
                item_id,
                version,
                event_type,
                machine_status,
                human_status,
                event_at,
                event_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.task_name,
                row.item_id,
                row.version,
                event_type,
                row.machine_status.value,
                row.human_status.value,
                _serialize_datetime(row.updated_at),
                event_payload,
            ),
        )

    def _persist(self, row: LedgerRow, *, event_type: str) -> LedgerRow:
        """Persist a row as the current state and append a history event."""

        existing = self._load_state(row.task_name, row.item_id)
        now = _utcnow()
        if existing is None:
            next_version = max(1, row.version)
            row.version = next_version
            row.created_at = row.created_at if row.created_at else now
            row.updated_at = now
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            self._insert_state(row)
        else:
            next_version = max(existing.version + 1, row.version)
            row.version = next_version
            row.created_at = existing.created_at
            row.updated_at = now
            row.machine_status_updated_at = row.machine_status_updated_at or now
            row.human_status_updated_at = row.human_status_updated_at or now
            self._update_state(row)

        self._append_history(row, event_type)
        return self._clone_row(row)

    def _mutate_row(self, task_name: str, item_id: str) -> LedgerRow:
        row = self._load_state(task_name, item_id)
        if row is None:
            row = LedgerRow(task_name=task_name, item_id=item_id)
        return self._clone_row(row)

    def get_row(self, task_name: str, item_id: str) -> LedgerRow | None:
        row = self._load_state(task_name, item_id)
        return self._clone_row(row) if row is not None else None

    def query_rows(self, query: LedgerQuery | None = None) -> list[LedgerRow]:
        active_query = query or LedgerQuery()
        where_clause, params, order_clause = active_query.to_sql_where(prefix="lr")
        cursor = self._conn.execute(
            f"""
            SELECT * FROM ledger_rows AS lr
            WHERE {where_clause}
            {order_clause};
            """,
            params,
        )
        return [self._deserialize_state_row(raw) for raw in cursor.fetchall()]

    def get_row_history(self, task_name: str, item_id: str) -> list[LedgerRow]:
        """Return row history from oldest to newest for a single item."""

        cursor = self._conn.execute(
            """
            SELECT event_payload FROM ledger_events
            WHERE task_name = ? AND item_id = ?
            ORDER BY version ASC, id ASC;
            """,
            (task_name, item_id),
        )
        rows: list[LedgerRow] = []
        for raw in cursor.fetchall():
            payload = _parse_payload(raw["event_payload"])
            if isinstance(payload, dict):
                rows.append(_payload_to_row(payload))
        return rows

    def get_task_history(self, task_name: str) -> list[dict[str, Any]]:
        cursor = self._conn.execute(
            """
            SELECT task_name, item_id, version, event_type, machine_status, human_status, event_at, event_payload
            FROM ledger_events
            WHERE task_name = ?
            ORDER BY event_at DESC, id DESC;
            """,
            (task_name,),
        )
        return [dict(record) for record in cursor.fetchall()]

    def export_csv(self, destination: Path, query: LedgerQuery | None = None) -> None:
        rows = self.query_rows(query)
        field_names = [
            "task_name",
            "item_id",
            "version",
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
            "human_decision_payload",
            "proposal_run_id",
            "review_run_id",
            "human_decision_run_id",
            "apply_run_id",
            "proposal_code_commit",
            "review_code_commit",
            "human_decision_code_commit",
            "apply_code_commit",
            "applied_in_commit",
            "human_notes",
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
            writer = csv.DictWriter(fp, fieldnames=field_names)
            writer.writeheader()
            for row in rows:
                payload = row.to_dict()
                rendered: dict[str, Any] = {}
                for key in field_names:
                    value = payload.get(key)
                    if isinstance(value, (dict, list, tuple, set)):
                        rendered[key] = json.dumps(value, ensure_ascii=False)
                    elif isinstance(value, (int, float)):
                        rendered[key] = value
                    elif value is None:
                        rendered[key] = ""
                    else:
                        rendered[key] = str(value)
                writer.writerow(rendered)

    def export_jsonl(self, destination: Path, query: LedgerQuery | None = None) -> None:
        rows = self.query_rows(query)
        with destination.open("w", encoding="utf-8") as fp:
            for row in rows:
                fp.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")

    def export_markdown(self, destination: Path, query: LedgerQuery | None = None) -> None:
        rows = self.query_rows(query)
        field_names = ["task_name", "item_id", "item_locator", "machine_status", "human_status", "human_notes"]

        with destination.open("w", encoding="utf-8") as fp:
            fp.write("| " + " | ".join(field_names) + " |\n")
            fp.write("|" + "|".join([" --- " for _ in field_names]) + "|\n")
            for row in rows:
                payload = row.to_dict()
                values = [str(payload.get(key, "")) for key in field_names]
                escaped = [value.replace("|", "\\|") for value in values]
                fp.write("| " + " | ".join(escaped) + " |\n")

    def upsert(self, row: LedgerRow) -> LedgerRow:
        with self._lock:
            existing = self._load_state(row.task_name, row.item_id)
            clone = self._clone_row(row)
            clone.updated_at = _utcnow()
            if existing is not None:
                existing_version = existing.version
            else:
                existing_version = 0
            clone.version = max(existing_version + 1, row.version)
            clone.machine_status_updated_at = clone.machine_status_updated_at or _utcnow()
            clone.human_status_updated_at = clone.human_status_updated_at or _utcnow()
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(clone, event_type=_event_type_for_method("upsert"))
            self._conn.execute("COMMIT;")
            return persisted

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
        """Create or refresh generic metadata for an item."""

        with self._lock:
            row = self._mutate_row(task_name, item_id)
            now = _utcnow()
            if item_locator is not None:
                row.item_locator = item_locator
            if input_fingerprint is not None:
                row.input_fingerprint = input_fingerprint
            row.rerun_eligible = rerun_eligible
            row.updated_at = now
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            row.extras.update(extras or {})
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("create_or_update_row"))
            self._conn.execute("COMMIT;")
            return persisted

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
        """Record proposal payload and transition machine state."""

        with self._lock:
            row = self._mutate_row(task_name, item_id)
            now = _utcnow()
            if item_locator is not None:
                row.item_locator = item_locator
            if input_fingerprint is not None:
                row.input_fingerprint = input_fingerprint

            row.proposal_payload = proposal_payload
            row.proposal_result = proposal_result
            row.machine_status = machine_status
            row.human_status = HumanDecision.NEEDS_REVIEW
            row.proposal_run_id = proposal_run_id or make_run_id(item_id, row.item_locator, {"stage": "proposal"})
            row.proposal_code_commit = proposal_code_commit
            row.proposal_recorded_at = now
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            row.rerun_eligible = machine_status in {MachineStatus.PROPOSED, MachineStatus.PROVIDER_ERROR}
            row.updated_at = now
            row.extras.update(extras or {})
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("record_proposal"))
            self._conn.execute("COMMIT;")
            return persisted

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
        """Record review output and transition machine state."""

        with self._lock:
            row = self._mutate_row(task_name, item_id)
            now = _utcnow()
            row.review_payload = review_payload
            row.review_result = review_result
            row.review_run_id = review_run_id or make_run_id(item_id, row.item_locator, {"stage": "review"})
            row.review_code_commit = review_code_commit
            row.review_recorded_at = now
            row.machine_status = machine_status
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            row.rerun_eligible = machine_status != MachineStatus.SUPERSEDED
            row.updated_at = now
            row.extras.update(extras or {})
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("record_review"))
            self._conn.execute("COMMIT;")
            return persisted

    def record_human_decision(
        self,
        *,
        task_name: str,
        item_id: str,
        decision: HumanDecision,
        decision_payload: Any | None = None,
        decision_run_id: str | None = None,
        decision_code_commit: str | None = None,
        notes: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> LedgerRow:
        """Record human decision and transition machine/human state."""

        with self._lock:
            row = self._mutate_row(task_name, item_id)
            now = _utcnow()
            row.human_decision_run_id = decision_run_id or make_run_id(item_id, row.item_locator, {"stage": "human"})
            row.human_decision_code_commit = decision_code_commit
            row.human_status = decision
            row.human_notes = notes
            row.human_decision_payload = decision_payload
            row.human_status_updated_at = now
            row.human_decision_recorded_at = now

            if decision == HumanDecision.APPROVED:
                row.machine_status = MachineStatus.APPLY_READY
            elif decision == HumanDecision.REJECTED:
                row.machine_status = MachineStatus.REVIEWED
            else:
                row.machine_status = MachineStatus.PROPOSED

            row.rerun_eligible = decision != HumanDecision.APPROVED
            row.machine_status_updated_at = now
            row.updated_at = now
            row.extras.update(extras or {})
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("record_human_decision"))
            self._conn.execute("COMMIT;")
            return persisted

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
        """Record result from apply attempt."""

        with self._lock:
            row = self._mutate_row(task_name, item_id)
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
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("record_apply"))
            self._conn.execute("COMMIT;")
            return persisted

    def mark_superseded(self, *, task_name: str, item_id: str) -> LedgerRow:
        """Mark an item as superseded."""

        with self._lock:
            existing = self._load_state(task_name, item_id)
            if existing is None:
                raise KeyError(f"missing ledger row {task_name}:{item_id}")
            row = self._clone_row(existing)
            now = _utcnow()
            row.machine_status = MachineStatus.SUPERSEDED
            row.machine_status_updated_at = now
            row.human_status_updated_at = now
            row.updated_at = now
            row.rerun_eligible = False
            self._conn.execute("BEGIN IMMEDIATE;")
            persisted = self._persist(row, event_type=_event_type_for_method("mark_superseded"))
            self._conn.execute("COMMIT;")
            return persisted

    def run_apply(
        self,
        *,
        query: LedgerQuery,
        callback: ApplyCallback,
        dry_run: bool,
        policy: ApplyPolicy = ApplyPolicy(),
    ) -> list[ApplyResult]:
        """Run an optional apply callback across rows selected by query."""

        selected_rows = self.query_rows(query)
        results: list[ApplyResult] = []

        for row in selected_rows:
            if row.machine_status not in {MachineStatus.APPLY_READY, MachineStatus.PROVIDER_ERROR} or row.human_status != HumanDecision.APPROVED:
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

                callback_result = _normalize_apply_callback_result(callback(row, dry_run))
                outcome = callback_result

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

    def import_jsonl(self, source_jsonl: Path, *, event_type: str = "import") -> int:
        """Import one JSONL ledger export into this SQLite store."""

        if not source_jsonl.exists():
            raise FileNotFoundError(str(source_jsonl))

        count = 0
        with source_jsonl.open("r", encoding="utf-8") as fp:
            lines = [line for line in fp if line.strip()]

        if not lines:
            return 0

        with self._lock:
            for line in lines:
                payload = json.loads(line)
                row = LedgerRow.from_dict(payload)
                self._conn.execute("BEGIN IMMEDIATE;")
                existing = self._load_state(row.task_name, row.item_id)
                next_row = self._clone_row(row)
                if existing is not None:
                    next_row.version = max(existing.version + 1, row.version or existing.version + 1)
                    next_row.created_at = existing.created_at
                else:
                    next_row.version = max(1, row.version)
                    if next_row.created_at is None:
                        next_row.created_at = _utcnow()
                self._persist(next_row, event_type=event_type)
                self._conn.execute("COMMIT;")
                count += 1

        return count


def _normalize_apply_callback_result(value: Any) -> _ApplyCallbackResult:
    """Normalize callback output into a normalized apply result."""

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
        message = value.get("message")
        if "success" not in value and "apply_payload" not in value and "apply_result" not in value:
            return _ApplyCallbackResult(success=True, apply_payload=None, apply_result=value, message=message)

        return _ApplyCallbackResult(
            success=bool(value.get("success", True)),
            apply_payload=value.get("apply_payload"),
            apply_result=value.get("apply_result", value),
            message=message,
        )

    if isinstance(value, bool):
        return _ApplyCallbackResult(success=value)

    return _ApplyCallbackResult(success=True, apply_payload=None, apply_result=value)
