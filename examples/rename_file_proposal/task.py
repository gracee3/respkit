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
    for match in re.finditer(r"[A-Za-z]+", filename):
        token = match.group(0)
        if len(token) > 2:
            return token
    return None


_TIME_PATTERNS = (r"\b(2[0-3]|[01]?[0-9]):[0-5][0-9]\b",)


_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("assistant principal", (r"\bassistant\s+principal\b", r"\bprincipal\s+assistant\b", r"\bassistant\s*pr\.?\b", r"\basst\s+principal\b")),
    ("deputy principal", (r"\bdeputy\s+principal\b",)),
    ("vice principal", (r"\bvice\s+principal\b",)),
    ("assistant manager", (r"\bassistant\s+manager\b",)),
    ("principal", (r"\bprincipal\b",)),
    ("pta", (r"\bpta\b",)),
)

_SENDER_PATTERNS = (
    r"(?im)^\s*(?:from|sender|sent\s+by)\s*:\s*([^\n]+)$",
    r"(?im)^\s*signature\s*:\s*([^\n]+)$",
)

_FIRST_PERSON_PATTERNS = (
    r"(?im)\bI\s+am\s+the\s+([^\n,.;:]+)",
    r"(?im)\bwe\s+are\s+the\s+([^\n,.;:]+)",
)

_RECIPIENT_PATTERNS = (
    r"(?im)^\s*dear\s+([^\n,]+)",
    r"(?im)^\s*(?:to|cc|bcc|recipient)\s*:\s*([^\n]+)",
)


def _find_role_in_text(text: str) -> str | None:
    lowered = text.lower()
    for canonical, patterns in _ROLE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return canonical
    return None


def _extract_actor_candidate(path: Path | None, text: str) -> str | None:
    if path is not None:
        filename_actor = _find_role_in_text(path.name.replace("-", " ").replace("_", " "))
        if filename_actor:
            return filename_actor

    for pattern in _SENDER_PATTERNS:
        match = re.search(pattern, text)
        if match:
            actor = _find_role_in_text(match.group(1))
            if actor:
                return actor

    for pattern in _FIRST_PERSON_PATTERNS:
        match = re.search(pattern, text)
        if match:
            actor = _find_role_in_text(match.group(0))
            if actor:
                return actor

    for pattern in _RECIPIENT_PATTERNS:
        match = re.search(pattern, text)
        if match:
            actor = _find_role_in_text(match.group(1))
            if actor:
                return actor

    return _find_role_in_text(text)


def _first_time(text: str) -> str | None:
    for pattern in _TIME_PATTERNS:
        found = _first(pattern, text)
        if found:
            return found

    # Avoid false positives from date-like groups such as 2024-10-09 by only accepting
    # explicit four-digit time tokens that look like HHMM and are not obviously year-like.
    for match in re.finditer(r"\d{4}", text):
        token = match.group(0)
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if (before == "-" and after == "-") or (before == "" and after == "-"):
            continue
        hour = int(token[:2])
        minute = int(token[2:])
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def extract_anchors(path: Path | None, text: str) -> dict[str, str | None]:
    """Extract deterministic metadata from filename and text."""

    old_filename = path.name if path else ""
    return {
        "old_filename": old_filename,
        "source_token": _first_alpha_token(old_filename) or "",
        "candidate_date": _first(r"\d{4}-\d{2}-\d{2}|\d{8}", old_filename),
        "candidate_time": _first_time(old_filename),
        "actor_anchor": _extract_actor_candidate(path, text),
        "text": text,
    }


def _canonicalize_actor(raw_actor: str, actor_anchor: str | None) -> str:
    actor = raw_actor.strip().lower()
    if not actor:
        return actor

    aliases = {
        "asst principal": "assistant principal",
        "asst. principal": "assistant principal",
        "assistant pr": "assistant principal",
        "principal (asst)": "assistant principal",
    }
    for alias, canonical in aliases.items():
        if actor == alias:
            return canonical

    if actor_anchor and actor == "principal" and actor_anchor != "principal":
        return actor_anchor
    if actor_anchor and actor in {"ap", "a principal", "asst principal", "assistant principal"}:
        if actor_anchor != "principal":
            return actor_anchor

    return actor


def _calibrate_confidence(payload: dict[str, Any], anchors: dict[str, str | None]) -> Any:
    confidence = payload.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        return confidence

    if confidence_value < 0:
        confidence_value = 0.0
    if confidence_value > 1:
        confidence_value = 1.0

    # If anchors are weak or conflicting, avoid overconfident outputs.
    missing_date_or_time = anchors.get("candidate_date") is None or anchors.get("candidate_time") is None
    if missing_date_or_time and confidence_value > 0.85:
        confidence_value = 0.85

    if anchors.get("actor_anchor") is None and confidence_value > 0.9:
        confidence_value = 0.9

    return confidence_value


def normalize_proposal_output(payload: dict[str, Any], item: NormalizedInput) -> dict[str, Any]:
    """Deterministic post-parse normalization for proposal output."""

    anchors = extract_anchors(item.source_path, item.decoded_text)
    output = dict(payload)

    if "actor" in output and isinstance(output.get("actor"), str):
        output["actor"] = _canonicalize_actor(output["actor"], anchors.get("actor_anchor"))
    if "confidence" in output:
        output["confidence"] = _calibrate_confidence(output, anchors)
    return output


def _normalize_review_adjustment_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_review_adjustment_value(value[key]) for key in sorted(value.keys())}
    if isinstance(value, list):
        return [_normalize_review_adjustment_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_review_adjustment_value(item) for item in value]
    if isinstance(value, set):
        return [_normalize_review_adjustment_value(item) for item in sorted(value)]
    if value is None:
        return ""
    return value


def normalize_review_output(payload: dict[str, Any], _: NormalizedInput) -> dict[str, Any]:
    """Normalize review output field types without adding semantic assumptions."""

    output = dict(payload)
    adjustments = output.get("recommended_adjustments")
    if adjustments is None:
        output["recommended_adjustments"] = ""
    elif not isinstance(adjustments, str):
        normalized = _normalize_review_adjustment_value(adjustments)
        output["recommended_adjustments"] = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return output


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
        response_transforms=(normalize_review_output,),
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
        response_transforms=(normalize_proposal_output,),
        review_policy=ReviewPolicy(
            task=review_task,
            context_builder=build_review_context,
        ),
    )

    return proposal_task, review_task
