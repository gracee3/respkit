# respkit

`respkit` is a small reusable Python SDK for structured LLM tasks over normalized text input.

## Install

```bash
python3 -m pip install respkit
```

For local development:

```bash
git clone https://github.com/gracee3/respkit.git
cd respkit
python3 -m pip install -e .[dev]
```

## Quick Start

```python
from pathlib import Path

from respkit.inputs import NormalizedInput
from respkit.providers import OpenAICompatibleProvider
from respkit.runners import SingleInputRunner

from examples.demo_rename_proposal.task import build_tasks


proposal_task, _review_task = build_tasks()
runner = SingleInputRunner(
    task=proposal_task,
    provider=OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses"),
    artifacts_root=Path(".respkit_demo"),
)

item = NormalizedInput(
    source_id="sample.txt",
    source_path=Path("sample.txt"),
    media_type="text/plain",
    decoded_text=Path("sample.txt").read_text(encoding="utf-8"),
)

result = runner.run(item)
print(result.status, result.validated_output)
```

`result` contains normalized status, structured output, validation report,
and artifact directory path. Use `ReviewRunner` when you need a second-pass
validator.

## What the SDK contains

- `respkit/` — reusable core:
  - providers (`openai_compatible`, base contract)
  - runners (`single`, `batch`, `review`)
  - tasks/contracts
  - validators and normalization
  - actions (markdown/json/manifest)
  - artifacts + manifest writers
- `examples/` — safe synthetic examples only
- `tests/` — SDK tests with synthetic fixtures

## Supported shape

Every task uses:

- normalized input (`source_id`, `source_path`, `decoded_text`)
- prompt template + renderer
- schema validation
- deterministic action execution
- optional review pass

The core execution path remains generic and reusable:

- prompt rendering
- provider call
- response parsing + validation
- artifact capture
- manifest append
- optional review runner with optional concurrency

## Synthetic developer example

The public example is intentionally synthetic:

- `examples/demo_rename_proposal/`
  - `task.py` — task wiring and prompt/context builders
  - `schemas.py` — proposal/review output models
  - `prompts/` — synthetic prompts
  - `__main__.py` — CLI entrypoint

This example uses only synthetic names/entities and works without corpus-specific data.

For a generic SDK-level example of the new corpus adjudication ledger abstraction, use:

```bash
python examples/demo_ledger.py
```

## Run the example

```bash
respkit-demo single /path/to/file.txt \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --provider-timeout 30
  # or: python -m examples.demo_rename_proposal single /path/to/file.txt ...

respkit-demo batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 4 \
  --review
```

You can run the review pass concurrently with:

```bash
respkit-demo batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 8 \
  --review --review-max-concurrency 4 \
  --provider-timeout 30
  # or: python -m examples.demo_rename_proposal batch ...
```

Available flags:

- `--max-concurrency`: proposal batch parallelism
- `--review-max-concurrency`: review concurrency (default `1`)
- `--provider-timeout`: request timeout seconds
- `--review`: enable optional review pass

## Smoke scripts

```bash
make smoke-single   # single fixture
make smoke-batch    # batch fixtures
make smoke          # runs both
```

Env vars used by smoke targets:

- `SMOKE_ENDPOINT` (default `http://localhost:8000/v1/responses`)
- `SMOKE_MAX_CONCURRENCY` (default `1`)
- `SMOKE_REVIEW_MAX_CONCURRENCY` (default `1`)
- `SMOKE_PROVIDER_TIMEOUT` (default `30`)
- `SMOKE_REVIEW` (set to any non-empty value to enable review)

`scripts/smoke_single.sh` and `scripts/smoke_batch.sh` call the synthetic example by default and can be used outside `make`.

## Status vocabulary

Status values are consistent across runners and manifest rows:

- `success`
- `preflight_model_not_found`
- `provider_error`
- `parse_error`
- `validation_failed`
- `action_failed`
- `review_failed`

`parse_error` means the provider output could not be parsed into a JSON payload.

## Artifact output

Each task run writes per-item artifacts under:

```
.respkit_demo/
  artifacts/
    <task_name>/<run_id>/
      prompt_template.md
      prompt.txt
      provider_request.json
      raw_response.json
      parsed_response.json
      validation_report.json
      validated_response.json
      action_results.json
      run_metadata.json
      manifest_row.json (if manifest action is configured)
```

The run metadata includes provider timing, status, and chosen model.

`manifest.jsonl` is append-only and one row is written per manifest action invocation.

## Ledger API (SDK)

`respkit` ships a task-agnostic adjudication ledger for tasks that iterate over many items and need proposal/review/human/apply coordination.

- module: `respkit.ledger`
- canonical storage: **SQLite** (`LedgerStore`)
- machine/human state is split:
  - machine: `not_run`, `proposed`, `reviewed`, `provider_error`, `apply_ready`, `applied`, `superseded`
  - human: `needs_review`, `approved`, `rejected`
- task payloads are stored in JSON columns (`proposal_payload`, `review_payload`, `apply_payload`, `human_decision_payload`) plus `extras`
- per-stage provenance and run identifiers are captured
- optional apply hooks with optional clean-tree guard are supported

SQLite stores a current-state table and an event/history table so stage transitions are auditable and never overwrite history.

`applied_in_commit` is separate from `apply_code_commit`:
- `apply_code_commit`: code hash at the time apply callback is invoked (or before mutation for non-dry-run apply).
- `applied_in_commit`: commit that eventually captures mutation output if your workflow commits afterward (optional and often unavailable immediately).

### Core API

- `LedgerStore(ledger_path)` — create/open a SQLite ledger
- `create_or_update_row(...)`
- `record_proposal(...)`
- `record_review(...)`
- `record_human_decision(...)`
- `record_apply(...)`
- `mark_superseded(...)`
- `query_rows(LedgerQuery(...))`
- `run_apply(...)`
- exports:
  - `export_csv(path, query=...)`
  - `export_jsonl(path, query=...)`
  - `export_markdown(path, query=...)`
- `import_jsonl(source_jsonl)` for one-time migration to SQLite

### Query examples

- `LedgerQuery(task_name=task_name, unresolved_only=True)`
- `LedgerQuery(task_name=task_name, provider_error_only=True)`
- `LedgerQuery(task_name=task_name, rejected_only=True)`
- `LedgerQuery(task_name=task_name, not_approved_only=True)`
- `LedgerQuery(task_name=task_name, unresolved_only=True, rerun_eligible_only=True)`
- include/exclude controls:
  - `LedgerQuery(include_approved=False)`
  - `LedgerQuery(include_superseded=True)`

### Generic usage

```python
from pathlib import Path

from respkit.ledger import (
    ApplyPolicy,
    HumanDecision,
    LedgerQuery,
    LedgerStore,
)

ledger = LedgerStore(Path(".my_ledger.sqlite"))
task_name = "generic-corpus-task"

ledger.record_proposal(
    task_name=task_name,
    item_id="item-001",
    item_locator="docs/file-a.txt",
    proposal_payload={"op": "normalize_section_headers"},
    proposal_result={"status": "ok"},
)
ledger.record_review(
    task_name=task_name,
    item_id="item-001",
    review_payload={"risk": "low"},
    review_result={"accept": True},
)
ledger.record_human_decision(task_name=task_name, item_id="item-001", decision=HumanDecision.APPROVED)

ready = ledger.query_rows(LedgerQuery(task_name=task_name, unresolved_only=True, include_approved=False))
print([r.item_id for r in ready])

ledger.run_apply(
    query=LedgerQuery(task_name=task_name),
    callback=lambda _row, dry_run: (
        {"op": "apply"} if dry_run else {"op": "apply"},
        {"status": "ok"},
    ),
    dry_run=True,
)

ledger.run_apply(
    query=LedgerQuery(task_name=task_name),
    callback=lambda _row, dry_run: ({"op": "apply"}, {"status": "applied"}),
    dry_run=False,
    policy=ApplyPolicy(require_clean_working_tree=True, working_directory=Path(".")),
)
```

### Resolver example (interactive + hook extension)

The SDK also provides a generic interactive resolver with task-specific hooks:

```python
from pathlib import Path

from respkit.ledger import DefaultResolverHooks, LedgerQuery, LedgerResolver, LedgerStore


class MyHooks(DefaultResolverHooks):
    def risk_flags(self, row):
        if row.review_payload and isinstance(row.review_payload, dict) and row.review_payload.get("risk") == "high":
            return ["high risk"]
        return []


ledger = LedgerStore(Path(".my_ledger.sqlite"))
resolver = LedgerResolver(ledger=ledger, hooks=MyHooks(), input_fn=lambda prompt: "a")
resolver.resolve(
    query=LedgerQuery(task_name="generic-corpus-task", unresolved_only=True, include_approved=False),
    dry_run=True,
)
```

### Resolver and Export CLI

Installable script:

```bash
respkit-ledger resolve --ledger .my_ledger.sqlite --task-name generic-corpus-task --unresolved-only
respkit-ledger export --ledger .my_ledger.sqlite --task-name generic-corpus-task --format markdown --out review.md
respkit-ledger import-jsonl --ledger .my_ledger.sqlite --source /tmp/old_ledger.jsonl
```

### Demo command

Run the generic ledger demos:

```bash
PYTHONPATH=. python3 examples/demo_ledger.py
PYTHONPATH=. python3 examples/demo_ledger_resolver.py
```

Target explicit paths:

```bash
PYTHONPATH=. python3 examples/demo_ledger.py --repo /tmp/corpus_repo --ledger /tmp/corpus_ledger.sqlite
```

## Local test fixtures

`tests/fixtures/rename_inputs/*.txt` contains synthetic, non-sensitive material for local runs.

## Notes

This repository intentionally does not bundle real corpus data or private task iterations.
Those should live in a private task/corpus repo.
