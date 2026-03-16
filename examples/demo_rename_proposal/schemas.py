"""Schemas for the synthetic public demo rename task."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DemoRenameProposalOutput(BaseModel):
    """Structured output for the demo proposal task."""

    kind: Literal["correspondence", "invoice", "note", "legal", "other"]
    actor: str
    slug: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str
    evidence_snippet: str | None = None
    evidence_page: int | None = None


class DemoRenameReviewOutput(BaseModel):
    """Structured output for the optional demo review pass."""

    decision: Literal["pass", "fail", "uncertain"]
    notes: str
    recommended_adjustments: str | None = None
