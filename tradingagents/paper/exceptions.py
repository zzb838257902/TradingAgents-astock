"""Paper portfolio repository exceptions."""

from __future__ import annotations


class PaperError(Exception):
    """Base error for paper portfolio operations."""


class StaleFencingToken(PaperError):
    """Fencing token does not match the authoritative account lease."""


class IdempotencyConflict(PaperError):
    """Duplicate business key with a different payload."""


class LeaseNotHeld(PaperError):
    """Caller does not hold the active account lease."""


class LeaseExpired(PaperError):
    """Account lease has expired."""


class LeaseConflict(PaperError):
    """Another process holds a valid account lease."""


class LeaseTimeout(PaperError):
    """Timed out waiting for the account file lock."""


class AccountNotFound(PaperError):
    """Paper account does not exist."""


class OrderNotFound(PaperError):
    """Paper order does not exist."""


class InvalidExecutionBatch(PaperError):
    """Execution batch failed validation."""


class ScreeningInputError(PaperError):
    """Screening request or calendar inputs are invalid."""


class InvalidScreenRun(PaperError):
    """Frozen screen run cannot be used for rebalance planning."""


class RevisionConflict(PaperError):
    """Rebalance revision cannot be created or reused."""
