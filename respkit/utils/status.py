"""Run status enums for v1 execution."""

from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    """Stable status values for execution outcomes."""

    SUCCESS = "success"
    PREFLIGHT_MODEL_NOT_FOUND = "preflight_model_not_found"
    PROVIDER_ERROR = "provider_error"
    PARSE_ERROR = "parse_error"
    VALIDATION_FAILED = "validation_failed"
    ACTION_FAILED = "action_failed"
    REVIEW_FAILED = "review_failed"

