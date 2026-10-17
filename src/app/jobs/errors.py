"""Job-domain execution and lease errors."""


class JobClaimConflict(RuntimeError):
    """Raised when a caller no longer owns the durable job lease."""
