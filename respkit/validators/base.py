"""Validator primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ValidatorResult:
    """Result returned by a deterministic validator."""

    payload: dict[str, Any]
    errors: list[str]
    repaired: bool = False


class Validator:
    """Base class for deterministic validators."""

    def apply(self, payload: dict[str, Any]) -> ValidatorResult:
        """Validate and optionally repair payload data."""

        raise NotImplementedError


def run_validators(payload: dict[str, Any], validators: list[Validator]) -> ValidatorResult:
    """Apply validators in order, keeping deterministic behavior."""

    working_payload = dict(payload)
    repaired = False
    errors: list[str] = []

    for validator in validators:
        result = validator.apply(working_payload)
        working_payload = result.payload
        errors.extend(result.errors)
        if result.repaired:
            repaired = True

    return ValidatorResult(payload=working_payload, errors=errors, repaired=repaired)
