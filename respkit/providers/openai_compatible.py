"""OpenAI-compatible Responses API provider adapter."""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx
from pydantic import BaseModel

from .base import LLMProvider, MessageLike, ProviderConfig, ProviderResponse


class OpenAICompatibleProvider(LLMProvider):
    """Thin wrapper around an OpenAI-compatible Responses endpoint."""

    def __init__(self, endpoint: str, api_key: str | None = None) -> None:
        normalized = endpoint.rstrip("/")
        if normalized.endswith("/responses"):
            normalized = normalized[: -len("/responses")]
        if normalized.endswith("/v1"):
            self._api_base = normalized
        else:
            self._api_base = f"{normalized}/v1"
        self._responses_url = f"{self._api_base}/responses"
        self._models_url = f"{self._api_base}/models"
        self._api_key = api_key
        self._discovered_models: list[str] | None = None
        self._preflight_complete = False

    def complete(
        self,
        *,
        messages: list[MessageLike],
        model: str,
        response_model: type[BaseModel] | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        cfg = config or ProviderConfig()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        discovered_models: list[str] | None = None
        if cfg.enable_model_preflight:
            try:
                discovered_models = self._discover_models(headers=headers, timeout=cfg.timeout_s)
            except Exception as exc:
                return ProviderResponse(
                    request_payload={"model": model},
                    raw_response={},
                    parsed_payload=None,
                    usage=None,
                    status_code=None,
                    error_code="preflight_request_failed",
                    error_message=self._annotate_error_message(str(exc), model=model, discovered_models=discovered_models),
                    discovered_models=discovered_models,
                )

            if model not in discovered_models:
                return ProviderResponse(
                    request_payload={"model": model},
                    raw_response={"discovered_models": discovered_models},
                    parsed_payload=None,
                    usage=None,
                    status_code=404,
                    error_code="preflight_model_not_found",
                    error_message=f"requested_model={model}; discovered_models={discovered_models}",
                    discovered_models=discovered_models,
                )

        payload: dict[str, Any] = {
            "model": model,
            "input": [message.to_api_payload() for message in messages],
            "temperature": cfg.temperature,
        }
        if cfg.additional_options:
            payload.update(dict(cfg.additional_options))

        if response_model is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            }

        request_payload: dict[str, Any] = dict(payload)

        try:
            with httpx.Client(timeout=cfg.timeout_s) as client:
                response = client.post(
                    self._responses_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except Exception as exc:
            return ProviderResponse(
                request_payload=request_payload,
                raw_response={},
                parsed_payload=None,
                usage=None,
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
                error_code="request_failed",
                error_message=self._annotate_error_message(str(exc), model=model, discovered_models=discovered_models),
                discovered_models=discovered_models,
            )

        try:
            data = response.json()
        except ValueError as exc:  # noqa: BLE001
            return ProviderResponse(
                request_payload=request_payload,
                raw_response={"http_status": response.status_code, "body": response.text},
                parsed_payload=None,
                usage=None,
                status_code=response.status_code,
                error_code="invalid_json",
                error_message=self._annotate_error_message(
                    f"Could not decode JSON from provider: {exc}",
                    model=model,
                    discovered_models=discovered_models,
                ),
                discovered_models=discovered_models,
            )

        parsed_payload, parse_error = self._parse_payload(data)
        if parse_error is not None:
            return ProviderResponse(
                request_payload=request_payload,
                raw_response=data,
                parsed_payload=None,
                usage=data.get("usage"),
                status_code=response.status_code,
                error_code="invalid_payload",
                error_message=self._annotate_error_message(
                    parse_error,
                    model=model,
                    discovered_models=discovered_models,
                ),
                discovered_models=discovered_models,
            )

        return ProviderResponse(
            request_payload=request_payload,
            raw_response=data,
            parsed_payload=parsed_payload,
            usage=data.get("usage"),
            status_code=response.status_code,
            discovered_models=discovered_models,
        )

    def _discover_models(self, *, headers: Mapping[str, str], timeout: float) -> list[str]:
        if not self._preflight_complete:
            self._discovered_models = self._fetch_models(headers=headers, timeout=timeout)
            self._preflight_complete = True
        return list(self._discovered_models or [])

    def _fetch_models(self, *, headers: Mapping[str, str], timeout: float) -> list[str]:
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(self._models_url, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"model preflight failed for {self._models_url}: {exc}") from exc

        if not isinstance(payload := self._extract_model_payload(payload), list):
            raise RuntimeError("model preflight response had unexpected format")

        return payload

    @staticmethod
    def _annotate_error_message(message: str, *, model: str, discovered_models: list[str] | None) -> str:
        if discovered_models is None:
            return f"{message} (requested_model={model})"
        return f"{message} (requested_model={model}; discovered_models={discovered_models})"

    @staticmethod
    def _extract_model_payload(payload: Mapping[str, Any]) -> list[str]:
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        ids: list[str] = []
        for model in data:
            if isinstance(model, Mapping):
                model_id = model.get("id")
                if isinstance(model_id, str):
                    ids.append(model_id)
        return ids

    @staticmethod
    def _parse_payload(raw: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str | None]:
        # Responses API payloads usually include an ``output`` list with text chunks.
        output = raw.get("output")
        if isinstance(output, list):
            for piece in output:
                if not isinstance(piece, Mapping):
                    continue
                if piece.get("type") == "message":
                    content = piece.get("content")
                    if isinstance(content, list):
                        text_blocks = [
                            item.get("text")
                            for item in content
                            if isinstance(item, Mapping) and item.get("type") == "output_text" and isinstance(item.get("text"), str)
                        ]
                        if text_blocks:
                            text = "\n".join(text_blocks).strip()
                            try:
                                return json.loads(text), None
                            except json.JSONDecodeError as exc:
                                return None, f"Could not parse JSON content: {exc}"

                if piece.get("type") == "function_call":
                    if isinstance(piece.get("arguments"), str):
                        try:
                            return json.loads(piece["arguments"]), None
                        except json.JSONDecodeError as exc:
                            return None, f"Could not parse function arguments JSON: {exc}"

        # Fallback for older chat-like responses
        if isinstance(output, str):
            try:
                return json.loads(output), None
            except json.JSONDecodeError as exc:
                return None, f"Could not parse provider output string: {exc}"

        return None, "No parseable JSON payload found in provider output"
