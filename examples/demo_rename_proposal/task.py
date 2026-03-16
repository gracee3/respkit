"""Synthetic demo task for the public SDK."""

from __future__ import annotations

import re
from pathlib import Path

from respkit.actions import AppendManifestAction, WriteMarkdownAction
from respkit.inputs import NormalizedInput
from respkit.tasks import ReviewPolicy, TaskDefinition
from respkit.validators import EnumCaseNormalizer, FillDefaultsValidator, TrimWhitespaceValidator

from .schemas import DemoRenameProposalOutput, DemoRenameReviewOutput


_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def extract_anchors(source_path: Path, text: str) -> dict[str, str | None]:
    """Extract light-weight anchors from a synthetic file path and text."""

    slug_source = source_path.stem
    slug = slug_source.lower().replace("_", "-")
    subject_line = ""
    for line in text.splitlines():
        if line.lower().startswith("subject:"):
            subject_line = line.split(":", 1)[1].strip()
            break
    return {
        "filename": slug_source,
        "subject_slug": _slugify(subject_line) if subject_line else None,
        "slug": _slugify(slug) if slug else None,
    }


def _slugify(value: str) -> str:
    tokens = _WORD_RE.findall(value.lower())
    if not tokens:
        return "item"
    return "-".join(tokens)[:64]


def _title_case_actor(value: str) -> str:
    if not value:
        return "Unknown"
    # Keep acronyms and already-cased identifiers stable.
    if value.isupper() or value.isspace() or value.lower() == value:
        return " ".join(part.capitalize() for part in value.split())
    return value.strip()


def normalize_proposal_output(payload: dict, item: NormalizedInput) -> dict:
    """Normalize proposal payload for deterministic output from the model."""

    kind = str(payload.get("kind", "other")).strip().lower()
    actor = str(payload.get("actor", "")).strip()
    slug = str(payload.get("slug", "")).strip()
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, confidence))

    if not slug:
        anchors = extract_anchors(item.source_path, item.decoded_text)
        slug = anchors.get("subject_slug") or anchors.get("slug") or _slugify(item.source_path.stem)

    return {
        "kind": kind or "other",
        "actor": _title_case_actor(actor),
        "slug": _slugify(slug),
        "confidence": confidence,
        "notes": str(payload.get("notes", "")).strip() or "Generated from proposal text.",
        "evidence_snippet": payload.get("evidence_snippet"),
        "evidence_page": payload.get("evidence_page"),
    }


def normalize_review_output(payload: dict, _: NormalizedInput) -> dict:
    return {
        "decision": str(payload.get("decision", "fail")).strip().lower(),
        "notes": str(payload.get("notes", "")).strip() or "No notes provided.",
        "recommended_adjustments": str(payload.get("recommended_adjustments", "") or ""),
    }


def build_tasks(manifest_writer=None, model_name: str = "gpt-oss-20b", provider_timeout: float = 30.0):
    """Build proposal + review task definitions for the public demo."""

    del manifest_writer

    task_root = Path(__file__).resolve().parent
    proposal_prompt = task_root / "prompts" / "demo_rename_proposal.md"
    review_prompt = task_root / "prompts" / "demo_rename_review.md"

    review_task = TaskDefinition(
        name="demo_rename_review",
        description="Optional review pass for rename proposal.",
        prompt_template_path=review_prompt,
        response_model=DemoRenameReviewOutput,
        provider_model=model_name,
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"decision": ["pass", "fail", "uncertain"]}),
            FillDefaultsValidator(defaults={"recommended_adjustments": None, "notes": ""}),
        ),
        output_normalizer=normalize_review_output,
        timeout_seconds=provider_timeout,
        actions=(WriteMarkdownAction(filename="review.md", content_builder=lambda context: f"{context.validated_output}\n"),),
    )

    proposal_task = TaskDefinition(
        name="demo_rename_proposal",
        description="Propose rename metadata for a single document.",
        prompt_template_path=proposal_prompt,
        response_model=DemoRenameProposalOutput,
        provider_model=model_name,
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(
                field_values={
                    "kind": ["correspondence", "invoice", "note", "legal", "other"],
                    "confidence": [],
                }
            ),
            FillDefaultsValidator(defaults={"notes": ""}),
        ),
        output_normalizer=normalize_proposal_output,
        review_policy=ReviewPolicy(
            task=review_task,
            context_builder=lambda original_item, first_output: {
                "old_filename": original_item.source_id,
                "first_output": first_output,
                "text": original_item.decoded_text,
            },
        ),
        timeout_seconds=provider_timeout,
        actions=(
            WriteMarkdownAction(
                filename="proposal.md",
                content_builder=lambda context: f"kind={context.validated_output.get('kind')}\nactor={context.validated_output.get('actor')}\nslug={context.validated_output.get('slug')}\nconfidence={context.validated_output.get('confidence')}\n",
            ),
            AppendManifestAction(
                summary_fields=("kind", "actor", "slug", "confidence"),
                file_name="demo_summary.json",
            ),
        ),
    )

    return proposal_task, review_task
