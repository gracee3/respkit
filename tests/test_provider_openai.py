from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any

from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from respkit.providers.openai_compatible import OpenAICompatibleProvider
from respkit.tasks.message import Message


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any] | None = None
    text: str = ""
    raise_error: Exception | None = None

    def json(self) -> dict[str, Any] | None:
        if self.payload is None:
            raise ValueError("invalid json")
        return self.payload

    def raise_for_status(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error


@dataclass
class _FakeClient:
    response: _FakeResponse
    captured: dict[str, Any]

    def __init__(self, response: _FakeResponse, captured: dict[str, Any]):
        self.response = response
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]):
        self.captured["url"] = url
        self.captured["json"] = dict(json)
        self.captured["headers"] = dict(headers)
        return self.response


class Payload(BaseModel):
    foo: int


def test_openai_provider_success_captures_request_and_payload(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"foo": 123}'}]}],
                    "usage": {"input_tokens": 2},
                },
            ),
            captured=captured,
        )

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert captured["url"] == "http://localhost:8000/responses"
    assert captured["json"]["model"] == "gpt-oss-20b"
    assert captured["json"]["input"] == [{"role": "user", "content": "hello"}]
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert result.request_payload == captured["json"]
    assert result.parsed_payload == {"foo": 123}
    assert result.raw_response.get("usage") == {"input_tokens": 2}
    assert result.usage == {"input_tokens": 2}
    assert result.error_code is None
    assert result.error_message is None
    assert result.status_code == 200


def test_openai_provider_accepts_full_responses_endpoint(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"foo": 456}'}]}],
                },
            ),
            captured=captured,
        )

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert captured["url"] == "http://localhost:8000/v1/responses"
    assert result.parsed_payload == {"foo": 456}
    assert result.error_code is None


def test_openai_provider_request_error_is_normalized(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(
            response=_FakeResponse(status_code=500, raise_error=Exception("provider unreachable"), payload={}),
            captured=captured,
        )

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
    )

    assert captured["json"]["model"] == "gpt-oss-20b"
    assert result.error_code == "request_failed"
    assert "provider unreachable" in result.error_message
    assert result.request_payload == captured["json"]
    assert result.parsed_payload is None
    assert result.usage is None


def test_openai_provider_invalid_json_payload_is_reported(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={"output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]},
            ),
            captured=captured,
        )

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
    )

    assert result.error_code == "invalid_payload"
    assert result.parsed_payload is None
    assert result.error_message is not None
    assert "Could not parse JSON content" in result.error_message
