"""Example rename proposal and review task constructors."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from respkit.actions import AppendManifestAction, WriteMarkdownAction
from respkit.actions.base import ActionContext
from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.providers import ProviderConfig
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

_HHMM_PATTERN = re.compile(r"\b(\d{1,2}):([0-5]\d)(?::[0-5]\d)?\s*(am|pm)?\b", re.IGNORECASE)
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?\b"
)
_QUOTED_TIME_PATTERN = re.compile(
    r"\b(1[0-2]|0?\d):([0-5]\d)\s*(am|pm)\b",
    re.IGNORECASE,
)

_THREAD_SEPARATOR_PATTERNS = (
    re.compile(r"(?im)^\s*-{2,}\s*(original message|forwarded message)\s*-{2,}\s*$"),
    re.compile(r"(?im)^On .+wrote:\s*$"),
    re.compile(r"(?im)^>+\s*from:\s+"),
)


_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("assistant principal", (r"\bassistant\s+principal\b", r"\bprincipal\s+assistant\b", r"\bassistant\s*pr\.?\b", r"\basst\s+principal\b")),
    ("deputy principal", (r"\bdeputy\s+principal\b",)),
    ("vice principal", (r"\bvice\s+principal\b",)),
    ("assistant manager", (r"\bassistant\s+manager\b",)),
    ("school counselor", (r"\bschool\s+counselor\b", r"\bcounselor\b")),
    ("school psychologist", (r"\bschool\s+psychologist\b", r"\bpsychologist\b")),
    ("principal", (r"\bprincipal\b",)),
    ("director", (r"\bdirector\b",)),
    ("teacher", (r"\bteacher\b",)),
    ("student", (r"\bstudent\b",)),
    ("pta", (r"\bpta\b",)),
    ("care", (r"\bcare\b", r"\bcare\s+team\b")),
    ("system", (r"\bsystem\b",)),
    ("client", (r"\bclient\b",)),
    ("parent", (r"\bparent\b", r"\bparents\b")),
)

_SENDER_PATTERNS = (
    r"(?im)^\s*(?:from|sender|sent\s+by)\s*:\s*([^\n]+)$",
    r"(?im)^\s*signature\s*:\s*([^\n]+)$",
)

_FIRST_PERSON_PATTERNS = (
    r"(?im)^\s*I\s+am\s+the\s+([^\n,.;:]+)",
    r"(?im)^\s*We\s+are\s+the\s+([^\n,.;:]+)",
    r"(?im)^\s*I\s+wrote\s+as\s+([^\n,.;:]+)",
)

_RECIPIENT_PATTERNS = (
    r"(?im)^\s*dear\s+([^\n,]+)",
    r"(?im)^\s*(?:to|cc|bcc|recipient)\s*:\s*([^\n]+)",
)


def _normalize_actor_text(raw: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \"'<>")
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"", "n/a", "na", "none"}:
        return None
    return lowered


def _find_role_anchor(actor_text: str) -> str | None:
    if not actor_text:
        return None

    lowered = actor_text.lower()
    for canonical, patterns in _ROLE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return canonical

    return _normalize_actor_text(raw=actor_text)


def _find_role_in_text(text: str) -> str | None:
    lowered = text.lower()
    for canonical, patterns in _ROLE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return canonical
    return None


def _split_current_text(text: str) -> tuple[str, str]:
    earliest = None
    for pattern in _THREAD_SEPARATOR_PATTERNS:
        match = pattern.search(text)
        if match and (earliest is None or match.start() < earliest):
            earliest = match.start()
    if earliest is None:
        return text, ""
    return text[:earliest].rstrip(), text[earliest:].lstrip()


def _normalize_role(text: str) -> str:
    return text.strip(" -_").lower()


def _is_more_specific_role(current_actor: str, candidate_actor: str) -> bool:
    if current_actor == candidate_actor:
        return False

    specificity_hierarchy = {
        "principal": 10,
        "assistant principal": 20,
        "vice principal": 18,
        "deputy principal": 18,
        "director": 14,
        "care": 12,
        "teacher": 8,
        "school counselor": 9,
        "school psychologist": 9,
        "parent": 11,
        "system": 6,
        "client": 6,
        "student": 7,
    }
    current_value = specificity_hierarchy.get(_normalize_role(current_actor), 0)
    candidate_value = specificity_hierarchy.get(_normalize_role(candidate_actor), 0)
    if candidate_value > current_value:
        return True

    return len(candidate_actor) > len(current_actor)


def _extract_actor_signals(
    path: Path | None,
    text: str,
) -> tuple[str, str, list[str], bool]:
    current_text, _ = _split_current_text(text)
    signals = OrderedDict[str, str]()
    thread_ambiguous = _split_current_text(text)[1] != ""

    def _add_signal(source: str, value: str | None) -> None:
        if not value:
            return
        if source in signals:
            return
        signals[source] = value

    if path is not None:
        filename_actor = _find_role_in_text(path.name.replace("-", " ").replace("_", " "))
        if filename_actor:
            _add_signal("filename", filename_actor)

    for pattern in _SENDER_PATTERNS:
        match = re.search(pattern, current_text)
        if match:
            actor = _find_role_anchor(match.group(1))
            if actor:
                _add_signal("sender", actor)
                break

    for pattern in _FIRST_PERSON_PATTERNS:
        match = re.search(pattern, current_text)
        if match:
            actor = _find_role_anchor(match.group(1))
            if actor:
                _add_signal("first_person", actor)
                break

    for pattern in _RECIPIENT_PATTERNS:
        match = re.search(pattern, current_text)
        if match:
            actor = _find_role_anchor(match.group(1))
            if actor:
                _add_signal("recipient", actor)
                break

    body_actor = _find_role_in_text(current_text)
    if body_actor:
        _add_signal("body", body_actor)

    actor_anchor_sources = list(signals.keys())
    actor_anchor: str | None = None
    actor_anchor_source: str | None = None
    for source in ("filename", "sender", "first_person", "recipient", "body"):
        if source in signals:
            actor_anchor = signals[source]
            actor_anchor_source = source
            break

    if actor_anchor is None:
        actor_anchor = None
        actor_anchor_source = None

    actor_evidence_values = list(signals.values())
    mixed_actor_evidence = len(set(actor_evidence_values)) > 1
    return actor_anchor or "", actor_anchor_source or "", actor_evidence_values, mixed_actor_evidence


def _extract_time_from_text(text: str) -> str | None:
    match = _ISO_TIMESTAMP_PATTERN.search(text)
    if match:
        hour = int(match.group(2))
        minute = int(match.group(3))
        return f"{hour:02d}:{minute:02d}"

    match = _HHMM_PATTERN.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if match.group(3):
            am_pm = match.group(3).lower()
            if am_pm == "pm" and hour != 12:
                hour += 12
            elif am_pm == "am" and hour == 12:
                hour = 0
        return f"{hour:02d}:{minute:02d}"

    match = _QUOTED_TIME_PATTERN.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        am_pm = match.group(3).lower()
        if am_pm == "pm" and hour != 12:
            hour += 12
        elif am_pm == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    return None


def _extract_time_from_filename(filename: str) -> str | None:
    for candidate in (_first(_TIME_PATTERNS[0], filename),):
        if candidate:
            return candidate

    parts = re.split(r"[^0-9]", filename)
    for index, part in enumerate(parts):
        if len(part) != 4:
            continue
        part_value = int(part)
        if index == 0 and 1900 <= part_value <= 2099:
            continue
        hour = part_value // 100
        minute = part_value % 100
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        if 1900 <= part_value <= 2099 and index != 0:
            continue
    return None


def _confidence_cap_for_actor_evidence(
    confidence: float,
    actor_anchor_source: str,
    actor_evidence_count: int,
    mixed_actor_evidence: bool,
    thread_ambiguous: bool,
) -> float:
    cap = 1.0
    if actor_anchor_source in {"recipient", "body"}:
        cap = min(cap, 0.75)
    if mixed_actor_evidence:
        cap = min(cap, 0.7)
    if actor_evidence_count > 1:
        cap = min(cap, 0.8)
    if thread_ambiguous:
        cap = min(cap, 0.8)
    return min(confidence, cap)


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
    actor_anchor, actor_anchor_source, actor_values, mixed_actor_evidence = _extract_actor_signals(path, text)
    filename_time = _extract_time_from_filename(old_filename)
    candidate_time = filename_time if filename_time else _extract_time_from_text(text)
    _, thread_tail = _split_current_text(text)
    return {
        "old_filename": old_filename,
        "source_token": _first_alpha_token(old_filename) or "",
        "candidate_date": _first(r"\d{4}-\d{2}-\d{2}|\d{8}", old_filename),
        "candidate_time": candidate_time,
        "actor_anchor": actor_anchor,
        "actor_anchor_source": actor_anchor_source,
        "actor_values": ",".join(actor_values),
        "thread_reply_detected": str(bool(thread_tail)),
        "actor_evidence_mixed": str(mixed_actor_evidence),
        "text": text,
    }


def _canonicalize_actor(
    raw_actor: str,
    actor_anchor: str | None,
    actor_anchor_source: str,
) -> str:
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

    if not actor_anchor:
        return actor

    if actor_anchor_source in {"sender", "first_person"}:
        if actor_anchor not in {"", actor} and (
            actor in {"principal", "teacher", "parent", "system", "care", "client", "director", "student", "assistant principal"}
            or actor in {"assistant principal", "principal"} and actor_anchor in {"assistant principal", "vice principal", "deputy principal"}
        ):
            return actor_anchor

    if actor_anchor_source == "filename":
        if actor_anchor in {"assistant principal", "vice principal", "deputy principal", "principal", "director", "teacher", "parent", "student"}:
            if actor in {"principal", "assistant principal", "teacher", "parent", "system", "care", "client", "director", "student"}:
                return actor_anchor

    if actor_anchor_source == "body":
        if actor in {"principal"} and actor_anchor in {"assistant principal", "vice principal", "deputy principal"}:
            return actor_anchor

    if actor_anchor_source in {"sender", "first_person", "filename"} and _is_more_specific_role(actor, actor_anchor):
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

    actor_anchor_source = (anchors.get("actor_anchor_source") or "").strip().lower()
    actor_values = anchors.get("actor_values") or ""
    actor_value_count = len([value for value in actor_values.split(",") if value]) if actor_values else 0
    mixed_actor_evidence = str(anchors.get("actor_evidence_mixed")).strip().lower() in {"1", "true", "yes"}
    thread_ambiguous = str(anchors.get("thread_reply_detected")).strip().lower() in {"1", "true", "yes"}
    confidence_value = _confidence_cap_for_actor_evidence(
        confidence_value,
        actor_anchor_source,
        actor_value_count,
        mixed_actor_evidence,
        thread_ambiguous,
    )

    return confidence_value


def normalize_proposal_output(payload: dict[str, Any], item: NormalizedInput) -> dict[str, Any]:
    """Deterministic post-parse normalization for proposal output."""

    anchors = extract_anchors(item.source_path, item.decoded_text)
    output = dict(payload)

    if "actor" in output and isinstance(output.get("actor"), str):
        output["actor"] = _canonicalize_actor(
            output["actor"],
            anchors.get("actor_anchor"),
            (anchors.get("actor_anchor_source") or ""),
        )
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
    provider_timeout: float = 30.0,
) -> tuple[TaskDefinition, TaskDefinition]:
    """Return (first-pass task, review task)."""

    prompt_root = prompt_root or (Path(__file__).resolve().parent / "prompts")
    proposal_prompt = prompt_root / "rename_file_proposal.md"
    review_prompt = prompt_root / "rename_file_review.md"
    provider_config = ProviderConfig(timeout_s=provider_timeout)

    actions = [WriteMarkdownAction(filename="proposal_row.md", content_builder=_proposal_markdown)]
    if manifest_writer is not None:
        actions.append(AppendManifestAction(manifest_writer))

    review_task = TaskDefinition(
        name="rename_file_review",
        description="Review a rename proposal output.",
        prompt_template_path=review_prompt,
        response_model=RenameReviewOutput,
        provider_model=model_name,
        provider_config=provider_config,
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
        provider_config=provider_config,
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
