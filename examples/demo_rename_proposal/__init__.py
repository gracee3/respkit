"""Public synthetic demo tasks for respkit."""

from .schemas import DemoRenameProposalOutput, DemoRenameReviewOutput
from .task import build_tasks

__all__ = ["build_tasks", "DemoRenameProposalOutput", "DemoRenameReviewOutput"]
