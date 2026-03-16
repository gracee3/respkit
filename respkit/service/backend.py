"""Stdio transport entrypoint for the ledger service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .adapters import TaskServiceAdapter, load_task_adapter
from .dispatcher import (
    _JSONRPC_VERSION,
    ServiceError,
    LedgerService,
)


def _load_adapters(adapter_targets: list[str] | None) -> list[TaskServiceAdapter]:
    adapters: list[TaskServiceAdapter] = []
    if adapter_targets is None:
        return adapters

    for target in adapter_targets:
        adapter_type = load_task_adapter(target)
        adapter = adapter_type()
        if not isinstance(adapter, TaskServiceAdapter):
            raise TypeError(f"{target} does not implement TaskServiceAdapter")
        adapters.append(adapter)
    return adapters


class LedgerServiceBackend:
    """Translate JSON-RPC requests into `LedgerService` calls."""

    def __init__(
        self,
        ledger_path: Path,
        adapters: list[TaskServiceAdapter] | None = None,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        error_stream: TextIO | None = None,
    ) -> None:
        self.service = LedgerService(ledger_path, adapters=adapters or [])
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.error = error_stream or sys.stderr
        self._running = True

    def close(self) -> None:
        self.service.close()

        if not self.output.closed:
            self.output.flush()

    def _dispatch(self, method: str, params: dict[str, Any] | None, request_id: Any) -> dict[str, Any]:
        if method not in self._method_map():
            raise ServiceError(-32601, f"unknown method '{method}'")
        return self._method_map()[method](params or {})

    def _method_map(self) -> dict[str, Any]:
        return {
            "ledger.open": self._open,
            "ledger.info": self.service.info,
            "ledger.summary": self.service.summary,
            "ledger.health": self.service.health,
            "ledger.tasks": self.service.tasks,
            "rows.list": self.service.list_rows,
            "rows.get": self.service.get_row,
            "rows.history": self.service.get_row_history,
            "rows.preview": self.service.preview_row,
            "rows.validate": self.service.validate,
            "rows.derive": self.service.derive,
            "rows.decide": self.service.decide,
            "actions.list": self.service.list_actions,
            "actions.invoke": self.service.invoke_action,
            "export": self.service.export,
            "system.shutdown": self._shutdown,
        }

    def _open(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        return self.service.info()

    def _shutdown(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        self._running = False
        return {"status": "ok", "message": "shutdown"}

    def _response_success(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "result": result}

    def _response_error(self, request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        return {
            "jsonrpc": _JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": code, "message": message, "data": data},
        }

    def run(self) -> None:
        """Run the read/response loop over stdin/stdout."""
        while self._running:
            raw = self.input.readline()
            if raw == "":
                break
            request = raw.strip()
            if not request:
                continue

            request_id: Any = None
            has_id = False
            try:
                payload = json.loads(request)
                if not isinstance(payload, dict):
                    raise ServiceError(-32600, "request must be an object")
                if "jsonrpc" not in payload:
                    raise ServiceError(-32600, "missing jsonrpc field")
                method = payload.get("method")
                if not isinstance(method, str) or not method:
                    raise ServiceError(-32600, "method must be a non-empty string")

                if "id" in payload:
                    request_id = payload["id"]
                    has_id = True
                    if not isinstance(request_id, (str, int, float)) and request_id is not None:
                        raise ServiceError(-32600, "request id must be string, number, or null")

                params = payload.get("params")
                if params is not None and not isinstance(params, dict):
                    raise ServiceError(-32602, "params must be an object")

                result = self._dispatch(method, params, request_id)
            except json.JSONDecodeError as exc:
                response = self._response_error(request_id, -32700, "parse error", str(exc))
            except ServiceError as exc:
                response = self._response_error(request_id, exc.code, exc.message, exc.data)
            except Exception as exc:  # noqa: BLE001
                response = self._response_error(request_id, -32603, "internal error", str(exc))
            else:
                response = self._response_success(request_id, result)

            if has_id and not self._running and payload.get("method") == "system.shutdown":
                # still emit final response for explicit shutdown requests
                self.output.write(json.dumps(response, ensure_ascii=False))
                self.output.write("\n")
                self.output.flush()
                break

            if not has_id:
                continue
            self.output.write(json.dumps(response, ensure_ascii=False))
            self.output.write("\n")
            self.output.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ledger service for local frontend clients")
    parser.add_argument("--ledger", required=True, type=Path, help="Path to SQLite ledger file")
    parser.add_argument(
        "--adapter",
        action="append",
        default=None,
        help="Optional task adapter as module:Class, repeatable",
    )
    parser.add_argument("--stdio", action="store_true", help="Enable stdio transport")
    return parser


def run_stdio_server(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    adapters = _load_adapters(args.adapter)
    backend = LedgerServiceBackend(
        ledger_path=args.ledger,
        adapters=adapters,
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        error_stream=sys.stderr,
    )
    try:
        backend.run()
    finally:
        backend.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_stdio_server(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
