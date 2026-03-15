"""Task definitions consumed by runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from ..inputs import NormalizedInput
from ..providers.base import ProviderConfig
from ..artifacts.writer import ArtifactPolicy
from ..validators.base import Validator
from ..actions.base import Action


PromptContextBuilder = Callable[[NormalizedInput], Mapping[str, Any]]
ReviewContextBuilder = Callable[[NormalizedInput, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class TaskDefinition:
    """Reusable task configuration object."""

    name: str
    description: str
    prompt_template_path: str | Path
    response_model: type[BaseModel]
    provider_model: str
    provider_options: Mapping[str, Any] | None = None
    validators: tuple[Validator, ...] = ()
    actions: tuple[Action, ...] = ()
    artifact_policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)
    provider_config: ProviderConfig = field(default_factory=ProviderConfig)
    input_mode: str = "text"
    min_input_chars: int | None = None
    prompt_context_builder: PromptContextBuilder = lambda input_obj: {"text": input_obj.decoded_text}
    review_policy: "ReviewPolicy | None" = None

    def normalized_provider_options(self) -> dict[str, Any]:
        return dict(self.provider_options or {})


@dataclass(frozen=True)
class ReviewPolicy:
    """Optional review task configuration."""

    task: "TaskDefinition"
    context_builder: ReviewContextBuilder
