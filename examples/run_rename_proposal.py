"""Minimal example execution for v1 SDK tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from respkit.artifacts import ArtifactWriter
from respkit.manifest import ManifestWriter
from respkit.providers.openai_compatible import OpenAICompatibleProvider
from respkit.runners import SingleInputRunner, DirectoryBatchRunner, ReviewRunner
from respkit.utils.filesystem import read_text_file
from respkit.inputs import NormalizedInput
from examples.rename_file_proposal import build_tasks


def _build_runner(endpoint: str, output_dir: Path, task_config, manifest_path: Path) -> SingleInputRunner:
    provider = OpenAICompatibleProvider(endpoint=endpoint, api_key=None)
    manifest_writer = ManifestWriter(manifest_path)
    return SingleInputRunner(
        task=task_config,
        provider=provider,
        artifacts_root=output_dir,
        manifest_writer=manifest_writer,
    )


def _annotate_review_not_run(first_result, reason: str) -> None:
    artifacts_dir = Path(first_result.artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    review_status_path = artifacts_dir / "review_status.json"
    review_status_payload = {"review_status": "not_run", "reason": reason, "first_run_id": first_result.run_id}
    review_status_path.write_text(json.dumps(review_status_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    run_metadata_path = artifacts_dir / ArtifactWriter.RUN_METADATA_FILE
    if run_metadata_path.exists():
        existing = json.loads(run_metadata_path.read_text(encoding="utf-8"))
    else:
        existing = {}
    existing["review_status"] = "not_run"
    existing["review_status_reason"] = reason
    run_metadata_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def run_single(input_path: Path, endpoint: str, output_dir: Path, with_review: bool) -> None:
    proposal_task, review_task = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")
    manifest_path = output_dir / "manifest.jsonl"
    runner = _build_runner(endpoint, output_dir, proposal_task, manifest_path)
    normalized = NormalizedInput(
        source_id=str(input_path),
        source_path=input_path,
        media_type="text/plain",
        decoded_text=read_text_file(input_path),
    )
    first = runner.run(normalized)

    if with_review:
        if first.status == "success":
            reviewer = _build_runner(endpoint, output_dir, review_task, manifest_path)
            review_result = ReviewRunner().run(first, normalized, proposal_task.review_policy, reviewer)
            print(f"Single run review status: {review_result.status}")
        else:
            reason = f"first-pass status was {first.status}; review requires success"
            _annotate_review_not_run(first, reason)
            print(f"Single run review status: not_run ({reason})")
    print(f"Single run status: {first.status}, artifacts: {first.artifacts_dir}")


def run_batch(directory: Path, endpoint: str, output_dir: Path, with_review: bool, *, max_concurrency: int = 1) -> None:
    proposal_task, review_task = build_tasks(manifest_writer=None, model_name="gpt-oss-20b")
    manifest_path = output_dir / "manifest.jsonl"
    first_runner = _build_runner(endpoint, output_dir, proposal_task, manifest_path)
    batch = DirectoryBatchRunner(single_runner=first_runner, max_concurrency=max_concurrency)
    first_results = batch.run(directory)

    if with_review:
        files = sorted(p for p in directory.iterdir() if p.is_file())
        reviewer = _build_runner(endpoint, output_dir, review_task, manifest_path)
        for original, first_result in zip(files, first_results):
            review_input = NormalizedInput(
                source_id=str(original),
                source_path=original,
                media_type="text/plain",
                decoded_text=read_text_file(original),
            )
            if first_result.status != "success":
                reason = f"first-pass status was {first_result.status}; review requires success"
                _annotate_review_not_run(first_result, reason)
                print(f"review status for {original.name}: not_run ({reason})")
                continue
            review_run = ReviewRunner().run(
                first_result=first_result,
                original_item=review_input,
                policy=proposal_task.review_policy,
                single_runner=reviewer,
            )
            print(f"review status for {original.name}: {review_run.status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rename proposal example")
    parser.add_argument("mode", choices=("single", "batch"), help="single file or directory batch")
    parser.add_argument("path", help="Input file or directory")
    parser.add_argument("--endpoint", default="http://localhost:8000/v1/responses", help="OpenAI-compatible Responses endpoint")
    parser.add_argument("--out", default=".respkit_examples", help="Artifact root")
    parser.add_argument("--max-concurrency", type=int, default=1, help="Max concurrent file runs in batch mode")
    parser.add_argument("--review", action="store_true", help="Run optional review pass")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.path)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "single":
        run_single(path, args.endpoint, output_dir, args.review)
    else:
        run_batch(path, args.endpoint, output_dir, args.review, max_concurrency=args.max_concurrency)


if __name__ == "__main__":
    main()
