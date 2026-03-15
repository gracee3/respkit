"""Directory batch runner built on top of single-item execution."""

from __future__ import annotations

import asyncio
from collections import Counter
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..inputs import NormalizedInput
from ..utils import list_text_files, read_text_file
from .single import SingleInputRunner, ExecutionResult


@dataclass
class DirectoryBatchRunner:
    """Execute a task over all files in a directory."""

    single_runner: SingleInputRunner
    output_root: Path | None = None
    summary_filename: str = "batch_summary.json"
    max_concurrency: int = 1

    async def _run_single(self, item: NormalizedInput, semaphore: asyncio.Semaphore) -> "ExecutionResult":
        async with semaphore:
            return await asyncio.to_thread(self.single_runner.run, item)

    async def _run_concurrently(self, items: Sequence[NormalizedInput]) -> list["ExecutionResult"]:
        concurrency = max(1, self.max_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [asyncio.create_task(self._run_single(item, semaphore)) for item in items]
        return list(await asyncio.gather(*tasks))

    def run(self, directory: Path) -> list[ExecutionResult]:
        outputs: list[ExecutionResult] = []
        summary = Counter[str]()
        files = list_text_files(directory)
        items = []
        for path in files:
            text = read_text_file(path)
            items.append(
                NormalizedInput(
                source_id=path.as_posix(),
                source_path=path,
                media_type="text/plain",
                decoded_text=text,
            )
            )
        if self.max_concurrency > 1:
            outputs = asyncio.run(self._run_concurrently(items))
        else:
            for item in items:
                outputs.append(self.single_runner.run(item))

        for result in outputs:
            summary[result.status] += 1

        batch_summary = {
            "total": len(outputs),
            "status_counts": dict(summary),
            "statuses": [result.status for result in outputs],
        }
        output_root = self.output_root or self.single_runner.artifacts_root
        output_root.mkdir(parents=True, exist_ok=True)
        summary_path = output_root / self.summary_filename
        summary_path.write_text(json.dumps(batch_summary, ensure_ascii=False, indent=2), encoding="utf-8")

        status_parts = [f"{status}={count}" for status, count in sorted(summary.items())]
        print(f"Batch run complete: total={len(outputs)} " + ", ".join(status_parts))

        return outputs
