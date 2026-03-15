"""Example rename proposal and review task constructors."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from respkit.actions import AppendManifestAction, WriteMarkdownAction
from respkit.actions.base import ActionContext
from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.tasks import ReviewPolicy, TaskDefinition
from respkit.validators import EnumCaseNormalizer, FillDefaultsValidator, TrimWhitespaceValidator
from .schemas import RenameProposalOutput, RenameReviewOutput


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None


def _first_alpha_token(filename: str) -> str | None:
    match = re.match(r"[A-Za-z0-9_-]+", filename.replace(" ", "_"))
    return match.group(0) if match else None


def extract_anchors(path: Path | None, text: str) -> dict[str, str | None]:
    """Extract deterministic metadata from filename and text."""

    old_filename = path.name if path else ""
    return {
        "old_filename": old_filename,
        "source_token": _first_alpha_token(old_filename) or "",
        "candidate_date": _first(r"\d{4}-\d{2}-\d{2}|\d{8}", old_filename),
        "candidate_time": _first(r"\b(2[0-3]|[01]?[0-9]):[0-5][0-9]\b", old_filename),
        "text": text,
    }


def build_proposal_context(item: NormalizedInput) -> dict[str, Any]:
    anchors = extract_anchors(item.source_path, item.decoded_text)
    anchors["excerpt"] = item.decoded_text[:2500]
    return anchors


def build_review_context(item: NormalizedInput, first_output: dict[str, Any]) -> dict[str, Any]:
    anchors = extract_anchors(item.source_path, item.decoded_text)
    return {
        "old_filename": anchors.get("old_filename", ""),
        "source_token": anchors.get("source_token", ""),
        "candidate_date": anchors.get("candidate_date"),
        "candidate_time": anchors.get("candidate_time"),
        "first_output": json.dumps(first_output, ensure_ascii=False),
    }


def build_review_prompt_context(item: NormalizedInput) -> dict[str, Any]:
    return {
        "text": item.decoded_text,
        "old_filename": item.metadata.get("old_filename", ""),
        "source_token": item.metadata.get("source_token", ""),
        "candidate_date": item.metadata.get("candidate_date", ""),
        "candidate_time": item.metadata.get("candidate_time", ""),
        "first_output": item.metadata.get("first_output", "{}"),
    }


def _proposal_markdown(context: ActionContext) -> str:
    output = context.validated_output if isinstance(context.validated_output, dict) else {}
    return "\n".join(
        [
            "# Rename Proposal",
            "",
            f"- task: {context.task_name}",
            f"- run_id: {context.run_id}",
            f"- source: {context.input.source_id}",
            "",
            "## Proposal",
            f"- kind: {output.get('kind', '')}",
            f"- actor: {output.get('actor', '')}",
            f"- slug: {output.get('slug', '')}",
            f"- confidence: {output.get('confidence', '')}",
            f"- notes: {output.get('notes', '')}",
        ]
    )


def build_tasks(
    prompt_root: Path | None = None,
    manifest_writer: ManifestWriter | None = None,
    model_name: str = "gpt-oss-20b",
) -> tuple[TaskDefinition, TaskDefinition]:
    """Return (first-pass task, review task)."""

    prompt_root = prompt_root or (Path(__file__).resolve().parent / "prompts")
    proposal_prompt = prompt_root / "rename_file_proposal.md"
    review_prompt = prompt_root / "rename_file_review.md"

    actions = [WriteMarkdownAction(filename="proposal_row.md", content_builder=_proposal_markdown)]
    if manifest_writer is not None:
        actions.append(AppendManifestAction(manifest_writer))

    review_task = TaskDefinition(
        name="rename_file_review",
        description="Review a rename proposal output.",
        prompt_template_path=review_prompt,
        response_model=RenameReviewOutput,
        provider_model=model_name,
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"decision": ["pass", "fail", "uncertain"]}),
            FillDefaultsValidator(defaults={"recommended_adjustments": ""}),
        ),
        prompt_context_builder=build_review_prompt_context,
    )

    proposal_task = TaskDefinition(
        name="rename_file_proposal",
        description="Propose deterministic rename metadata for a text file.",
        prompt_template_path=proposal_prompt,
        response_model=RenameProposalOutput,
        provider_model=model_name,
        min_input_chars=20,
        validators=(
            TrimWhitespaceValidator(),
            EnumCaseNormalizer(field_values={"kind": ["legal", "billing", "correspondence", "notes", "other"]}),
            FillDefaultsValidator(defaults={"evidence_snippet": "", "evidence_page": 0, "notes": ""}),
        ),
        actions=tuple(actions),
        prompt_context_builder=build_proposal_context,
        review_policy=ReviewPolicy(
            task=review_task,
            context_builder=build_review_context,
        ),
    )

    return proposal_task, review_task
