"""Run the synthetic demo rename task from the package entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from respkit.artifacts import ArtifactWriter
from respkit.inputs import NormalizedInput
from respkit.manifest import ManifestWriter
from respkit.runners import DirectoryBatchRunner, ReviewRunner, SingleInputRunner
from respkit.utils import list_text_files, read_text_file
from respkit.validators import TrimWhitespaceValidator

from .task import build_tasks


async def _run_review_item(path: Path, first_result, reviewer, policy, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        if first_result.status != "success":
            return f"{path.name}:not_run"

        input_item = NormalizedInput(
            source_id=path.as_posix(),
            source_path=path,
            media_type="text/plain",
            decoded_text=read_text_file(path),
        )
        review_result = await asyncio.to_thread(
            ReviewRunner().run,
            first_result=first_result,
            original_item=input_item,
            policy=policy,
            single_runner=reviewer,
        )
        return f"{path.name}:{review_result.status}"


def _build_runner(endpoint: str, out_root: Path, task, manifest: Path) -> SingleInputRunner:
    from respkit.providers.openai_compatible import OpenAICompatibleProvider

    return SingleInputRunner(
        task=task,
        provider=OpenAICompatibleProvider(endpoint=endpoint),
        artifacts_root=out_root,
        manifest_writer=ManifestWriter(manifest),
    )


def run_single(
    path: Path,
    endpoint: str,
    out: Path,
    with_review: bool,
    provider_timeout: float = 30.0,
) -> None:
    proposal_task, review_task = build_tasks(provider_timeout=provider_timeout)
    manifest_path = out / "manifest.jsonl"
    runner = _build_runner(endpoint, out, proposal_task, manifest_path)

    item = NormalizedInput(
        source_id=path.as_posix(),
        source_path=path,
        media_type="text/plain",
        decoded_text=read_text_file(path),
    )

    first = runner.run(item)
    print(f"single status: {first.status}")

    if with_review:
        reviewer = _build_runner(endpoint, out, review_task, manifest_path)
        review = ReviewRunner().run(first, item, proposal_task.review_policy, reviewer)
        print(f"review status: {review.status}")


def run_batch(directory: Path, endpoint: str, out: Path, with_review: bool, max_concurrency: int = 1,
              review_max_concurrency: int = 1, provider_timeout: float = 30.0) -> None:
    proposal_task, review_task = build_tasks(provider_timeout=provider_timeout)
    manifest_path = out / "manifest.jsonl"
    first_runner = _build_runner(endpoint, out, proposal_task, manifest_path)
    first_results = DirectoryBatchRunner(single_runner=first_runner, max_concurrency=max_concurrency).run(directory)
    print(f"proposal status count: {[r.status for r in first_results]}")

    if not with_review:
        return

    by_source = {result.source_id: result for result in first_results}
    reviewer = _build_runner(endpoint, out, review_task, manifest_path)

    async def _run() -> None:
        sem = asyncio.Semaphore(max(1, review_max_concurrency))
        tasks = [
            _run_review_item(path, by_source[path.as_posix()], reviewer, proposal_task.review_policy, sem)
            for path in list_text_files(directory)
            if path.as_posix() in by_source
        ]
        statuses = await asyncio.gather(*tasks)
        for status in statuses:
            print(status)

    asyncio.run(_run())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic demo rename proposal task")
    parser.add_argument("mode", choices=("single", "batch"))
    parser.add_argument("path")
    parser.add_argument("--endpoint", default="http://localhost:8000/v1/responses")
    parser.add_argument("--out", default=".respkit_demo")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--review-max-concurrency", type=int, default=1)
    parser.add_argument("--provider-timeout", type=float, default=30.0)
    parser.add_argument("--review", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.path)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.mode == "single":
        run_single(path, args.endpoint, out, args.review, provider_timeout=args.provider_timeout)
    else:
        run_batch(
            directory=path,
            endpoint=args.endpoint,
            out=out,
            with_review=args.review,
            max_concurrency=args.max_concurrency,
            review_max_concurrency=args.review_max_concurrency,
            provider_timeout=args.provider_timeout,
        )


if __name__ == "__main__":
    main()
