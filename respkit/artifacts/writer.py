"""Artifact writer for each run/item."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..inputs import NormalizedInput


@dataclass(frozen=True)
class ArtifactPolicy:
    include_provider_request_snapshot: bool = True
    include_prompt_snapshot: bool = True
    include_parsed_response: bool = True
    include_raw_response: bool = True
    include_validated_response: bool = True
    include_validation_report: bool = True
    include_action_results: bool = True
    include_run_metadata: bool = True


@dataclass
class ArtifactWriter:
    """Write structured artifacts into a deterministic per-run directory."""

    artifact_dir: Path

    PROMPT_TEMPLATE_FILE = "prompt_template.md"
    PROMPT_RENDERED_FILE = "prompt.txt"
    PROVIDER_REQUEST_FILE = "provider_request.json"
    RAW_RESPONSE_FILE = "raw_response.json"
    PARSED_RESPONSE_FILE = "parsed_response.json"
    VALIDATED_RESPONSE_FILE = "validated_response.json"
    VALIDATION_REPORT_FILE = "validation_report.json"
    ACTION_RESULTS_FILE = "action_results.json"
    RUN_METADATA_FILE = "run_metadata.json"
    MANIFEST_ROW_FILE = "manifest_row.json"

    def __post_init__(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def write_text(self, filename: str, text: str) -> Path:
        path = self.artifact_dir / filename
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, filename: str, payload: Mapping[str, Any]) -> Path:
        path = self.artifact_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_run_metadata(self, metadata: Mapping[str, Any]) -> Path:
        return self.write_json(self.RUN_METADATA_FILE, metadata)

    def write_prompt_snapshot(self, template_source: str, rendered_prompt: str) -> None:
        (self.artifact_dir / self.PROMPT_TEMPLATE_FILE).write_text(template_source, encoding="utf-8")
        self.write_text(self.PROMPT_RENDERED_FILE, rendered_prompt)

    def write_raw_response(self, response: Mapping[str, Any]) -> None:
        self.write_json(self.RAW_RESPONSE_FILE, response)

    def write_provider_request_snapshot(self, request_payload: Mapping[str, Any]) -> None:
        self.write_json(self.PROVIDER_REQUEST_FILE, request_payload)

    def write_parsed_response(self, payload: Mapping[str, Any] | None) -> None:
        self.write_json(self.PARSED_RESPONSE_FILE, payload or {})

    def write_validated_response(self, payload: Mapping[str, Any] | None) -> None:
        self.write_json(self.VALIDATED_RESPONSE_FILE, {} if payload is None else payload)

    def write_validation_report(self, report: Mapping[str, Any]) -> None:
        self.write_json(self.VALIDATION_REPORT_FILE, report)

    def write_action_results(self, results: list[dict[str, Any]]) -> None:
        self.write_json(self.ACTION_RESULTS_FILE, results)

    def manifest_row_path(self) -> Path:
        return self.artifact_dir / self.MANIFEST_ROW_FILE

    def write_manifest_row(self, row: Mapping[str, Any]) -> Path:
        return self.write_json(self.MANIFEST_ROW_FILE, row)


@dataclass(frozen=True)
class RunArtifactBundle:
    run_dir: Path
    prompt_file: Path | None = None
    raw_response_file: Path | None = None
    validated_file: Path | None = None
    report_file: Path | None = None
    actions_file: Path | None = None
    metadata_file: Path | None = None
