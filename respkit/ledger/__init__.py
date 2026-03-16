"""Generic ledger abstractions for staged machine/human adjudication."""

from .git import GitWorkingTreeState, LedgerGitError, get_head_commit, require_clean_working_tree, working_tree_state
from .models import HumanDecision, LedgerRow, MachineStatus
from .query import LedgerQuery
from .store import ApplyCallback, ApplyPolicy, ApplyResult, LedgerStore

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
    "get_head_commit",
    "require_clean_working_tree",
    "working_tree_state",
]
