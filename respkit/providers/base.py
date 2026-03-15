"""Provider interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass
class ProviderConfig:
    """Provider-level request options."""

    timeout_s: float = 30.0
    max_retries: int = 1
    temperature: float = 0.0
    additional_options: Mapping[str, Any] | None = None


class MessageLike(Protocol):
    role: str
    content: str

    def to_api_payload(self) -> Mapping[str, Any]:
        ...


@dataclass
class ProviderResponse:
    """Captures response payloads from provider calls for observability."""

    request_payload: Mapping[str, Any]
    raw_response: Mapping[str, Any]
    parsed_payload: Mapping[str, Any] | None
    usage: Mapping[str, Any] | None
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class ProviderError:
    """Normalized provider error shape for stable callers."""

    code: str
    message: str

    def as_exception_message(self) -> str:
        return f"[{self.code}] {self.message}"


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    def complete(
        self,
        *,
        messages: list[MessageLike],
        model: str,
        response_model: type | None = None,
        config: ProviderConfig | None = None,
    ) -> ProviderResponse:
        """Send messages to a provider and return structured capture fields."""

        raise NotImplementedError
