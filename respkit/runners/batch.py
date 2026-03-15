"""Directory batch runner built on top of single-item execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..inputs import NormalizedInput
from ..utils import list_text_files, read_text_file
from .single import SingleInputRunner, ExecutionResult


@dataclass
class DirectoryBatchRunner:
    """Execute a task over all files in a directory."""

    single_runner: SingleInputRunner

    def run(self, directory: Path) -> list[ExecutionResult]:
        outputs: list[ExecutionResult] = []
        for path in list_text_files(directory):
            text = read_text_file(path)
            item = NormalizedInput(
                source_id=path.as_posix(),
                source_path=path,
                media_type="text/plain",
                decoded_text=text,
            )
            outputs.append(self.single_runner.run(item))
        return outputs
