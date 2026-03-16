"""CLI for generic ledger operations and interactive resolver."""

from __future__ import annotations

import argparse
from pathlib import Path

from .query import LedgerQuery
from .resolver import DefaultResolverHooks, ResolverSession, load_hook_class
from .store import LedgerStore


def _add_query_args(parser: argparse.ArgumentParser, *, require_task_name: bool = True) -> None:
    parser.add_argument("--task-name", required=require_task_name)
    parser.add_argument("--item-id-prefix")
    parser.add_argument("--item-locator-prefix")
    parser.add_argument("--item-id", action="append")
    parser.add_argument("--unresolved-only", action="store_true", default=False, help="only unresolved rows")
    parser.add_argument("--provider-error-only", action="store_true")
    parser.add_argument("--rejected-only", action="store_true")
    parser.add_argument("--not-approved-only", action="store_true")
    parser.add_argument("--include-approved", action="store_true")
    parser.add_argument("--include-superseded", action="store_true")
    parser.add_argument("--rerun-eligible-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None)


def _build_query_from_args(args: argparse.Namespace) -> LedgerQuery:
    item_ids = args.item_id if args.item_id else None
    unresolved_only = args.unresolved_only
    if args.command == "resolve" and not args.all:
        unresolved_only = True
    if args.command == "resolve" and args.all:
        unresolved_only = False

    if args.resume:
        unresolved_only = True

    return LedgerQuery(
        task_name=args.task_name,
        item_ids=item_ids,
        item_id_prefix=args.item_id_prefix,
        item_locator_prefix=args.item_locator_prefix,
        unresolved_only=unresolved_only,
        provider_error_only=args.provider_error_only,
        rejected_only=args.rejected_only,
        not_approved_only=args.not_approved_only,
        include_approved=args.include_approved,
        include_superseded=args.include_superseded,
        rerun_eligible_only=args.rerun_eligible_only,
        limit=args.limit,
    )


def _build_hook(hook_target: str | None):
    if hook_target is None:
        return DefaultResolverHooks()
    hook_cls = load_hook_class(hook_target)
    try:
        return hook_cls()
    except TypeError:
        return hook_cls


def _run_resolve(args: argparse.Namespace) -> int:
    store = LedgerStore(args.ledger)
    hooks = _build_hook(args.hooks)
    session = ResolverSession(store=store, hooks=hooks)
    results = session.resolve_interactive(
        query=_build_query_from_args(args),
        input_fn=input,
        output_fn=print,
        dry_run=args.dry_run,
        decision_source=args.decision_source,
        decision_actor=args.decision_actor,
    )
    saved = len([item for item in results if item.status == "saved"])
    print(f"saved={saved} processed={len(results)}")
    return 0


def _run_export(args: argparse.Namespace) -> int:
    store = LedgerStore(args.ledger)
    query = _build_query_from_args(args)
    if args.format == "csv":
        store.export_csv(args.out, query=query)
    elif args.format == "jsonl":
        store.export_jsonl(args.out, query=query)
    else:
        store.export_markdown(args.out, query=query)
    print(f"exported={args.out}")
    return 0


def _run_import(args: argparse.Namespace) -> int:
    store = LedgerStore(args.ledger)
    count = store.import_jsonl(args.source)
    print(f"imported={count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic ledger utilities")
    subcommands = parser.add_subparsers(dest="command", required=True)

    resolve = subcommands.add_parser("resolve", help="Run interactive resolver on selected rows")
    resolve.add_argument("--ledger", type=Path, required=True)
    _add_query_args(resolve)
    resolve.add_argument("--resume", action="store_true", help="resume pending rows by default")
    resolve.add_argument("--decision-source", default="human", help="decision source for persisted recommendations")
    resolve.add_argument("--decision-actor", default="cli-user", help="decision actor for persisted recommendations")
    resolve.add_argument("--hooks", help="task hook class path e.g. examples.ledger_hooks:ToyResolverHooks")
    resolve.add_argument("--dry-run", action="store_true", help="preview decisions without writing")
    resolve.set_defaults(func=_run_resolve)

    export = subcommands.add_parser("export", help="Export rows as csv/jsonl/markdown")
    export.add_argument("--ledger", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--format", choices=["csv", "jsonl", "md"], default="csv")
    _add_query_args(export)
    export.set_defaults(func=_run_export)

    resolve.add_argument("--all", action="store_true", help="include all matching rows, including approved")
    import_cmd = subcommands.add_parser("import-jsonl", help="Import JSONL rows into a SQLite ledger")
    import_cmd.add_argument("--ledger", type=Path, required=True)
    import_cmd.add_argument("--source", type=Path, required=True)
    _add_query_args(import_cmd, require_task_name=False)
    import_cmd.set_defaults(func=_run_import)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
