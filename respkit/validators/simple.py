"""Simple deterministic validators used by v1 tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import Validator, ValidatorResult


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_trim(v) for v in value]
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    return value


class TrimWhitespaceValidator(Validator):
    """Trims leading/trailing whitespace from string fields."""

    def apply(self, payload: dict[str, Any]) -> ValidatorResult:
        return ValidatorResult(payload=_trim(payload), errors=[], repaired=True)


@dataclass
class EnumCaseNormalizer(Validator):
    """Normalizes enumerated string values case-insensitively."""

    field_values: dict[str, list[str]]

    def apply(self, payload: dict[str, Any]) -> ValidatorResult:
        repaired = False
        updated = dict(payload)
        for field_name, allowed in self.field_values.items():
            if field_name not in updated:
                continue
            current = updated[field_name]
            if not isinstance(current, str):
                continue

            normalized = next((v for v in allowed if v.lower() == current.lower()), None)
            if normalized is None:
                return ValidatorResult(
                    payload=updated,
                    errors=[f"{field_name}='{current}' is not one of {allowed}"],
                    repaired=False,
                )
            if normalized != current:
                updated[field_name] = normalized
                repaired = True

        return ValidatorResult(payload=updated, errors=[], repaired=repaired)


@dataclass
class FillDefaultsValidator(Validator):
    """Fill absent fields with schema defaults for deterministic downstream behavior."""

    defaults: dict[str, Any]

    def apply(self, payload: dict[str, Any]) -> ValidatorResult:
        updated = dict(payload)
        for key, value in self.defaults.items():
            if key not in updated or updated[key] is None:
                updated[key] = value
        return ValidatorResult(payload=updated, errors=[], repaired=True)
