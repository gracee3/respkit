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
        return self.write_json("run_metadata.json", metadata)

    def write_prompt_snapshot(self, template_source: str, rendered_prompt: str) -> None:
        (self.artifact_dir / "prompt_template.md").write_text(template_source, encoding="utf-8")
        self.write_text("prompt.txt", rendered_prompt)

    def write_raw_response(self, response: Mapping[str, Any]) -> None:
        self.write_json("raw_response.json", response)

    def write_provider_request_snapshot(self, request_payload: Mapping[str, Any]) -> None:
        self.write_json("provider_request.json", request_payload)

    def write_parsed_response(self, payload: Mapping[str, Any] | None) -> None:
        if payload is not None:
            self.write_json("parsed_response.json", payload)

    def write_validated_response(self, payload: Mapping[str, Any]) -> None:
        self.write_json("validated_response.json", payload)

    def write_validation_report(self, report: Mapping[str, Any]) -> None:
        self.write_json("validation_report.json", report)

    def write_action_results(self, results: list[dict[str, Any]]) -> None:
        self.write_json("action_results.json", results)

    def manifest_row_path(self) -> Path:
        return self.artifact_dir / "manifest_row.json"

    def write_manifest_row(self, row: Mapping[str, Any]) -> Path:
        return self.write_json("manifest_row.json", row)


@dataclass(frozen=True)
class RunArtifactBundle:
    run_dir: Path
    prompt_file: Path | None = None
    raw_response_file: Path | None = None
    validated_file: Path | None = None
    report_file: Path | None = None
    actions_file: Path | None = None
    metadata_file: Path | None = None
