from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import sleep as default_sleep
from typing import Any, Callable, Iterator, Protocol, TypeVar


T = TypeVar("T")

_RETRYABLE_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "08000",  # connection_exception
        "08003",  # connection_does_not_exist
        "08006",  # connection_failure
        "08001",  # sqlclient_unable_to_establish_sqlconnection
        "08004",  # sqlserver_rejected_establishment_of_sqlconnection
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
    }
)

_TRANSACTION_DEPTH: ContextVar[int] = ContextVar("omnix_postgresql_transaction_depth", default=0)


class TransactionContractError(RuntimeError):
    """Raised when an operation violates the PostgreSQL transaction contract."""


class TransactionExecutor(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[Any]: ...


@dataclass(frozen=True, slots=True)
class TransactionPolicy:
    max_attempts: int = 3
    retry_base_seconds: float = 0.025

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0 <= self.retry_base_seconds <= 10:
            raise ValueError("retry_base_seconds must be between 0 and 10")

    def delay_for_attempt(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return self.retry_base_seconds * (2 ** (attempt - 1))


@contextmanager
def transaction_scope() -> Iterator[None]:
    token = _TRANSACTION_DEPTH.set(_TRANSACTION_DEPTH.get() + 1)
    try:
        yield
    finally:
        _TRANSACTION_DEPTH.reset(token)


def in_transaction() -> bool:
    return _TRANSACTION_DEPTH.get() > 0


def require_outside_transaction(operation_name: str) -> None:
    if in_transaction():
        raise TransactionContractError(
            f"{operation_name} cannot run inside an authoritative PostgreSQL transaction"
        )


def postgres_sqlstate(error: BaseException) -> str | None:
    visited: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        value = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if value:
            return str(value)
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        current = cause
    return None


def is_retryable_transaction_error(error: BaseException) -> bool:
    return postgres_sqlstate(error) in _RETRYABLE_SQLSTATES


def run_transaction(
    database: TransactionExecutor,
    operation: Callable[[Any], T],
    *,
    policy: TransactionPolicy | None = None,
    sleep: Callable[[float], None] = default_sleep,
) -> T:
    resolved = policy or TransactionPolicy()
    for attempt in range(1, resolved.max_attempts + 1):
        try:
            with database.transaction() as connection:
                return operation(connection)
        except Exception as exc:
            if attempt >= resolved.max_attempts or not is_retryable_transaction_error(exc):
                raise
            delay = resolved.delay_for_attempt(attempt)
            if delay:
                sleep(delay)
    raise AssertionError("transaction retry loop exhausted without returning or raising")
