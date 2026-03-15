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
    responses: dict[str, list[_FakeResponse]]
    captured: dict[str, Any]

    def __init__(self, response: _FakeResponse, captured: dict[str, Any]):
        self.responses = {"get": [response], "post": []}
        self.captured = captured
        self.calls: list[tuple[str, str, Any]] = []

    def _pop(self, method: str) -> _FakeResponse:
        if method not in self.responses or not self.responses[method]:
            raise RuntimeError(f"No mocked response for {method}")
        return self.responses[method].pop(0)

    def register(self, method: str, response: _FakeResponse) -> None:
        self.responses.setdefault(method, []).append(response)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]):
        call = self._pop("post")
        self.calls.append(("POST", url, dict(json)))
        self.captured.setdefault("calls", []).append({"method": "POST", "url": url, "body": dict(json), "headers": dict(headers)})
        self.captured["url"] = url
        self.captured["json"] = dict(json)
        self.captured["headers"] = dict(headers)
        return call

    def get(self, url: str, *, headers: dict[str, str]):
        call = self._pop("get")
        self.calls.append(("GET", url, {}))
        self.captured.setdefault("calls", []).append({"method": "GET", "url": url, "headers": dict(headers)})
        self.captured["url"] = url
        self.captured["headers"] = dict(headers)
        return call


class Payload(BaseModel):
    foo: int


def test_openai_provider_success_captures_request_and_payload(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={"data": [{"id": "gpt-oss-20b"}]},
            ),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"foo": 123}'}]}],
                    "usage": {"input_tokens": 2},
                },
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert len(captured["calls"]) == 2
    assert captured["calls"][0]["method"] == "GET"
    assert captured["calls"][0]["url"] == "http://localhost:8000/v1/models"
    assert captured["calls"][1]["method"] == "POST"
    assert captured["calls"][1]["url"] == "http://localhost:8000/v1/responses"
    assert captured["json"]["model"] == "gpt-oss-20b"
    assert captured["json"]["input"] == [{"role": "user", "content": "hello"}]
    assert "response_format" not in captured["json"]
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
        client = _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={"data": [{"id": "gpt-oss-20b"}]},
            ),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"foo": 456}'}]}],
                },
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert captured["calls"][0]["url"] == "http://localhost:8000/v1/models"
    assert captured["calls"][1]["url"] == "http://localhost:8000/v1/responses"
    assert result.parsed_payload == {"foo": 456}
    assert result.error_code is None


def test_openai_provider_preflight_success_when_model_exists(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, payload={"data": [{"id": "gpt-oss-20b"}, {"id": "other-model"}]}),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"foo": 123}'}]}]},
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert result.error_code is None
    assert result.discovered_models == ["gpt-oss-20b", "other-model"]
    assert captured["calls"][0]["url"] == "http://localhost:8000/v1/models"
    assert captured["calls"][1]["url"] == "http://localhost:8000/v1/responses"


def test_openai_provider_preflight_model_missing(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        return _FakeClient(
            response=_FakeResponse(status_code=200, payload={"data": [{"id": "other-model"}]}),
            captured=captured,
        )

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert result.error_code == "preflight_model_not_found"
    assert result.status_code == 404
    assert "requested_model=gpt-oss-20b" in result.error_message
    assert "discovered_models=['other-model']" in result.error_message
    assert result.discovered_models == ["other-model"]
    assert len(captured["calls"]) == 1
    assert captured["calls"][0]["method"] == "GET"
    assert captured["calls"][0]["url"] == "http://localhost:8000/v1/models"


def test_openai_provider_request_error_is_normalized(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, payload={"data": [{"id": "gpt-oss-20b"}]}),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(status_code=500, raise_error=Exception("provider unreachable"), payload={}),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
    )

    assert len(captured["calls"]) == 2
    assert captured["calls"][0]["method"] == "GET"
    assert captured["calls"][1]["method"] == "POST"
    assert captured["json"]["model"] == "gpt-oss-20b"
    assert result.error_code == "request_failed"
    assert "provider unreachable" in result.error_message
    assert result.request_payload == captured["json"]
    assert result.parsed_payload is None
    assert result.usage is None


def test_openai_provider_invalid_json_payload_is_reported(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, payload={"data": [{"id": "gpt-oss-20b"}]}),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={"output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]},
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
    )

    assert result.error_code == "invalid_payload"
    assert result.parsed_payload is None
    assert result.error_message is not None
    assert "No parseable JSON payload" in result.error_message


def test_openai_provider_parses_embedded_json_in_plain_text(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={"data": [{"id": "gpt-oss-20b"}]},
            ),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "prefix text {\n  \"foo\": 123\n} suffix"}
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                },
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert result.error_code is None
    assert result.parsed_payload == {"foo": 123}


def test_openai_provider_rejects_plain_text_without_json(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_client_factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(
            response=_FakeResponse(status_code=200, payload={"data": [{"id": "gpt-oss-20b"}]}),
            captured=captured,
        )
        client.register(
            "post",
            _FakeResponse(
                status_code=200,
                payload={
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "no json here"}]}],
                },
            ),
        )
        return client

    monkeypatch.setattr("httpx.Client", fake_client_factory)

    provider = OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses")
    result = provider.complete(
        messages=[Message(role="user", content="hello")],
        model="gpt-oss-20b",
        response_model=Payload,
    )

    assert result.error_code == "invalid_payload"
    assert result.error_message is not None
    assert "No parseable JSON payload" in result.error_message
