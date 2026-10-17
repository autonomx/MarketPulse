class PersistenceError(RuntimeError):
    """Base error for authoritative persistence operations."""


class RevisionConflict(PersistenceError):
    """Raised when an optimistic update uses a stale revision."""


class IdempotencyConflict(PersistenceError):
    """Raised when an operation key is reused with a different request."""


class EntityNotFound(PersistenceError, KeyError):
    """Raised when a required authoritative aggregate does not exist."""
