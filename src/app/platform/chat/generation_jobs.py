"""Background execution for accepted Chat generation jobs."""
from __future__ import annotations

import contextvars
import inspect
import logging
import queue
import threading
from collections import defaultdict, deque
from collections.abc import Callable, MutableMapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import time
from dataclasses import dataclass
from typing import Any, Literal, overload
from weakref import WeakValueDictionary

from app.jobs import CancelJobRequest, CompleteJobRequest, FailJobRequest
from app.jobs.models import JobRecord, JobStatus
from app.observability.logging import log_context
from app.observability.tracing import span
from app.persistence.device_permits import device_permit_slot
from app.providers.base import provider_turn_owner
from app.providers.catalog import LOCAL_DEVICE, provider_supports
from app.runtime.cancellation import CancellationToken

from .models import ChatMessage, ChatSession, SendChatMessageRequest

logger = logging.getLogger(__name__)

ContextBuilder = Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]]
CompletionHook = Callable[[Any, str, str, list[dict[str, Any]], dict[str, Any]], None]

_CHAT_WORKER_COUNT = 4
_CHAT_OUTSTANDING_LIMIT = 128
# Provider calls run on a fixed pool. It is larger than the worker count so an
# interrupted call that is still unwinding does not hold back the next turn.
_CHAT_PROVIDER_CALL_LIMIT = 2 * _CHAT_WORKER_COUNT
# In-process cancellation wakes a turn at once; a cancel recorded by another
# gateway process is seen through the job store at this interval.
_CROSS_PROCESS_CANCEL_POLL_SECONDS = 1.0
_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.LEASED,
    JobStatus.RUNNING,
    JobStatus.WAITING,
    JobStatus.RETRYING,
}
_registry_guard = threading.Lock()
_submission_locks: WeakValueDictionary[str, Any] = WeakValueDictionary()
_job_commit_locks: WeakValueDictionary[str, Any] = WeakValueDictionary()
_job_cancel_events: dict[str, CancellationToken] = {}
_active_chat_providers: dict[str, Any] = {}
_execution_registry_lock = threading.Lock()


class _ChatGenerationInterrupted(Exception):
    """Internal signal used when a newer prompt supersedes this turn."""


class ChatQueueFull(RuntimeError):
    """Local execution capacity is exhausted; the accepted job remains observable."""


@dataclass(frozen=True)
class _ChatGenerationWork:
    chat_store: Any
    job_store: Any
    job: JobRecord
    request: SendChatMessageRequest
    context_builder: ContextBuilder | None
    completion_hook: CompletionHook | None


class _ChatGenerationDispatcher:
    """Bound local execution while preserving turn order within each session."""

    def __init__(self, worker_count: int = _CHAT_WORKER_COUNT,
                 outstanding_limit: int = _CHAT_OUTSTANDING_LIMIT,
                 provider_call_limit: int = _CHAT_PROVIDER_CALL_LIMIT) -> None:
        if worker_count < 1 or outstanding_limit < worker_count:
            raise ValueError("outstanding limit must accommodate all Chat workers")
        if provider_call_limit < worker_count:
            raise ValueError("provider call limit must accommodate all Chat workers")
        self._worker_count = worker_count
        self._outstanding_limit = outstanding_limit
        self._provider_call_limit = provider_call_limit
        self._provider_pool: ThreadPoolExecutor | None = None
        self._provider_calls = 0
        self._outstanding = 0
        # None wakes a worker to stop.
        self._ready_sessions: queue.Queue[str | None] = queue.Queue()
        self._pending: dict[str, deque[_ChatGenerationWork]] = defaultdict(deque)
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()
        self._started = False
        self._closing = False
        self._threads: list[threading.Thread] = []
        self._active: dict[str, Any] = {}
        self._admission_rejections = 0

    def diagnostics(self):
        with self._lock:
            return {"active_dispatch_slots": len(self._active),
                    "queued_dispatches": sum(len(items) for items in self._pending.values()),
                    "outstanding_limit": self._outstanding_limit,
                    "admission_rejection_count": self._admission_rejections,
                    "provider_calls_in_flight": self._provider_calls,
                    "provider_call_limit": self._provider_call_limit,
                    "closing": self._closing}

    def submit_provider_call(self, call: Callable[[], Any]) -> Future:
        """Run one provider call on the bounded pool; excess calls wait in its queue."""
        with self._lock:
            if self._closing:
                raise ChatQueueFull("Chat execution is shutting down. Retry on an available gateway.")
            if self._provider_pool is None:
                self._provider_pool = ThreadPoolExecutor(
                    max_workers=self._provider_call_limit,
                    thread_name_prefix="omnix-chat-provider",
                )
            self._provider_calls += 1
            # The call runs in the submitter's context: job log ids, turn owner, tenant.
            future = self._provider_pool.submit(contextvars.copy_context().run, call)

        def finished(_future: Future) -> None:
            with self._lock:
                self._provider_calls -= 1

        future.add_done_callback(finished)
        return future

    def submit(self, work: _ChatGenerationWork) -> None:
        session_id = str((work.job.input_payload or {}).get("session_id") or "").strip()
        if not session_id:
            session_id = f"missing-session:{work.job.id}"
        with self._lock:
            if self._closing:
                self._admission_rejections += 1
                raise ChatQueueFull('Chat execution is shutting down. Retry on an available gateway.')
            if self._outstanding >= self._outstanding_limit:
                self._admission_rejections += 1
                raise ChatQueueFull("Chat execution queue is full. Retry when an active response finishes.")
            self._outstanding += 1
            self._pending[session_id].append(work)
            if session_id not in self._scheduled:
                self._scheduled.add(session_id)
                self._ready_sessions.put(session_id)
            if not self._started:
                self._started = True
                for index in range(self._worker_count):
                    thread = threading.Thread(
                        target=self._worker,
                        name=f"omnix-chat-worker-{index + 1}",
                        daemon=True,
                    )
                    self._threads.append(thread)
                    thread.start()

    def close(self, timeout: float = 5):
        with self._lock:
            self._closing = True
            works = list(self._active.values()) + [work for items in self._pending.values() for work in items]
            self._outstanding -= sum(len(items) for items in self._pending.values())
            self._pending.clear()
            self._scheduled.clear()
        for work in works:
            try:
                cancel_chat_generation_job(work.chat_store, work.job_store, work.job.id,
                                           CancelJobRequest(reason='Gateway is shutting down.'))
            except Exception:
                logger.exception('Could not cancel Chat job during shutdown: %s', work.job.id)
        for _ in self._threads:
            self._ready_sessions.put(None)
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))
        with self._lock:
            pool, self._provider_pool = self._provider_pool, None
        if pool is not None:
            # Queued calls are dropped; running ones were interrupted above.
            pool.shutdown(wait=False, cancel_futures=True)
        return sum(thread.is_alive() for thread in self._threads)

    def _worker(self) -> None:
        while True:
            session_id = self._ready_sessions.get()
            if session_id is None:
                self._ready_sessions.task_done()
                return
            work: _ChatGenerationWork | None = None
            retry = False
            with self._lock:
                pending = self._pending.get(session_id)
                if pending:
                    work = pending.popleft()
                    self._active[work.job.id] = work
            try:
                if work is not None:
                    current = work.job_store.get_job(work.job.id)
                    if current is None or current.status in {
                        JobStatus.COMPLETED,
                        JobStatus.FAILED,
                        JobStatus.CANCELED,
                        JobStatus.STALE,
                        JobStatus.CANCEL_REQUESTED,
                    }:
                        _drop_job_cancel_event(work.job.id)
                        continue
                    try_start = getattr(work.job_store, 'try_start_chat_job', work.job_store.mark_running)
                    started = try_start(work.job.id) if current.status == JobStatus.QUEUED else current
                    if started is None and current.status == JobStatus.QUEUED:
                        retry = True
                        time.sleep(.05)
                        continue
                    if started is None or started.status in {
                        JobStatus.COMPLETED,
                        JobStatus.FAILED,
                        JobStatus.CANCELED,
                        JobStatus.STALE,
                        JobStatus.CANCEL_REQUESTED,
                    }:
                        _drop_job_cancel_event(work.job.id)
                        continue
                    with log_context(
                        job_id=started.id, feature="chat",
                        request_id=getattr(started, "correlation_id", None),
                    ), span(
                        "job.execute", job_type="chat_generation", job_id=started.id,
                        request_id=getattr(started, "correlation_id", None),
                    ):
                        _run_chat_generation_job(
                            chat_store=work.chat_store,
                            job_store=work.job_store,
                            job=started,
                            request=work.request,
                            context_builder=work.context_builder,
                            completion_hook=work.completion_hook,
                        )
            except Exception:
                logger.exception(
                    "Unhandled Chat generation worker failure for job %s; worker will continue",
                    getattr(getattr(work, "job", None), "id", "unknown"),
                )
            finally:
                if work is not None and not retry:
                    _drop_job_cancel_event(work.job.id)
                with self._lock:
                    if work is not None:
                        self._active.pop(work.job.id, None)
                        if retry and not self._closing:
                            self._pending[session_id].appendleft(work)
                        else:
                            self._outstanding -= 1
                    pending = self._pending.get(session_id)
                    if pending:
                        self._ready_sessions.put(session_id)
                    else:
                        self._pending.pop(session_id, None)
                        self._scheduled.discard(session_id)
                self._ready_sessions.task_done()


_dispatcher = _ChatGenerationDispatcher()


def _registry_lock(registry: MutableMapping[Any, threading.RLock], key: Any) -> threading.RLock:
    with _registry_guard:
        return registry.setdefault(key, threading.RLock())


@overload
def _job_cancel_event(job_id: str, *, create: Literal[True]) -> CancellationToken: ...


@overload
def _job_cancel_event(job_id: str, *, create: bool = False) -> CancellationToken | None: ...


def _job_cancel_event(job_id: str, *, create: bool = False) -> CancellationToken | None:
    with _execution_registry_lock:
        event = _job_cancel_events.get(job_id)
        if event is None and create:
            event = CancellationToken()
            _job_cancel_events[job_id] = event
        return event


def _drop_job_cancel_event(job_id: str) -> None:
    with _execution_registry_lock:
        _job_cancel_events.pop(job_id, None)


def _register_active_chat_provider(job_id: str, provider: Any) -> None:
    with _execution_registry_lock:
        _active_chat_providers[job_id] = provider


def _drop_active_chat_provider(job_id: str) -> None:
    with _execution_registry_lock:
        _active_chat_providers.pop(job_id, None)


def _interrupt_active_chat_provider(job_id: str) -> bool:
    with _execution_registry_lock:
        provider = _active_chat_providers.get(job_id)
    if provider is None:
        return False
    interrupt = getattr(provider, "cancel_active_request", None)
    if not callable(interrupt):
        return False
    try:
        # Name the job so a shared provider interrupts only this job's turn.
        if "owner" in inspect.signature(interrupt).parameters:
            return bool(interrupt(owner=job_id))
        return bool(interrupt())
    except Exception:
        logger.warning("Could not interrupt Chat provider for job %s", job_id, exc_info=True)
        return False


@contextmanager
def chat_submission_lock(session_id: str, submission_id: str | None, *, job_store=None, chat_store=None):
    """Serialize idempotent acceptance for one browser submission."""

    durable = getattr(job_store, 'chat_transaction', None)
    if callable(durable):
        with durable(chat_store, session_id):
            yield
        return
    key = (session_id, submission_id or f"anonymous:{threading.get_ident()}")
    lock = _registry_lock(_submission_locks, key)
    with lock:
        yield


@contextmanager
def chat_job_commit_lock(job_id: str):
    """Make cancellation and final transcript persistence mutually exclusive."""

    lock = _registry_lock(_job_commit_locks, job_id)
    with lock:
        yield


def find_chat_generation_job(
    job_store: Any,
    *,
    session_id: str,
    submission_id: str | None,
) -> JobRecord | None:
    if not submission_id:
        return None
    find = getattr(job_store, "find_job_by_submission", None)
    if callable(find):
        return find(
            job_type="chat.generate",
            session_id=session_id,
            submission_id=submission_id,
        )
    for job in job_store.iter_jobs(job_types=("chat.generate",)):
        if (
            job.type == "chat.generate"
            and isinstance(job.input_ref, dict)
            and job.input_ref.get("session_id") == session_id
            and isinstance(job.input_payload, dict)
            and job.input_payload.get("submission_id") == submission_id
        ):
            return job
    return None


def existing_chat_generation_turn(
    chat_store: Any,
    job: JobRecord,
) -> tuple[ChatSession, ChatMessage] | None:
    payload = job.input_payload or {}
    session_id = str(payload.get("session_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    session = chat_store.get_session(session_id) if session_id else None
    if session is None:
        return None
    message = _find_user_message(session, message_id)
    return (session, message) if message is not None else None


def mark_chat_acceptance_failed(
    chat_store: Any,
    *,
    session_id: str,
    message_id: str,
    error: Exception,
) -> None:
    message = " ".join(str(error).split()).strip() or type(error).__name__
    try:
        _patch_user_message_metadata(
            chat_store,
            session_id=session_id,
            message_id=message_id,
            metadata={"generation_status": "failed", "generation_error": message[:500]},
        )
    except Exception:
        logger.warning("Could not mark unqueued Chat message %s failed", message_id, exc_info=True)
    _mark_assistant_turn_failed(chat_store, session_id, message_id)


def start_chat_generation_job(
    *,
    chat_store: Any,
    job_store: Any,
    job: JobRecord,
    request: SendChatMessageRequest,
    context_builder: ContextBuilder | None = None,
    completion_hook: CompletionHook | None = None,
) -> JobRecord:
    """Queue local Chat generation after the job has been durably accepted."""

    # Keep the durable state queued until the per-session dispatcher actually
    # takes the work. This lets a newer prompt cancel a queued predecessor
    # without falsely presenting it as provider execution.
    _job_cancel_event(job.id, create=True)
    try:
        dispatcher = getattr(job_store, 'chat_dispatcher', _dispatcher)
        dispatcher.submit(_ChatGenerationWork(
            chat_store=chat_store,
            job_store=job_store,
            job=job,
            request=request,
            context_builder=context_builder,
            completion_hook=completion_hook,
        ))
    except ChatQueueFull as exc:
        _drop_job_cancel_event(job.id)
        payload = job.input_payload or {}
        _fail_job(chat_store, job_store, job, exc,
                  session_id=payload.get("session_id"), message_id=payload.get("message_id"),
                  code="chat_queue_full")
        return job_store.get_job(job.id) or job
    return job


def active_chat_generation_jobs(
    job_store: Any,
    *,
    session_id: str,
) -> list[JobRecord]:
    """Return active Chat turns for one session in submission order."""

    lookup = getattr(job_store, 'list_active_chat_jobs', None)
    if callable(lookup):
        return lookup(session_id)
    active = []
    for job in job_store.iter_jobs(job_types=("chat.generate",)):
        if job.type != "chat.generate" or job.status not in _ACTIVE_JOB_STATUSES:
            continue
        payload = job.input_payload or {}
        if str(payload.get("session_id") or "").strip() != session_id:
            continue
        active.append(job)
    return sorted(active, key=lambda item: (item.created_at, item.id))


def interrupt_active_chat_generation_jobs(
    chat_store: Any,
    job_store: Any,
    *,
    session_id: str,
    reason: str,
) -> list[JobRecord]:
    """Cancel all prior active turns before accepting a newer prompt."""

    canceled: list[JobRecord] = []
    for job in active_chat_generation_jobs(job_store, session_id=session_id):
        result = cancel_chat_generation_job(
            chat_store,
            job_store,
            job.id,
            CancelJobRequest(reason=reason),
        )
        if result is not None:
            canceled.append(result)
    return canceled


def cancel_chat_generation_job(
    chat_store: Any,
    job_store: Any,
    job_id: str,
    request: CancelJobRequest,
) -> JobRecord | None:
    """Cancel without racing the final transcript/job commit."""

    durable = getattr(job_store, 'chat_transaction', None)
    current = job_store.get_job(job_id)
    session_id = str((current.input_payload or {}).get('session_id') or '') if current else ''
    transaction = durable(chat_store, session_id) if callable(durable) and session_id else nullcontext()
    # Match admission/completion: acquire the durable session lock before any
    # process-local job lock to avoid opposite lock ordering during interruption.
    with transaction:
        return _cancel_chat_generation_job_locked(chat_store, job_store, job_id, request)


def _cancel_chat_generation_job_locked(chat_store, job_store, job_id, request):

    with chat_job_commit_lock(job_id):
        current = job_store.get_job(job_id)
        if current is None:
            return None
        if current.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELED,
            JobStatus.STALE,
        }:
            return current
        canceled = job_store.cancel_job(job_id, request) or current
        def signal():
            event = _job_cancel_event(job_id, create=True)
            if event is not None:
                event.set()
            if current.status in {
                JobStatus.LEASED, JobStatus.RUNNING, JobStatus.WAITING, JobStatus.RETRYING,
            }:
                _interrupt_active_chat_provider(job_id)
            else:
                _drop_job_cancel_event(job_id)

        if callable(getattr(job_store, 'chat_transaction', None)):
            from app.persistence.transaction_binding import after_commit
            after_commit(job_store.database, signal)
        else:
            signal()
        payload = current.input_payload or {}
        session_id = str(payload.get("session_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        if session_id and message_id:
            _cancel_chat_turn(chat_store, job_store, current, session_id, message_id)
        result = job_store.get_job(job_id) or canceled
        if current.status in {JobStatus.QUEUED, JobStatus.WAITING, JobStatus.RETRYING}:
            _drop_job_cancel_event(job_id)
        return result


def recover_abandoned_chat_generation_jobs(chat_store: Any, job_store: Any) -> int:
    """Recover abandoned Chat jobs while preserving work owned by live gateways."""

    candidates = getattr(job_store, "iter_recoverable_chat_jobs", None)
    transition = getattr(job_store, "recover_chat_job", None)
    if callable(candidates) and callable(transition):
        recovered = 0
        for candidate in candidates():
            job = transition(candidate.id)
            if job is None:
                continue
            payload = job.input_payload or {}
            if chat_store is not None and payload.get("session_id") and payload.get("message_id"):
                marker = _mark_assistant_turn_canceled if job.status == JobStatus.CANCELED else _mark_assistant_turn_failed
                try:
                    marker(chat_store, payload["session_id"], payload["message_id"])
                except Exception:
                    logger.warning("Could not update recovered assistant turn for job %s", job.id, exc_info=True)
            recovered += 1
        return recovered

    list_jobs = getattr(job_store, "list_jobs", None)
    if not callable(list_jobs):
        return 0

    recovered = 0
    for job in list_jobs(limit=500):
        if (
            job.type != "chat.generate"
            or job.compat.get("execution_owner")
            or not job.compat.get("inline_execution")
            or job.status not in _ACTIVE_JOB_STATUSES | {JobStatus.CANCEL_REQUESTED}
        ):
            continue
        payload = job.input_payload or {}
        session_id = str(payload.get("session_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        if job.status == JobStatus.CANCEL_REQUESTED:
            if session_id and message_id:
                _cancel_chat_turn(chat_store, job_store, job, session_id, message_id)
            else:
                _finalize_job_cancel(job_store, job.id)
        else:
            _fail_job(
                chat_store,
                job_store,
                job,
                RuntimeError("Gateway restarted before Chat generation completed."),
                session_id=session_id or None,
                message_id=message_id or None,
            )
        recovered += 1
    return recovered


def _resolve_chat_provider(
    session: ChatSession,
    request: SendChatMessageRequest,
    user_message: ChatMessage | None = None,
) -> Any | None:
    provider_id = _chat_provider_id(session, request, user_message)
    if not provider_id:
        return None
    try:
        from app.providers.service import get_provider

        return get_provider(provider_id)
    except Exception:
        logger.warning("Could not resolve Chat provider for active cancellation", exc_info=True)
        return None


def _chat_provider_id(
    session: ChatSession,
    request: SendChatMessageRequest,
    user_message: ChatMessage | None,
) -> str:
    metadata = getattr(user_message, "metadata", None)
    route = metadata.get("omnix_provider_route") if isinstance(metadata, dict) else None
    routed_provider = route.get("provider_id") if isinstance(route, dict) else None
    return str(
        routed_provider
        or request.provider_id
        or getattr(session, "provider_id", None)
        or ""
    ).strip().casefold().removeprefix("llm:")


def _chat_provider_slot(provider_id: str):
    if provider_supports(provider_id, LOCAL_DEVICE):
        return device_permit_slot(
            "llm-local",
            priority="interactive",
            timeout_seconds=5.0,
        )
    return nullcontext()


def _generate_reply_with_interrupt(
    *,
    chat_store: Any,
    job_store: Any,
    session: ChatSession,
    user_message: ChatMessage,
    request: SendChatMessageRequest,
    context_items: list[dict[str, Any]],
    job: JobRecord,
) -> dict[str, Any] | None:
    """Run provider work on the bounded pool so cancellation can release the worker.

    The worker wakes when the call finishes or the job's token is cancelled.
    A cancel recorded by another process is read from the job store every
    ``_CROSS_PROCESS_CANCEL_POLL_SECONDS``.
    """

    if _cancel_requested(job_store, job.id):
        raise _ChatGenerationInterrupted()
    provider_id = _chat_provider_id(session, request, user_message)
    token = _job_cancel_event(job.id, create=True)

    def invoke() -> dict[str, Any] | None:
        if token.cancelled:  # cancelled while queued behind other calls
            return None
        with _chat_provider_slot(provider_id), provider_turn_owner(job.id):
            return _generate_reply(
                chat_store,
                session,
                user_message,
                request=request,
                context_items=context_items,
            )

    future = _dispatcher.submit_provider_call(invoke)
    wake = threading.Event()
    future.add_done_callback(lambda _future: wake.set())
    stop_waking = token.on_cancel(wake.set)
    try:
        while not wake.wait(timeout=_CROSS_PROCESS_CANCEL_POLL_SECONDS):
            if _cancel_requested(job_store, job.id):
                break
    finally:
        stop_waking()
    if not future.done() or token.cancelled:
        future.cancel()
        _interrupt_active_chat_provider(job.id)
        raise _ChatGenerationInterrupted()
    return future.result()


def _run_chat_generation_job(
    *,
    chat_store: Any,
    job_store: Any,
    job: JobRecord,
    request: SendChatMessageRequest,
    context_builder: ContextBuilder | None,
    completion_hook: CompletionHook | None,
) -> None:
    payload = job.input_payload or {}
    session_id = str(payload.get("session_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    if not session_id or not message_id:
        _fail_job(
            chat_store,
            job_store,
            job,
            RuntimeError("Chat generation job is missing its session or message identity."),
        )
        return

    try:
        _update_progress(job_store, job.id, "Preparing response")
        context_items: list[dict[str, Any]] = []
        context_diagnostics: dict[str, Any] = {}
        if context_builder is not None:
            context_items, context_diagnostics = context_builder()
            _patch_user_message_metadata(
                chat_store,
                session_id=session_id,
                message_id=message_id,
                metadata={
                    "context_sources": _context_source_summaries(context_items),
                    "context_diagnostics": context_diagnostics,
                },
            )
            _update_job_input(
                job_store,
                job,
                {
                    **payload,
                    "context_sources": [
                        str(item.get("source_id") or "context")
                        for item in context_items
                    ],
                    "context_diagnostics": context_diagnostics,
                },
            )

        if _cancel_requested(job_store, job.id):
            _cancel_chat_turn(chat_store, job_store, job, session_id, message_id)
            return

        # Only the newest part of the transcript a prompt can read (WP-5.7).
        load_window = getattr(chat_store, "get_session_window", None)
        session = (
            load_window(session_id, through_message_id=message_id)
            if callable(load_window)
            else chat_store.get_session(session_id)
        )
        if session is None:
            raise RuntimeError("Chat session not found while generation was starting.")
        user_index = next(
            (
                index
                for index, item in enumerate(session.messages)
                if item.id == message_id and item.role == "user"
            ),
            None,
        )
        if user_index is None:
            raise RuntimeError("Chat user message not found while generation was starting.")
        # A later accepted turn may already be durable. Generate against only
        # the transcript through this turn; the per-session dispatcher will
        # add this reply before the next turn starts.
        session = session.model_copy(
            update={
                "messages": session.messages[: user_index + 1],
                "message_count": (
                    session.message_count - (len(session.messages) - user_index - 1)
                    if session.transcript_is_window
                    else user_index + 1
                ),
            }
        )
        user_message = session.messages[-1]

        _mark_assistant_turn_streaming(user_message)
        _update_progress(job_store, job.id, "Generating response")
        provider = _resolve_chat_provider(session, request, user_message)
        if provider is not None:
            _register_active_chat_provider(job.id, provider)
        try:
            answer = _generate_reply_with_interrupt(
                chat_store=chat_store,
                job_store=job_store,
                session=session,
                user_message=user_message,
                request=request,
                context_items=context_items,
                job=job,
            )
        finally:
            _drop_active_chat_provider(job.id)
        if not answer:
            raise RuntimeError("Chat generation ended without a response.")
        content = str(answer.get("content") or "").strip()
        metadata = dict(answer.get("metadata") or {})
        metadata["reply_to_message_id"] = message_id

        def before_commit() -> None:
            _persist_routing_metadata(chat_store, session, user_message)
            if context_items:
                metadata["context_sources"] = _context_source_summaries(context_items)
                metadata["context_diagnostics"] = context_diagnostics

        hook = None
        if completion_hook is not None:
            def hook() -> None:
                completion_hook(chat_store, session_id, message_id, context_items, context_diagnostics)

        _commit_chat_reply(
            chat_store, job_store, job, session_id=session_id, message_id=message_id,
            content=content, metadata=metadata, before_commit=before_commit, completion_hook=hook,
        )
    except _ChatGenerationInterrupted:
        _cancel_chat_turn(chat_store, job_store, job, session_id, message_id)
    except Exception as exc:  # pragma: no cover - exercised through job state
        _fail_job(chat_store, job_store, job, exc, session_id=session_id, message_id=message_id)


def _commit_chat_reply(
    chat_store: Any,
    job_store: Any,
    job: JobRecord,
    *,
    session_id: str,
    message_id: str,
    content: str,
    metadata: dict[str, Any],
    before_commit: Callable[[], None] | None = None,
    completion_hook: Callable[[], None] | None = None,
) -> ChatSession | None:
    """Persist a reply and complete its job atomically, unless the turn was canceled.

    Returns the updated session, or ``None`` when a cancellation won the race.
    """
    atomic_completion = getattr(job_store, 'chat_completion', None)
    transaction = atomic_completion(chat_store, job.id) if callable(atomic_completion) else nullcontext()
    with transaction, chat_job_commit_lock(job.id):
        if _cancel_requested(job_store, job.id):
            _cancel_chat_turn(chat_store, job_store, job, session_id, message_id)
            return None
        require_owner = getattr(job_store, "require_chat_execution_owner", None)
        if callable(require_owner):
            require_owner(job.id)
        if before_commit is not None:
            before_commit()
        completed = chat_store.complete_streamed_reply(
            session_id,
            message_id,
            content,
            metadata,
        )
        if completed is None:
            raise RuntimeError("Chat response could not be persisted.")
        if completion_hook is not None:
            try:
                completion_hook()
            except Exception:
                if not callable(atomic_completion):
                    _remove_assistant_reply(chat_store, session_id, message_id)
                raise
        completed_job = job_store.complete_job(
            job.id,
            CompleteJobRequest(
                output_refs=[
                    {
                        "type": "chat_response",
                        "module": "chatbot",
                        "session_id": session_id,
                        "message_id": message_id,
                        "content": content,
                    }
                ],
                logs=[
                    {
                        "level": "info",
                        "message": "Chat response generated and persisted.",
                        "session_id": session_id,
                        "message_id": message_id,
                    }
                ],
            ),
        )
        if completed_job is None:
            raise RuntimeError("Chat generation completed but its job disappeared.")
    return completed


def _fail_job(
    chat_store: Any,
    job_store: Any,
    job: JobRecord,
    error: Exception,
    *,
    session_id: str | None = None,
    message_id: str | None = None,
    code: str = "chat_generation_failed",
) -> None:
    message = " ".join(str(error).split()).strip() or type(error).__name__
    current = job_store.get_job(job.id)
    if current is None or current.status in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELED,
        JobStatus.STALE,
    }:
        return
    if current.status == JobStatus.CANCEL_REQUESTED:
        if session_id and message_id:
            _cancel_chat_turn(chat_store, job_store, job, session_id, message_id)
        else:
            _finalize_job_cancel(job_store, job.id)
        return
    if session_id and message_id:
        try:
            _patch_user_message_metadata(
                chat_store,
                session_id=session_id,
                message_id=message_id,
                metadata={"generation_status": "failed", "generation_error": message[:500]},
            )
        except Exception:
            logger.warning("Could not mark failed Chat message %s", message_id, exc_info=True)
        _mark_assistant_turn_failed(chat_store, session_id, message_id)
    try:
        job_store.fail_job(
            job.id,
            FailJobRequest(
                code=code,
                message=message[:500],
                retryable=True,
                details={
                    key: value
                    for key, value in {
                        "session_id": session_id,
                        "message_id": message_id,
                    }.items()
                    if value
                },
            ),
        )
    except Exception:
        logger.exception("Could not fail Chat generation job %s", job.id)


def _cancel_chat_turn(
    chat_store: Any,
    job_store: Any,
    job: JobRecord,
    session_id: str,
    message_id: str,
) -> None:
    current = job_store.get_job(job.id)
    if current is None or current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STALE}:
        return
    try:
        _patch_user_message_metadata(
            chat_store,
            session_id=session_id,
            message_id=message_id,
            metadata={"generation_status": "canceled"},
        )
    except Exception:
        logger.warning("Could not mark canceled Chat message %s", message_id, exc_info=True)
    _mark_assistant_turn_canceled(chat_store, session_id, message_id)
    _finalize_job_cancel(job_store, job.id)


def _finalize_job_cancel(job_store: Any, job_id: str) -> None:
    finalize = getattr(job_store, "finalize_cancel", None)
    if callable(finalize):
        finalize(job_id, "Chat generation canceled.")
    else:
        job_store.cancel_job(job_id, CancelJobRequest(reason="Chat generation canceled."))


def _cancel_requested(job_store: Any, job_id: str) -> bool:
    current = job_store.get_job(job_id)
    return current is None or current.status in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.STALE,
        JobStatus.CANCEL_REQUESTED,
        JobStatus.CANCELED,
    }


def _update_progress(job_store: Any, job_id: str, message: str) -> None:
    update = getattr(job_store, "update_progress", None)
    if callable(update):
        update(job_id, current=0, total=1, message=message)


def _update_job_input(job_store: Any, job: JobRecord, payload: dict[str, Any]) -> None:
    update = getattr(job_store, "update_job_input", None)
    if callable(update):
        update(job.id, payload)


def _find_user_message(session: ChatSession, message_id: str) -> ChatMessage | None:
    return next(
        (
            message
            for message in session.messages
            if message.id == message_id and message.role == "user"
        ),
        None,
    )


def _assistant_turn_id(user_message: ChatMessage) -> str:
    return str(user_message.metadata.get("assistant_turn_id") or "").strip()


def _mark_assistant_turn_streaming(user_message: ChatMessage) -> None:
    assistant_turn_id = _assistant_turn_id(user_message)
    if not assistant_turn_id:
        return
    from .assistant_turns import default_assistant_turn_coordinator

    default_assistant_turn_coordinator().mark_streaming(assistant_turn_id)


def _mark_assistant_turn_canceled(chat_store: Any, session_id: str, message_id: str) -> None:
    user_message = _load_user_message(chat_store, session_id, message_id)
    assistant_turn_id = _assistant_turn_id(user_message) if user_message is not None else ""
    if not assistant_turn_id:
        return
    try:
        from .assistant_turns import default_assistant_turn_coordinator

        coordinator = default_assistant_turn_coordinator()
        coordinator.request_cancel(assistant_turn_id, "Chat generation canceled.")
        coordinator.mark_provider_cancelled(assistant_turn_id)
    except Exception:
        logger.warning("Could not mark assistant turn %s canceled", assistant_turn_id, exc_info=True)


def _mark_assistant_turn_failed(chat_store: Any, session_id: str, message_id: str) -> None:
    user_message = _load_user_message(chat_store, session_id, message_id)
    assistant_turn_id = _assistant_turn_id(user_message) if user_message is not None else ""
    if not assistant_turn_id:
        return
    try:
        from .assistant_turns import default_assistant_turn_coordinator

        default_assistant_turn_coordinator().mark_failed(assistant_turn_id)
    except Exception:
        logger.warning("Could not mark assistant turn %s failed", assistant_turn_id, exc_info=True)


def _load_user_message(chat_store: Any, session_id: str, message_id: str) -> ChatMessage | None:
    session = chat_store.get_session(session_id)
    return _find_user_message(session, message_id) if session is not None else None


def _remove_assistant_reply(chat_store: Any, session_id: str, message_id: str) -> None:
    remove = getattr(chat_store, "remove_assistant_reply", None)
    if callable(remove):
        remove(session_id, message_id)
        return
    session = chat_store.get_session(session_id)
    if session is None:
        return
    reply_ids = [
        item.id
        for item in session.messages
        if item.role == "assistant"
        and item.metadata.get("reply_to_message_id") == message_id
    ]
    delete_messages = getattr(chat_store, "delete_messages", None)
    if callable(delete_messages):
        delete_messages(session_id, reply_ids)
        return
    delete_ids = set(reply_ids)
    session.messages = [item for item in session.messages if item.id not in delete_ids]
    session.message_count = len(session.messages)
    chat_store._save_session(session)


def _generate_reply(
    chat_store: Any,
    session: ChatSession,
    user_message: ChatMessage,
    *,
    request: SendChatMessageRequest,
    context_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use the established non-streaming generation boundary in the worker."""

    from .memory_port import execute_memory_command, parse_memory_command

    command = parse_memory_command(user_message.content)
    if command is not None:
        memory_factory = getattr(chat_store, "memory_service_factory", None)
        if not callable(memory_factory):
            raise RuntimeError("Chat memory service is unavailable")
        result = execute_memory_command(
            chat_store,
            memory_factory(),
            session.id,
            user_message.id,
            command,
        )
        return {
            "content": result.content,
            "metadata": {
                "generation_status": "completed",
                "memory_command": result.model_dump(mode="json"),
            },
        }

    generate = getattr(chat_store, "_generate_reply", None)
    if not callable(generate):
        raise RuntimeError("Chat store does not provide a generation boundary")
    return generate(
        session,
        user_message,
        provider_id=request.provider_id or session.provider_id,
        model_id=request.model_id or session.model_id,
        request=request,
        context_items=context_items,
    )


def _persist_routing_metadata(
    chat_store: Any,
    session: ChatSession,
    user_message: ChatMessage,
) -> None:
    keys = (
        "omnix_chat_routed",
        "omnix_route",
        "semantic_intent",
        "semantic_task",
        "semantic_compilation",
        "routing_decision",
        "request_mode",
        "turn_plan",
        "active_objective",
        "routing_environment",
    )
    patch = {key: user_message.metadata[key] for key in keys if key in user_message.metadata}
    if not patch:
        return
    _patch_user_message_metadata(
        chat_store,
        session_id=session.id,
        message_id=user_message.id,
        metadata=patch,
    )


def _patch_user_message_metadata(
    chat_store: Any,
    *,
    session_id: str,
    message_id: str,
    metadata: dict[str, Any],
) -> None:
    if chat_store is None:
        return
    update = getattr(chat_store, "update_user_message_metadata", None)
    if callable(update):
        update(session_id=session_id, message_id=message_id, metadata=metadata)
        return
    update = getattr(chat_store, "update_message_metadata", None)
    if callable(update):
        update(session_id=session_id, message_id=message_id, metadata=metadata)
        return
    session = chat_store.get_session(session_id)
    if session is None:
        return
    message = next((item for item in session.messages if item.id == message_id), None)
    if message is None:
        return
    message.metadata.update(metadata)
    chat_store._save_session(session)


def _context_source_summaries(context_items: list[dict[str, Any]]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for item in context_items:
        source_id = str(item.get("source_id") or "context").strip()
        title = str(item.get("title") or source_id).strip()
        summary = {"source_id": source_id, "title": title}
        url = str(item.get("url") or "").strip()
        if url:
            summary["url"] = url
        raw_metadata = item.get("metadata")
        item_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        citation = str(item_metadata.get("citation_label") or "").strip()
        if citation:
            summary["citation"] = citation
        summaries.append(summary)
    return summaries
