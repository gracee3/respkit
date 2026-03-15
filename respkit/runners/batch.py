"""Directory batch runner built on top of single-item execution."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..inputs import NormalizedInput
from ..utils import list_text_files, read_text_file
from .single import SingleInputRunner, ExecutionResult


@dataclass
class DirectoryBatchRunner:
    """Execute a task over all files in a directory."""

    single_runner: SingleInputRunner
    output_root: Path | None = None
    summary_filename: str = "batch_summary.json"

    def run(self, directory: Path) -> list[ExecutionResult]:
        outputs: list[ExecutionResult] = []
        summary = Counter[str]()
        for path in list_text_files(directory):
            text = read_text_file(path)
            item = NormalizedInput(
                source_id=path.as_posix(),
                source_path=path,
                media_type="text/plain",
                decoded_text=text,
            )
            result = self.single_runner.run(item)
            outputs.append(result)
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
