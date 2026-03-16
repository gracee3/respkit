"""Protocol dispatch and business operations for the ledger service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..ledger import HumanDecision, LedgerQuery, LedgerStore, ResolverAction, ResolverSession
from ..ledger.git import get_head_commit
from ..ledger.models import LedgerRow
from .adapters import ActionDescriptor, DefaultTaskServiceAdapter, TaskServiceAdapter
from ..version import __version__


@dataclass(frozen=True)
class ServiceError(Exception):
    """Error envelope for JSON-RPC responses."""

    code: int
    message: str
    data: Any = None


_JSONRPC_VERSION = "2.0"
_DEFAULT_DECISION_SOURCE = "agent"
_DEFAULT_DECISION_ACTOR = "service"
_BUILTIN_ACTIONS = {action.value for action in ResolverAction}


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_str(value: Any, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        return value if value else default
    return str(value)


def _row_view_payload(row: LedgerRow, adapter: TaskServiceAdapter) -> dict[str, Any]:
    rendered_summary = adapter.render_summary(row)
    payload = row.to_dict()
    human_payload = payload.get("human_decision_payload")
    approved_output = None
    if isinstance(human_payload, dict):
        approved_output = human_payload.get("approved_output")

    return {
        "task_name": row.task_name,
        "item_id": row.item_id,
        "item_locator": row.item_locator,
        "machine_status": row.machine_status.value,
        "human_status": row.human_status.value,
        "rerun_eligible": row.rerun_eligible,
        "proposal_payload": row.proposal_payload,
        "review_payload": row.review_payload,
        "apply_payload": row.apply_payload,
        "human_decision_payload": row.human_decision_payload,
        "extras": row.extras,
        "risk_flags": adapter.risk_flags(row),
        "categories": adapter.row_categories(row),
        "human_notes": row.human_notes,
        "decision_source": row.human_decision_source,
        "decision_actor": row.human_decision_actor,
        "decision_note": row.human_notes,
        "decision_metadata": row.human_decision_metadata,
        "rendered_summary": rendered_summary,
        "approved_output": approved_output,
    }


class LedgerService:
    """Small service facade for ledger operations."""

    def __init__(
        self,
        ledger_path: Path,
        adapters: list[TaskServiceAdapter] | None = None,
        default_decision_source: str = _DEFAULT_DECISION_SOURCE,
        default_decision_actor: str = _DEFAULT_DECISION_ACTOR,
    ) -> None:
        self.store = LedgerStore(ledger_path)
        self.adapters = adapters or []
        self.default_decision_source = default_decision_source
        self.default_decision_actor = default_decision_actor

        self._adapter_defaults = [adapter for adapter in self.adapters if isinstance(adapter, TaskServiceAdapter)]
        self._default_adapter = DefaultTaskServiceAdapter()

    def close(self) -> None:
        self.store.close()

    def _adapter_for_task(self, task_name: str) -> TaskServiceAdapter:
        for adapter in self._adapter_defaults:
            if adapter.supports_task(task_name):
                return adapter
        return self._default_adapter

    def _query_from_params(self, params: dict[str, Any] | None) -> LedgerQuery:
        params = params or {}
        task_name = params.get("task_name")
        item_ids = params.get("item_ids")
        if isinstance(item_ids, str):
            item_ids = [item_id.strip() for item_id in item_ids.split(",") if item_id.strip()]
        limit = params.get("limit")
        if isinstance(limit, str) and limit.strip():
            try:
                limit = int(limit)
            except ValueError as exc:
                raise ServiceError(-32602, "limit must be an integer") from exc
        offset = params.get("offset")
        if isinstance(offset, str) and offset.strip():
            try:
                offset = int(offset)
            except ValueError as exc:
                raise ServiceError(-32602, "offset must be an integer") from exc
        elif offset is not None and not isinstance(offset, int):
            raise ServiceError(-32602, "offset must be an integer")

        return LedgerQuery(
            task_name=task_name,
            item_ids=list(item_ids) if item_ids is not None else None,
            item_id_prefix=params.get("item_id_prefix"),
            item_locator=params.get("item_locator"),
            item_locator_prefix=params.get("item_locator_prefix"),
            unresolved_only=_coerce_bool(params.get("unresolved_only"), False),
            provider_error_only=_coerce_bool(params.get("provider_error_only"), False),
            rejected_only=_coerce_bool(params.get("rejected_only"), False),
            not_approved_only=_coerce_bool(params.get("not_approved_only"), False),
            include_approved=_coerce_bool(params.get("include_approved"), True),
            include_superseded=_coerce_bool(params.get("include_superseded"), False),
            rerun_eligible_only=_coerce_bool(params.get("rerun_eligible_only"), False),
            limit=limit,
            offset=offset,
        )

    def _build_session(self, task_name: str) -> ResolverSession:
        adapter = self._adapter_for_task(task_name)
        return ResolverSession(store=self.store, hooks=adapter, decision_source=self.default_decision_source, decision_actor=self.default_decision_actor)

    def info(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        rows = self.store.query_rows()
        return {
            "schema_version": "1.0",
            "ledger_path": str(self.store.ledger_path),
            "row_count": len(rows),
            "task_count": len({row.task_name for row in rows}),
            "service_version": __version__,
        }

    def summary(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = self._query_from_params(params or {})
        rows = self.store.query_rows(query)
        totals: dict[str, int] = {
            "total": len(rows),
            "approved": 0,
            "needs_review": 0,
            "rejected": 0,
            "provider_error": 0,
            "not_run": 0,
            "reviewed": 0,
            "apply_ready": 0,
            "applied": 0,
            "superseded": 0,
            "unresolved": 0,
        }

        for row in rows:
            if row.human_status.value == HumanDecision.APPROVED.value:
                totals["approved"] += 1
            elif row.human_status.value == HumanDecision.REJECTED.value:
                totals["rejected"] += 1
            elif row.human_status.value == HumanDecision.NEEDS_REVIEW.value:
                totals["needs_review"] += 1

            status_name = row.machine_status.value
            if status_name in totals:
                totals[status_name] += 1
            if row.is_unresolved():
                totals["unresolved"] += 1

        by_task: dict[str, Any] = {}
        for row in rows:
            task_bucket = by_task.setdefault(row.task_name, {"total": 0, "approved": 0, "rejected": 0, "needs_review": 0})
            task_bucket["total"] += 1
            if row.human_status == HumanDecision.APPROVED:
                task_bucket["approved"] += 1
            elif row.human_status == HumanDecision.REJECTED:
                task_bucket["rejected"] += 1
            else:
                task_bucket["needs_review"] += 1
        return {"counts": totals, "by_task": by_task}

    def tasks(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        rows = self.store.query_rows()
        task_counts: dict[str, int] = {}
        for row in rows:
            task_counts[row.task_name] = task_counts.get(row.task_name, 0) + 1
        return {
            "task_names": sorted(task_counts),
            "rows_by_task": task_counts,
            "registered_adapters": [adapter.__class__.__name__ for adapter in self.adapters],
        }

    def list_rows(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = params or {}
        query = self._query_from_params(payload.get("query", payload))
        with_view = _coerce_bool(payload.get("with_view"), True)
        rows = self.store.query_rows(query)
        result_rows: list[dict[str, Any]] = []
        for row in rows:
            if not with_view:
                result_rows.append(row.to_dict())
                continue

            adapter = self._adapter_for_task(row.task_name)
            result_rows.append(_row_view_payload(row, adapter))
        return {"rows": result_rows, "count": len(result_rows)}

    def get_row(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            raise ServiceError(-32602, "params required")
        task_name = params.get("task_name")
        item_id = params.get("item_id")
        if not task_name or not item_id:
            raise ServiceError(-32602, "task_name and item_id required")

        row = self.store.get_row(task_name, item_id)
        if row is None:
            raise ServiceError(-32602, f"row {task_name}:{item_id} not found")
        adapter = self._adapter_for_task(task_name)
        with_view = _coerce_bool(params.get("with_view"), True)
        payload = _row_view_payload(row, adapter) if with_view else row.to_dict()
        return {"row": payload}

    def get_row_history(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            raise ServiceError(-32602, "params required")
        task_name = params.get("task_name")
        item_id = params.get("item_id")
        if not task_name or not item_id:
            raise ServiceError(-32602, "task_name and item_id required")

        events = self.store.get_row_events(task_name=task_name, item_id=item_id)
        payload_rows = [
            {
                "version": event["version"],
                "event_type": event["event_type"],
                "machine_status": event["machine_status"],
                "human_status": event["human_status"],
                "event_at": event["event_at"],
                "payload": event["payload"],
            }
            for event in events
        ]
        return {"events": payload_rows}

    def preview_row(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        row_view = self._required_row_view(params)
        adapter = self._adapter_for_task(row_view[0])
        row = self.store.get_row(*row_view)
        preview = adapter.preview_item(row)
        return {"preview": preview}

    def validate(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        row_view = self._required_row_view(params)
        full_row = self.store.get_row(*row_view)
        adapter = self._adapter_for_task(row_view[0])
        session = self._build_session(row_view[0])
        if full_row is None:
            raise ServiceError(-32602, f"row {row_view[0]}:{row_view[1]} missing")
        resolution = adapter.validate_resolution(full_row, params.get("edits"))
        if isinstance(resolution, tuple) and len(resolution) == 2:
            valid, errors = resolution
            result = {"valid": bool(valid), "errors": [errors] if errors else []}
        else:
            result = {"valid": resolution.valid, "errors": resolution.errors}
        if _coerce_bool(params.get("derive_output"), False):
            approved_output = session.derive_approved_output(session.get_row(*row_view), params.get("edits"))
            result["approved_output"] = approved_output
        return result

    def derive(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        row_view = self._required_row_view(params)
        session = self._build_session(row_view[0])
        output = session.derive_approved_output(session.get_row(*row_view), params.get("edits"))
        return {"approved_output": output}

    def decide(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            raise ServiceError(-32602, "params required")
        row_view = self._required_row_view(params)
        action = params.get("action")
        if action is None:
            raise ServiceError(-32602, "action required")
        if action not in _BUILTIN_ACTIONS:
            raise ServiceError(-32602, f"unsupported decision action '{action}'")

        session = self._build_session(row_view[0])
        decision_source = params.get("decision_source") or self.default_decision_source
        decision_actor = params.get("decision_actor") or self.default_decision_actor
        edits = params.get("edits")
        decision_note = _coerce_str(params.get("decision_note"), default=None)
        if decision_note is None and params.get("note") is not None:
            decision_note = _coerce_str(params.get("note"), default=None)

        recommendation = session.build_recommendation(
            row_view,
            action,
            edits=edits,
            note=decision_note,
            decision_source=decision_source,
            decision_actor=decision_actor,
            decision_metadata=params.get("decision_metadata"),
            decision_code_commit=params.get("decision_code_commit"),
        )

        apply = _coerce_bool(params.get("apply"), False)
        force_apply = _coerce_bool(params.get("force_apply"), False)
        if not apply:
            session_status = "recommendation_only"
            return {
                "status": session_status,
                "task_name": recommendation.task_name,
                "item_id": recommendation.item_id,
            "action": recommendation.action.value,
            "decision_source": recommendation.decision_source,
            "decision_actor": recommendation.decision_actor,
            "decision_note": recommendation.note,
            "validation": recommendation.validation.__dict__,
            "decision_payload": recommendation.decision_payload,
            "approved_output": recommendation.approved_output,
        }

        if params.get("decision_code_commit") is None and _coerce_bool(params.get("capture_decision_code_commit"), False):
            repo = Path(params.get("decision_code_working_directory") or Path.cwd())
            recommendation = replace(
                recommendation,
                decision_code_commit=get_head_commit(repo),
            )

        result = session.apply_recommendation(recommendation, force_apply=force_apply, apply=True)
        response: dict[str, Any] = {
            "status": result.status,
            "task_name": result.task_name,
            "item_id": result.item_id,
            "action": result.action.value if result.action else None,
            "decision": result.decision.value if result.decision else None,
            "message": result.message,
        }
        if result.row is not None:
            persisted = self.store.get_row(result.row.task_name, result.row.item_id)
            if persisted is not None:
                response["row"] = _row_view_payload(persisted, self._adapter_for_task(result.row.task_name))
        return response

    def list_actions(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            raise ServiceError(-32602, "params required")
        query = self._query_from_params(params.get("query", params))
        rows = self.store.query_rows(query)
        if query.task_name is None and not rows:
            return {"actions": []}

        action_map: dict[str, ActionDescriptor] = {}
        for row in rows:
            adapter = self._adapter_for_task(row.task_name)
            for action in adapter.available_actions(row):
                action_map[action.name] = action
        return {"actions": [action.__dict__ for action in action_map.values()]}

    def invoke_action(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            raise ServiceError(-32602, "params required")
        payload = params or {}
        task_name = payload.get("task_name")
        if not task_name:
            raise ServiceError(-32602, "task_name required")
        action = payload.get("action")
        if not action:
            raise ServiceError(-32602, "action required")
        item_ids = payload.get("item_ids")
        if isinstance(item_ids, str):
            item_ids = [item_id.strip() for item_id in item_ids.split(",") if item_id.strip()]

        query = self._query_from_params({"task_name": task_name, "item_ids": item_ids})
        rows = self.store.query_rows(query)
        if not rows:
            return {"action": action, "results": []}

        adapter = self._adapter_for_task(task_name)
        if action in _BUILTIN_ACTIONS:
            outcomes = []
            decision_code_commit = payload.get("decision_code_commit")
            if decision_code_commit is None and _coerce_bool(payload.get("capture_decision_code_commit"), False):
                decision_code_commit = get_head_commit(Path(payload.get("decision_code_working_directory") or Path.cwd()))
            for row in rows:
                rec = self._build_session(task_name).build_recommendation(
                    (row.task_name, row.item_id),
                    action,
                    edits=payload.get("edits"),
                    note=_coerce_str(payload.get("decision_note"), default=None)
                    if payload.get("decision_note") is not None
                    else _coerce_str(payload.get("note"), default=None),
                    decision_source=payload.get("decision_source") or self.default_decision_source,
                    decision_actor=payload.get("decision_actor") or self.default_decision_actor,
                    decision_metadata=payload.get("decision_metadata"),
                    decision_code_commit=decision_code_commit,
                )
                apply = _coerce_bool(payload.get("apply"), False)
                force_apply = _coerce_bool(payload.get("force_apply"), False)
                if not apply:
                    outcomes.append(
                        {
                            "task_name": rec.task_name,
                            "item_id": rec.item_id,
                            "action": action,
                            "status": "recommendation_only",
                        }
                    )
                    continue
                result = _build_session_result(self, task_name, rec, force_apply=force_apply)
                outcomes.append(
                    {
                        "task_name": result["task_name"],
                        "item_id": result["item_id"],
                        "action": result["action"],
                        "status": result["status"],
                        "message": result.get("message"),
                    }
                )
            return {"action": action, "results": outcomes}

        results: list[dict[str, Any]] = []
        for row in rows:
            action_result = adapter.execute_action(row=row, action=action, params=payload.get("params"), store=self.store)
            results.append(
                {
                    "task_name": row.task_name,
                    "item_id": row.item_id,
                    "status": "ok" if action_result.success else "error",
                    "message": action_result.message,
                    "payload": action_result.payload,
                }
            )
        return {"action": action, "results": results}

    def export(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        query = self._query_from_params(params)
        format_name = params.get("format", "csv")
        include_applied_commit = _coerce_bool(params.get("include_applied_commit"), False)
        del include_applied_commit  # future compatibility
        if format_name not in {"csv", "jsonl", "markdown", "md"}:
            raise ServiceError(-32602, "format must be one of csv/jsonl/markdown/md")
        output = params.get("output")
        if output:
            destination = Path(output)
            if format_name == "csv":
                self.store.export_csv(destination, query=query)
            elif format_name == "jsonl":
                self.store.export_jsonl(destination, query=query)
            else:
                self.store.export_markdown(destination, query=query)
            return {"format": format_name, "output": str(destination)}

        # Return materialized payload when no output path provided.
        rows = self.store.query_rows(query)
        if format_name == "jsonl":
            return {"format": "jsonl", "data": [row.to_dict() for row in rows]}

        if format_name in {"markdown", "md"}:
            lines = [
                "| task_name | item_id | item_locator | machine_status | human_status | human_notes |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                notes = (row.human_notes or "").replace("|", "\\|")
                locator = (row.item_locator or "").replace("|", "\\|")
                lines.append(
                    f"| {row.task_name} | {row.item_id} | {locator} | {row.machine_status.value} | {row.human_status.value} | {notes} |"
                )
            return {"format": "markdown", "data": "\n".join(lines)}

        if format_name == "csv":
            headers = [
                "task_name",
                "item_id",
                "machine_status",
                "human_status",
                "item_locator",
                "human_notes",
            ]
            lines = [",".join(headers)]
            for row in rows:
                values = [
                    str(row.task_name),
                    str(row.item_id),
                    row.machine_status.value,
                    row.human_status.value,
                    row.item_locator or "",
                    (row.human_notes or "").replace(",", ";"),
                ]
                lines.append(",".join(f'"{value.replace(chr(34), chr(34) + chr(34))}"' for value in values))
            return {"format": "csv", "data": "\n".join(lines)}

        raise ServiceError(-32602, f"unsupported format '{format_name}'")

    def _required_row_view(self, params: dict[str, Any] | None = None) -> tuple[str, str]:
        if params is None:
            raise ServiceError(-32602, "params required")
        task_name = params.get("task_name")
        item_id = params.get("item_id")
        if not task_name or not item_id:
            raise ServiceError(-32602, "task_name and item_id required")
        row = self.store.get_row(task_name, item_id)
        if row is None:
            raise ServiceError(-32602, f"row {task_name}:{item_id} not found")
        return (row.task_name, row.item_id)

    def health(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        return {"status": "ok", "ledger_path": str(self.store.ledger_path)}

    def system_shutdown(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        return {"status": "ok", "message": "shutdown requested"}

def _build_session_result(
    service: LedgerService,
    task_name: str,
    rec: Any,
    *,
    force_apply: bool,
) -> dict[str, Any]:
    session = service._build_session(task_name)
    result = session.apply_recommendation(rec, force_apply=force_apply, apply=True)  # type: ignore[arg-type]
    return {
        "task_name": result.task_name,
        "item_id": result.item_id,
        "action": result.action.value if result.action else None,
        "status": result.status,
        "decision": result.decision.value if result.decision else None,
        "message": result.message,
    }
