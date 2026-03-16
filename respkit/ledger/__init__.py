"""Generic ledger abstractions for staged machine/human adjudication."""

from .git import GitWorkingTreeState, LedgerGitError, get_head_commit, require_clean_working_tree, working_tree_state
from .models import HumanDecision, LedgerRow, MachineStatus
from .query import LedgerQuery
from .store import ApplyCallback, ApplyPolicy, ApplyResult, LedgerStore
from .resolver import (
    DefaultResolverHooks,
    ResolverDecision,
    LedgerResolver,
    ResolverHooks,
    ResolverAction,
    ResolverApplyResult,
    ResolverRecommendation,
    ResolverRowView,
    ResolverResult,
    ResolverSession,
    ValidationResult,
    load_hook_class,
)

__all__ = [
    "ApplyCallback",
    "ApplyPolicy",
    "ApplyResult",
    "GitWorkingTreeState",
    "LedgerGitError",
    "LedgerQuery",
    "LedgerRow",
    "LedgerStore",
    "MachineStatus",
    "HumanDecision",
    "DefaultResolverHooks",
    "LedgerResolver",
    "ResolverAction",
    "ResolverApplyResult",
    "ResolverRecommendation",
    "ResolverRowView",
    "ResolverSession",
    "ResolverDecision",
    "ResolverResult",
    "ResolverHooks",
    "ValidationResult",
    "load_hook_class",
    "get_head_commit",
    "require_clean_working_tree",
    "working_tree_state",
]
