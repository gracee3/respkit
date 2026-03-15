#!/usr/bin/env python3
"""Generate an evaluation CSV/JSON from existing run artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel

from examples.rename_file_proposal import build_tasks
from respkit.manifest import ManifestWriter
from respkit.providers.openai_compatible import OpenAICompatibleProvider
from respkit.runners import DirectoryBatchRunner, SingleInputRunner


def _load_manifest_rows(manifest_path: Path) -> list[dict[str, Any]]:
    if not manifest_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _normalize_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _summarize_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {}

    summary: dict[str, Any] = {}
    for key, value_item in value.items():
        if isinstance(value_item, (str, int, float, bool)):
            summary[key] = value_item
    return summary


def _to_output_row(row: dict[str, Any]) -> dict[str, str | float]:
    payload: dict[str, Any] = {}
    run_dir = row.get("artifact_dir")
    if isinstance(run_dir, str):
        payload.update(_read_json_object(Path(run_dir) / "validated_response.json"))
    if not payload:
        summary = row.get("validated_output_summary")
        if isinstance(summary, dict):
            payload.update(summary)

    return {
        "source_path": str(row.get("source_path", "")),
        "status": str(row.get("status", "")),
        "kind": str(payload.get("kind", "")),
        "actor": str(payload.get("actor", "")),
        "slug": str(payload.get("slug", "")),
        "confidence": _normalize_confidence(payload.get("confidence")),
    }


def _rows_from_manifest(input_dir: Path, out_root: Path) -> list[dict[str, str | float]]:
    manifest_rows = _load_manifest_rows(out_root / "manifest.jsonl")
    accepted_task = "rename_file_proposal"
    filtered_rows: list[dict[str, Any]] = []
    for row in manifest_rows:
        if not isinstance(row, dict):
            continue
        if row.get("task_name") != accepted_task:
            continue
        source_path = row.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            continue
        try:
            path = Path(source_path)
            input_path = input_dir
            try:
                path = path.resolve()
                input_path = input_path.resolve()
            except OSError:
                pass
            if not (path == input_path or path.is_relative_to(input_path)):
                continue
        except OSError:
            continue
        filtered_rows.append(row)

    return [_to_output_row(row) for row in filtered_rows]


def _manifest_row_from_result(result: Any) -> dict[str, Any]:
    input_obj = getattr(result, "input", None)
    source_path = str(getattr(input_obj, "source_path", ""))
    return {
        "task_name": "rename_file_proposal",
        "source_path": source_path,
        "status": getattr(result, "status", ""),
        "run_id": getattr(result, "run_id", ""),
        "artifact_dir": getattr(result, "artifacts_dir", ""),
        "validated_output_summary": _summarize_payload(getattr(result, "validated_output", None)),
    }


def _run_batch(input_dir: Path, endpoint: str, out_root: Path) -> list[dict[str, str | float]]:
    proposal_task, _ = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")
    manifest_path = out_root / "manifest.jsonl"
    runner = SingleInputRunner(
        task=proposal_task,
        provider=OpenAICompatibleProvider(endpoint=endpoint),
        artifacts_root=out_root,
        manifest_writer=ManifestWriter(manifest_path),
    )
    batch = DirectoryBatchRunner(single_runner=runner, output_root=out_root)
    results = batch.run(input_dir)
    return [_to_output_row(_manifest_row_from_result(result)) for result in results]


def run_corpus(
    input_dir: Path,
    endpoint: str,
    out_root: Path,
    export_path: Path,
    output_format: str,
    rerun: bool = False,
) -> list[dict[str, str | float]]:
    if rerun:
        rows = _run_batch(input_dir=input_dir, endpoint=endpoint, out_root=out_root)
    else:
        rows = _rows_from_manifest(input_dir=input_dir, out_root=out_root)

    export_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "csv":
        with export_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(
                fp,
                fieldnames=["source_path", "status", "kind", "actor", "slug", "confidence"],
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        export_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Corpus export written to {export_path}")
    print(f"Manifest: {out_root / 'manifest.jsonl'}")
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run proposal task over a directory and export key output fields.")
    parser.add_argument("input_dir", help="Directory of text files")
    parser.add_argument("--endpoint", default="http://localhost:8000/v1/responses", help="Responses endpoint")
    parser.add_argument("--out", default=".respkit_corpus", help="Artifacts/output directory")
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="csv",
        help="Export format",
    )
    parser.add_argument(
        "--export",
        default=None,
        help="Output file path (defaults to <out>/corpus_eval.csv or .json)",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run the proposal task before exporting (default reads existing artifacts/manifest).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    export_path = Path(args.export) if args.export else out_root / f"corpus_eval.{args.format}"

    run_corpus(
        input_dir=input_dir,
        endpoint=args.endpoint,
        out_root=out_root,
        export_path=export_path,
        output_format=args.format,
        rerun=args.rerun,
    )


if __name__ == "__main__":
    main()
