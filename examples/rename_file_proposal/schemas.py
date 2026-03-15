"""Schemas for the example tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RenameProposalOutput(BaseModel):
    """Structured output for proposal task."""

    kind: Literal["legal", "billing", "correspondence", "notes", "other"]
    actor: str
    slug: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str
    evidence_snippet: str | None = None
    evidence_page: int | None = None


class RenameReviewOutput(BaseModel):
    """Structured output for review task."""

    decision: Literal["pass", "fail", "uncertain"]
    notes: str
    recommended_adjustments: str | None = None
