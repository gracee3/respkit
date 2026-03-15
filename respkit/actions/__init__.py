"""Reusable side-effect callbacks for task outputs."""

from .base import Action, ActionContext, ActionResult
from .markdown import WriteMarkdownAction
from .manifest import AppendManifestAction
from .json_artifact import WriteJSONArtifactAction

__all__ = [
    "Action",
    "ActionContext",
    "ActionResult",
    "WriteMarkdownAction",
    "AppendManifestAction",
    "WriteJSONArtifactAction",
]
