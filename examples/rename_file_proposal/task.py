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
    re.compile(r"(?im)^-{5,}\s*$"),
    re.compile(r"(?im)^On .+wrote:\s*$"),
    re.compile(r"(?im)^>+\s*from:\s+"),
)

_GENERIC_ACTOR_LABELS = {
    "assistant principal",
    "care",
    "client",
    "parent",
    "school official",
    "student",
    "teacher",
    "system",
    "principal",
}

_WEAK_FILENAME_TOKENS = {
    "care",
    "cio",
    "client",
    "plus",
    "re",
    "school",
    "dcps",
    "text",
    "txt",
}
_GENERIC_ACTOR_LABELS.update(_WEAK_FILENAME_TOKENS)

_SLUG_RETAIN_STOPWORDS = {
    "and",
    "for",
    "of",
    "the",
    "to",
    "in",
    "on",
    "at",
    "with",
    "from",
    "re",
}

_THREAD_MESSAGE_START_PATTERN = re.compile(r"(?im)^MESSAGE\s+1\s+OF\s+\d+\s*$")
_THREAD_MESSAGE_NEXT_PATTERN = re.compile(r"(?im)^MESSAGE\s+[2-9]\d*\s+OF\s+\d+\s*$")


_DATE_HEADER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("date_utc", r"(?im)^\s*date_utc\s*:\s*(.+)$"),
    ("date", r"(?im)^\s*date\s*:\s*(.+)$"),
    ("export_utc", r"(?im)^\s*export_utc\s*:\s*(.+)$"),
)


_TIME_HEADER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("time", r"(?im)^\s*time\s*:\s*(.+)$"),
)


_SENDER_PATTERNS = (
    r"(?im)^(?![\t ]*>)(?:from|sender|sent\s+by)\s*:\s*([^\n]+)$",
    r"(?im)^(?![\t ]*> )signature\s*:\s*([^\n]+)$",
)

_QUOTED_SENDER_PATTERNS = (
    r"(?im)^\s*>+\s*(?:from|sender|sent\s+by)\s*:\s*([^\n]+)$",
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

_HEADER_NAME_PATTERNS = (
    r"(?im)^(?![\t ]*>)(?:from|sender|sent\s+by)\s*:\s*(.+)$",
    r"(?im)^\s*(?:to|cc|bcc)\s*:\s*(.+)$",
)

_SPEAKER_LINE_PATTERN = re.compile(r"(?im)^([A-Za-z][A-Za-z'\-. ]+[A-Za-z])\s*:\s*$")

_COMMA_FIRST_NAME_PATTERN = re.compile(r"^([\w'\-. ]+),\s*([\w'\-. ]+)")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


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


def _extract_actor_from_header(raw_sender: str) -> tuple[str | None, str | None]:
    if not raw_sender:
        return None, None

    no_email = re.sub(r"<[^>]+>", " ", raw_sender)
    no_email = re.sub(r"\([^)]*\)", " ", no_email)
    no_email = re.sub(r"\s+", " ", no_email).strip(" \"'<>-")
    if not no_email:
        return None, None

    lowered = no_email.lower()
    if lowered in {"", "n/a", "na", "none"}:
        return None, None

    role = _find_role_in_text(no_email)
    match = _COMMA_FIRST_NAME_PATTERN.match(no_email)
    if match:
        first = _normalize_actor_text(match.group(2))
        last = _normalize_actor_text(match.group(1))
        if first and last:
            return f"{first} {last}", role

    cleaned = re.sub(
        r"^(?:assistant\s+principal|deputy\s+principal|vice\s+principal|assistant\s+principal|assistant\s+pr\.?|assistant\s*manager|principal\s+assistant|principal|director|school\s+counselor|school\s+psychologist|care\s+team|school\s+staff|school\s+official)\s+",
        "",
        no_email,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:mr|mrs|miss|ms|m?x|dr|prof|professor)\.?\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,()]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \"'<>-")
    return _normalize_actor_text(cleaned), role


def _coalesce_name_from_header(raw_sender: str) -> str | None:
    return _extract_actor_from_header(raw_sender)[0]


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
    current_value = specificity_hierarchy.get(current_actor.strip().lower(), 0)
    candidate_value = specificity_hierarchy.get(candidate_actor.strip().lower(), 0)
    if candidate_value > current_value:
        return True
    return len(candidate_actor) > len(current_actor)


def _is_weak_filename_token(token: str) -> bool:
    return (token or "").strip().lower() in _WEAK_FILENAME_TOKENS or len(token or "") <= 3


def _is_generic_actor_label(actor: str) -> bool:
    return (actor or "").strip().lower() in _GENERIC_ACTOR_LABELS


def _is_name_like(actor: str) -> bool:
    words = [word for word in (actor or "").strip().lower().split() if word]
    if len(words) < 2:
        return False
    if any(not word.replace("-", "").replace("'", "").isalpha() for word in words):
        return False
    return set(words) - _GENERIC_ACTOR_LABELS != set()


def _looks_like_name(actor: str) -> bool:
    words = [word for word in (actor or "").strip().lower().split() if word]
    if len(words) < 2:
        return False
    return all(re.fullmatch(r"[a-z'\-]+", word) for word in words)


def _looks_like_actor_role(actor: str) -> bool:
    return _find_role_anchor(actor or "") is not None


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


def _find_sender_lines(text: str, quoted: bool = False) -> list[tuple[str | None, str | None]]:
    patterns = _QUOTED_SENDER_PATTERNS if quoted else _SENDER_PATTERNS
    senders: list[tuple[str | None, str | None]] = []
    for line in text.splitlines():
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                name, role = _extract_actor_from_header(match.group(1))
                senders.append((name, role))
                break
    return senders


def _find_first_person_lines(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        for pattern in _FIRST_PERSON_PATTERNS:
            match = re.search(pattern, line)
            if match:
                actor = _find_role_anchor(match.group(1))
                if actor:
                    values.append(actor)
                break
    return values


def _find_recipient_lines(text: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        for pattern in _RECIPIENT_PATTERNS:
            match = re.search(pattern, line)
            if match:
                actor = _find_role_anchor(match.group(1))
                if actor:
                    values.append(actor)
                break
    return values


def _split_current_text(text: str) -> tuple[str, str]:
    message_start = _THREAD_MESSAGE_START_PATTERN.search(text)
    if message_start:
        post_start = text[message_start.end() :]
        message_next = _THREAD_MESSAGE_NEXT_PATTERN.search(post_start)
        if message_next:
            next_start = message_start.end() + message_next.start()
            return text[message_start.end() : next_start].rstrip(), text[next_start:].lstrip()
        return text[message_start.end() :].lstrip(), ""

    earliest = None
    for pattern in _THREAD_SEPARATOR_PATTERNS:
        match = pattern.search(text)
        if match and (earliest is None or match.start() < earliest):
            earliest = match.start()
    if earliest is None:
        return text, ""
    return text[:earliest].rstrip(), text[earliest:].lstrip()


def _actor_signal_is_concrete(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if normalized in _GENERIC_ACTOR_LABELS:
        return False
    if len(normalized) <= 3:
        return False
    if _is_weak_filename_token(normalized):
        return False
    return True


def _extract_actor_signals(
    path: Path | None,
    text: str,
) -> tuple[str, str, list[str], bool]:
    current_text, _ = _split_current_text(text)
    thread_ambiguous = _split_current_text(text)[1] != ""

    signals = OrderedDict[str, str]()
    actor_values: list[str] = []

    def _add_signal(source: str, value: str | None) -> None:
        if not value:
            return
        normalized = value.strip().lower()
        if not normalized:
            return
        if source in signals:
            return
        signals[source] = normalized
        actor_values.append(normalized)

    source_token = _first_alpha_token(path.name if path is not None else "")
    if source_token:
        _add_signal("filename_token", source_token)

    filename_role = _find_role_in_text(path.name.replace("-", " ").replace("_", " ")) if path is not None else None
    if filename_role:
        _add_signal("filename_role", filename_role)

    sender_lines = _find_sender_lines(current_text)
    for sender_name, sender_role in sender_lines:
        _add_signal("sender_name", sender_name)
        if sender_name and sender_role and sender_role != sender_name:
            _add_signal("sender_role", sender_role)
        break

    for value in _find_first_person_lines(current_text):
        if value:
            _add_signal("first_person", value)
            break

    for value in _find_recipient_lines(current_text):
        if value:
            _add_signal("recipient", value)
            break

    # Parse speaker lines from transcripts, e.g. "Kelly Mastracchio:"
    for line in current_text.splitlines():
        match = _SPEAKER_LINE_PATTERN.match(line)
        if match:
            speaker = _normalize_actor_text(match.group(1))
            if speaker:
                _add_signal("speaker", speaker)
            break

    for value in _find_recipient_lines(text):
        if value and "recipient_body" not in signals:
            _add_signal("recipient_body", value)
            break

    body_actor = _find_role_in_text(current_text)
    if body_actor:
        _add_signal("body", body_actor)

    for quoted_name, quoted_role in _find_sender_lines(current_text, quoted=True):
        if quoted_name:
            _add_signal("sender_quoted_name", quoted_name)
        if quoted_role:
            _add_signal("sender_quoted_role", quoted_role)
        break

    source_token_concrete = source_token and _actor_signal_is_concrete(source_token)

    actor_anchor_source = ""
    actor_anchor = ""

    candidate_sources = (
        ("sender_role", 350),
        ("sender_name", 340),
        ("filename_role", 300),
        ("body", 290),
        ("speaker", 260),
        ("first_person", 230),
        ("filename_token", 180 if source_token_concrete else 90),
        ("sender_quoted_role", 180),
        ("sender_quoted_name", 160),
        ("recipient", 120),
        ("recipient_body", 110),
    )

    for source, _ in sorted(candidate_sources, key=lambda item: item[1], reverse=True):
        if source not in signals:
            continue
        candidate = signals[source]
        if not candidate:
            continue

        if source == "filename_token" and not source_token_concrete:
            continue

        if source in {"recipient", "recipient_body"}:
            if any(strong in signals for strong in ("sender_role", "sender_name", "filename_token", "speaker", "filename_role")):
                continue

        if source == "body":
            if any(strong in signals for strong in ("sender_role", "sender_name", "filename_role")):
                continue

        if source.startswith("sender_quoted") and any(
            strong in signals
            for strong in ("sender_name", "sender_role", "speaker", "filename_token", "filename_role")
        ):
            continue

        if source in {"sender_name", "filename_token"} and _is_generic_actor_label(candidate):
            continue

        actor_anchor_source = source
        actor_anchor = candidate
        break

    if not actor_anchor and signals:
        actor_anchor_source = next(iter(signals.keys()))
        actor_anchor = signals[actor_anchor_source]

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


def _normalize_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _parse_time_value(raw_time: str) -> str | None:
    match = re.match(r"^(\d{1,2}):([0-5]\d)(?::[0-5]\d)?\s*(am|pm)?$", raw_time.strip(), re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    am_pm = (match.group(3) or "").lower()
    if am_pm == "pm" and hour != 12:
        hour += 12
    elif am_pm == "am" and hour == 12:
        hour = 0
    return _normalize_time(hour, minute)


def _parse_header_date(raw_value: str) -> str | None:
    value = raw_value.strip()
    if not value:
        return None

    normalized = re.sub(r"\s+", " ", value)
    iso_match = re.match(
        r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if iso_match:
        year, month, day = iso_match.group(1), iso_match.group(2), iso_match.group(3)
        return f"{year}-{month}-{day}"

    text_match = re.match(
        r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*)?([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})(?:\s+at\s+(.+))?",
        normalized,
        flags=re.IGNORECASE,
    )
    if text_match:
        month_name = text_match.group(1).lower().strip(".")
        day = int(text_match.group(2))
        year = int(text_match.group(3))
        month = _MONTHS.get(month_name[:3], _MONTHS.get(month_name))
        if month is None or not (1 <= day <= 31):
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"

    ymd_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", normalized)
    if ymd_match:
        year, month, day = ymd_match.groups()
        return f"{year}-{month}-{day}"

    return None


def _parse_header_time(raw_value: str) -> str | None:
    value = raw_value.strip()
    match = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)\b", value, flags=re.IGNORECASE)
    if not match:
        return _parse_time_value(value)
    return _parse_time_value(match.group(1))


def _extract_explicit_header_timestamp(text: str) -> tuple[str | None, str | None]:
    current_text, _ = _split_current_text(text)
    candidate_date: str | None = None
    candidate_time: str | None = None

    for label, pattern in _DATE_HEADER_PATTERNS:
        for match in re.finditer(pattern, current_text):
            parsed_date = _parse_header_date(match.group(1))
            if parsed_date:
                candidate_date = parsed_date
                break
        if candidate_date:
            break

    for label, pattern in _TIME_HEADER_PATTERNS:
        for match in re.finditer(pattern, current_text):
            parsed_time = _parse_header_time(match.group(1))
            if parsed_time:
                candidate_time = parsed_time
                break
        if candidate_time:
            break

    return candidate_date, candidate_time


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


def _extract_date_from_filename(filename: str) -> str | None:
    if match := re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", filename):
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if match := re.search(r"(20\d{2})(\d{2})(\d{2})", filename):
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def _select_explicit_timestamp(
    filename_date: str | None, filename_time: str | None, header_date: str | None, header_time: str | None
) -> tuple[str | None, str | None]:
    if header_date:
        if filename_date is None or header_date != filename_date:
            filename_date = header_date
    if header_time:
        if filename_time is None or header_time != filename_time:
            filename_time = header_time
    return filename_date, filename_time


def _extract_filename_slug_tokens(filename: str) -> list[str]:
    raw_tokens = re.split(r"[^a-z0-9]+", filename.lower())
    ordered: list[str] = []
    for token in raw_tokens:
        if len(token) < 2:
            continue
        if _is_weak_filename_token(token) or token in _SLUG_RETAIN_STOPWORDS:
            continue
        if token not in ordered:
            ordered.append(token)
    return ordered


def _enhance_slug(slug: str | None, source_filename: str, candidate_date: str | None) -> str:
    normalized_slug = _normalize_actor_text(slug or "") or ""
    if not normalized_slug:
        return ""
    slug_tokens = [token for token in normalized_slug.split("-") if token]
    if len(slug_tokens) >= 4:
        return "-".join(slug_tokens)

    for token in _extract_filename_slug_tokens(source_filename):
        if len(slug_tokens) >= 4:
            break
        if token not in slug_tokens:
            slug_tokens.append(token)

    if candidate_date and candidate_date.replace("-", "") not in slug_tokens and len(slug_tokens) < 5:
        slug_tokens.append(candidate_date.replace("-", ""))

    return "-".join(slug_tokens[:5])


def _confidence_cap_for_actor_evidence(
    confidence: float,
    actor_anchor_source: str,
    actor_evidence_count: int,
    mixed_actor_evidence: bool,
    thread_ambiguous: bool,
) -> float:
    cap = 1.0
    if actor_anchor_source in {"recipient", "recipient_body", "body"}:
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
    filename_date = _extract_date_from_filename(old_filename)
    filename_time = _extract_time_from_filename(old_filename)
    header_date, header_time = _extract_explicit_header_timestamp(text)
    candidate_date, candidate_time = _select_explicit_timestamp(filename_date, filename_time, header_date, header_time)
    if candidate_time is None:
        candidate_time = _extract_time_from_text(text)
    _, thread_tail = _split_current_text(text)
    return {
        "old_filename": old_filename,
        "source_token": _first_alpha_token(old_filename) or "",
        "candidate_date": candidate_date,
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

    actor_sources_with_strength = {
        "sender_name",
        "sender_role",
        "filename_role",
        "filename_token",
        "speaker",
        "first_person",
        "recipient",
        "recipient_body",
        "body",
    }
    source_strength = actor_anchor_source in actor_sources_with_strength

    if source_strength and _actor_signal_is_concrete(actor_anchor):
        if actor in {"principal", "teacher", "parent", "director", "student", "care", "client", "system", "assistant principal"}:
            return actor_anchor
        if actor in {"assistant principal", "principal"} and actor_anchor not in {"care", "client", "system", "teacher"}:
            return actor_anchor
        if actor_anchor_source in {"sender_name", "sender_role", "speaker"} and _looks_like_name(actor_anchor):
            return actor_anchor

    if source_strength and actor in _GENERIC_ACTOR_LABELS and actor != actor_anchor:
        if actor_anchor_source in {"sender_name", "sender_role", "filename_role", "filename_token", "speaker", "first_person"}:
            return actor_anchor

    if source_strength and _is_more_specific_role(actor, actor_anchor or ""):
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
