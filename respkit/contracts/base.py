"""Schema helper types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ContractViolation:
    """A single contract validation issue."""

    path: str
    message: str


@dataclass
class ValidationReport:
    """Contract validation report emitted by the SDK."""

    valid: bool
    value: Any | None = None
    errors: list[ContractViolation] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "value": self.value,
            "errors": [vars(v) for v in (self.errors or [])],
        }
