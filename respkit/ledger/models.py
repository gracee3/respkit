"""Generic, task-agnostic adjudication ledger models."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MachineStatus(str, Enum):
    """Machine-driven adjudication lifecycle states."""

    NOT_RUN = "not_run"
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    PROVIDER_ERROR = "provider_error"
    APPLY_READY = "apply_ready"
    APPLIED = "applied"
    SUPERSEDED = "superseded"


class HumanDecision(str, Enum):
    """Human adjudication states."""

    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LedgerRow:
    """One item row in the adjudication ledger."""

    task_name: str
    item_id: str
    machine_status: MachineStatus = MachineStatus.NOT_RUN
    human_status: HumanDecision = HumanDecision.NEEDS_REVIEW
    rerun_eligible: bool = False
    proposal_payload: Any | None = None
    review_payload: Any | None = None
    apply_payload: Any | None = None
    proposal_result: Any | None = None
    review_result: Any | None = None
    apply_result: Any | None = None
    item_locator: str | None = None
    input_fingerprint: str | None = None
    proposal_run_id: str | None = None
    review_run_id: str | None = None
    human_decision_run_id: str | None = None
    apply_run_id: str | None = None
    proposal_code_commit: str | None = None
    review_code_commit: str | None = None
    human_decision_code_commit: str | None = None
    apply_code_commit: str | None = None
    applied_in_commit: str | None = None
    proposal_recorded_at: datetime | None = None
    review_recorded_at: datetime | None = None
    human_decision_recorded_at: datetime | None = None
    apply_recorded_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    machine_status_updated_at: datetime = field(default_factory=_utcnow)
    human_status_updated_at: datetime = field(default_factory=_utcnow)
    extras: dict[str, Any] = field(default_factory=dict)

    def is_unresolved(self) -> bool:
        """Return True when the item should be considered unresolved."""

        if self.machine_status in {MachineStatus.APPLIED, MachineStatus.SUPERSEDED}:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize row to JSON-safe dict values."""

        def _normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: _normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_normalize(item) for item in value]
            if isinstance(value, tuple):
                return [_normalize(item) for item in value]
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, set):
                return [_normalize(item) for item in sorted(value, key=lambda item: str(item))]
            return value

        return _normalize(
            {
                "task_name": self.task_name,
                "item_id": self.item_id,
                "machine_status": self.machine_status,
                "human_status": self.human_status,
                "rerun_eligible": self.rerun_eligible,
                "proposal_payload": self.proposal_payload,
                "review_payload": self.review_payload,
                "apply_payload": self.apply_payload,
                "proposal_result": self.proposal_result,
                "review_result": self.review_result,
                "apply_result": self.apply_result,
                "item_locator": self.item_locator,
                "input_fingerprint": self.input_fingerprint,
                "proposal_run_id": self.proposal_run_id,
                "review_run_id": self.review_run_id,
                "human_decision_run_id": self.human_decision_run_id,
                "apply_run_id": self.apply_run_id,
                "proposal_code_commit": self.proposal_code_commit,
                "review_code_commit": self.review_code_commit,
                "human_decision_code_commit": self.human_decision_code_commit,
                "apply_code_commit": self.apply_code_commit,
                "applied_in_commit": self.applied_in_commit,
                "proposal_recorded_at": self.proposal_recorded_at,
                "review_recorded_at": self.review_recorded_at,
                "human_decision_recorded_at": self.human_decision_recorded_at,
                "apply_recorded_at": self.apply_recorded_at,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "machine_status_updated_at": self.machine_status_updated_at,
                "human_status_updated_at": self.human_status_updated_at,
                "extras": self.extras,
            }
        )

    @staticmethod
    def _parse_datetime(raw: str | None) -> datetime | None:
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LedgerRow":
        """Deserialize a row from stored JSON payload."""

        return cls(
            task_name=payload["task_name"],
            item_id=payload["item_id"],
            machine_status=MachineStatus(payload.get("machine_status", MachineStatus.NOT_RUN)),
            human_status=HumanDecision(payload.get("human_status", HumanDecision.NEEDS_REVIEW)),
            rerun_eligible=bool(payload.get("rerun_eligible", False)),
            proposal_payload=payload.get("proposal_payload"),
            review_payload=payload.get("review_payload"),
            apply_payload=payload.get("apply_payload"),
            proposal_result=payload.get("proposal_result"),
            review_result=payload.get("review_result"),
            apply_result=payload.get("apply_result"),
            item_locator=payload.get("item_locator"),
            input_fingerprint=payload.get("input_fingerprint"),
            proposal_run_id=payload.get("proposal_run_id"),
            review_run_id=payload.get("review_run_id"),
            human_decision_run_id=payload.get("human_decision_run_id"),
            apply_run_id=payload.get("apply_run_id"),
            proposal_code_commit=payload.get("proposal_code_commit"),
            review_code_commit=payload.get("review_code_commit"),
            human_decision_code_commit=payload.get("human_decision_code_commit"),
            apply_code_commit=payload.get("apply_code_commit"),
            applied_in_commit=payload.get("applied_in_commit"),
            proposal_recorded_at=cls._parse_datetime(payload.get("proposal_recorded_at")),
            review_recorded_at=cls._parse_datetime(payload.get("review_recorded_at")),
            human_decision_recorded_at=cls._parse_datetime(payload.get("human_decision_recorded_at")),
            apply_recorded_at=cls._parse_datetime(payload.get("apply_recorded_at")),
            created_at=cls._parse_datetime(payload.get("created_at")) or _utcnow(),
            updated_at=cls._parse_datetime(payload.get("updated_at")) or _utcnow(),
            machine_status_updated_at=cls._parse_datetime(payload.get("machine_status_updated_at")) or _utcnow(),
            human_status_updated_at=cls._parse_datetime(payload.get("human_status_updated_at")) or _utcnow(),
            extras=dict(payload.get("extras", {})),
        )
