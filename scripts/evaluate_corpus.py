#!/usr/bin/env python3
"""Generate a simple corpus review artifact from a directory of input files."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from examples.rename_file_proposal import build_tasks
from respkit.manifest import ManifestWriter
from respkit.providers.openai_compatible import OpenAICompatibleProvider
from respkit.runners import DirectoryBatchRunner, SingleInputRunner


def _to_output_row(result) -> dict[str, str | float]:
    payload = {}
    output = result.validated_output
    if isinstance(output, dict):
        payload = output
    elif output is not None and hasattr(output, "model_dump"):
        payload = output.model_dump()

    return {
        "source_path": str(result.input.source_path),
        "status": result.status,
        "kind": str(payload.get("kind", "")),
        "actor": str(payload.get("actor", "")),
        "slug": str(payload.get("slug", "")),
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
    }


def run_corpus(
    input_dir: Path,
    endpoint: str,
    out_root: Path,
    export_path: Path,
    output_format: str,
) -> None:
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

    rows = [_to_output_row(result) for result in results]
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
    print(f"Manifest: {manifest_path}")


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    if args.export:
        export_path = Path(args.export)
    else:
        export_path = out_root / f"corpus_eval.{args.format}"

    run_corpus(input_dir=input_dir, endpoint=args.endpoint, out_root=out_root, export_path=export_path, output_format=args.format)


if __name__ == "__main__":
    main()
