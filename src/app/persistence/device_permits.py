"""PostgreSQL-backed, leased capacity for devices shared by Omnix processes."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator, Literal
from uuid import uuid4

from app.persistence.database import PostgresDatabase
from app.persistence.unit_of_work import unit_of_work


DeviceModelClass = Literal["tts", "stt", "image", "llm-local"]
PermitPriority = Literal["realtime", "interactive", "batch"]
_PRIORITY_ORDER = {"realtime": 0, "interactive": 1, "batch": 2}
_LOG = logging.getLogger(__name__)


class DevicePermitError(RuntimeError):
    """The authoritative device-capacity service could not grant a safe permit."""


class DevicePermitUnavailable(DevicePermitError):
    """The requested capacity remained occupied until the caller's deadline."""


@dataclass(frozen=True, slots=True)
class DevicePermitLease:
    permit_id: str
    lease_token: str
    device_id: str
    model_class: DeviceModelClass
    holder_id: str
    units: int
    priority: PermitPriority
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class DevicePermitHolder:
    permit_id: str
    holder_id: str
    units: int
    priority: PermitPriority
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceModelOwnerLease:
    device_id: str
    model_class: DeviceModelClass
    holder_id: str
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class DevicePermitCapacity:
    device_id: str
    model_class: DeviceModelClass
    capacity: int
    realtime_reserved_units: int
    holders: tuple[DevicePermitHolder, ...]
    waiting: tuple[tuple[PermitPriority, int], ...]
    model_owner: tuple[str, datetime] | None = None


class PostgresDevicePermitService:
    """Acquire and renew capacity with row-serialized PostgreSQL leases.

    Active model calls own a token-fenced row. Waiting calls live in a durable,
    expiring priority queue, so a process crash does not retain either capacity
    or queue position forever.
    """

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        device_id: str,
        lease_seconds: int = 120,
        poll_interval_seconds: float = 0.1,
        tts_model_owner: str | None = None,
    ) -> None:
        normalized_device = device_id.strip()
        if not normalized_device or len(normalized_device) > 255:
            raise ValueError("device_id must contain between 1 and 255 characters")
        if not 5 <= int(lease_seconds) <= 86_400:
            raise ValueError("lease_seconds must be between 5 and 86400")
        if not 0.02 <= float(poll_interval_seconds) <= 2.0:
            raise ValueError("poll_interval_seconds must be between 0.02 and 2")
        self.database = database
        self.device_id = normalized_device
        self.lease_seconds = int(lease_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.tts_model_owner = tts_model_owner

    def configure_capacity(
        self,
        model_class: DeviceModelClass,
        *,
        capacity: int,
        realtime_reserved_units: int = 0,
    ) -> None:
        capacity = int(capacity)
        reserved = int(realtime_reserved_units)
        if capacity < 1 or not 0 <= reserved <= capacity:
            raise ValueError("device capacity must be positive and realtime reservation must fit")
        with unit_of_work(self.database) as work:
            work.connection.execute(
                """INSERT INTO omnix_device_capacity (
                       device_id, model_class, capacity, realtime_reserved_units
                   ) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (device_id, model_class) DO NOTHING""",
                (self.device_id, model_class, capacity, reserved),
            )
            row = work.connection.execute(
                """SELECT capacity, realtime_reserved_units
                     FROM omnix_device_capacity
                    WHERE device_id = %s AND model_class = %s FOR UPDATE""",
                (self.device_id, model_class),
            ).fetchone()
            if row is None or (int(row[0]), int(row[1])) != (capacity, reserved):
                raise DevicePermitError(
                    f"device capacity configuration differs from PostgreSQL for {model_class}"
                )
            work.commit()

    def acquire(
        self,
        model_class: DeviceModelClass,
        *,
        holder_id: str,
        units: int = 1,
        priority: PermitPriority = "interactive",
        timeout_seconds: float = 0.0,
        lease_seconds: int | None = None,
    ) -> DevicePermitLease | None:
        holder = holder_id.strip()
        units = int(units)
        timeout = max(0.0, float(timeout_seconds))
        lease_duration = self.lease_seconds if lease_seconds is None else int(lease_seconds)
        self._validate_request(model_class, holder, units, priority, lease_duration)
        request_id = uuid4().hex
        request_ttl = max(10, int(timeout + lease_duration + 10))
        with unit_of_work(self.database) as work:
            work.connection.execute(
                """INSERT INTO omnix_device_permit_requests (
                       request_id, device_id, model_class, holder_id, units,
                       priority, expires_at
                   ) VALUES (%s, %s, %s, %s, %s, %s,
                       clock_timestamp() + (%s * INTERVAL '1 second'))""",
                (request_id, self.device_id, model_class, holder, units, priority, request_ttl),
            )
            work.commit()

        deadline = time.monotonic() + timeout
        try:
            while True:
                lease = self._try_grant(
                    request_id,
                    model_class=model_class,
                    holder_id=holder,
                    units=units,
                    priority=priority,
                    lease_seconds=lease_duration,
                    request_ttl=request_ttl,
                )
                if lease is not None:
                    return lease
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(self.poll_interval_seconds, remaining))
        finally:
            self._remove_request(request_id)

    def _try_grant(
        self,
        request_id: str,
        *,
        model_class: DeviceModelClass,
        holder_id: str,
        units: int,
        priority: PermitPriority,
        lease_seconds: int,
        request_ttl: int,
    ) -> DevicePermitLease | None:
        permit_id = uuid4().hex
        token = uuid4().hex
        with unit_of_work(self.database) as work:
            connection = work.connection
            capacity_row = connection.execute(
                """SELECT capacity, realtime_reserved_units
                     FROM omnix_device_capacity
                    WHERE device_id = %s AND model_class = %s FOR UPDATE""",
                (self.device_id, model_class),
            ).fetchone()
            if capacity_row is None:
                raise DevicePermitError(
                    f"device capacity is not configured for {self.device_id}/{model_class}"
                )
            capacity, reserved = int(capacity_row[0]), int(capacity_row[1])
            connection.execute(
                """DELETE FROM omnix_device_permits
                    WHERE device_id = %s AND model_class = %s
                      AND lease_expires_at <= clock_timestamp()""",
                (self.device_id, model_class),
            )
            connection.execute(
                """UPDATE omnix_device_permit_requests
                      SET expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
                    WHERE request_id = %s AND expires_at > clock_timestamp()""",
                (request_ttl, request_id),
            )
            # Only the head of the priority queue may acquire.
            head = connection.execute(
                """SELECT request_id, priority
                     FROM omnix_device_permit_requests
                    WHERE device_id = %s AND model_class = %s
                      AND expires_at > clock_timestamp()
                    ORDER BY CASE priority
                               WHEN 'realtime' THEN 0
                               WHEN 'interactive' THEN 1
                               ELSE 2 END,
                             requested_at, request_id
                    LIMIT 1""",
                (self.device_id, model_class),
            ).fetchone()
            if head is None or str(head[0]) != request_id:
                work.commit()
                return None
            active = connection.execute(
                """SELECT COALESCE(sum(units), 0)
                     FROM omnix_device_permits
                    WHERE device_id = %s AND model_class = %s
                      AND lease_expires_at > clock_timestamp()""",
                (self.device_id, model_class),
            ).fetchone()
            effective_capacity = capacity if priority == "realtime" else capacity - reserved
            if int(active[0]) + units > effective_capacity:
                work.commit()
                return None
            row = connection.execute(
                """INSERT INTO omnix_device_permits (
                       permit_id, lease_token, device_id, model_class, holder_id,
                       units, priority, lease_expires_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s,
                       clock_timestamp() + (%s * INTERVAL '1 second'))
                   RETURNING lease_expires_at""",
                (
                    permit_id, token, self.device_id, model_class, holder_id,
                    units, priority, lease_seconds,
                ),
            ).fetchone()
            connection.execute(
                "DELETE FROM omnix_device_permit_requests WHERE request_id = %s",
                (request_id,),
            )
            work.commit()
        return DevicePermitLease(
            permit_id=permit_id,
            lease_token=token,
            device_id=self.device_id,
            model_class=model_class,
            holder_id=holder_id,
            units=units,
            priority=priority,
            lease_expires_at=row[0],
        )

    def renew(self, lease: DevicePermitLease, *, lease_seconds: int | None = None) -> bool:
        duration = self.lease_seconds if lease_seconds is None else int(lease_seconds)
        if not 5 <= duration <= 86_400:
            raise ValueError("lease_seconds must be between 5 and 86400")
        with unit_of_work(self.database) as work:
            row = work.connection.execute(
                """UPDATE omnix_device_permits
                      SET lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
                    WHERE permit_id = %s AND lease_token = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING lease_expires_at""",
                (duration, lease.permit_id, lease.lease_token),
            ).fetchone()
            work.commit()
        return row is not None

    def release(self, lease: DevicePermitLease) -> bool:
        with unit_of_work(self.database) as work:
            cursor = work.connection.execute(
                """DELETE FROM omnix_device_permits
                    WHERE permit_id = %s AND lease_token = %s""",
                (lease.permit_id, lease.lease_token),
            )
            work.commit()
        return cursor.rowcount > 0

    def claim_model_owner(
        self,
        model_class: DeviceModelClass,
        *,
        holder_id: str,
    ) -> DeviceModelOwnerLease:
        holder = holder_id.strip()
        if not holder or len(holder) > 255:
            raise ValueError("holder_id must contain between 1 and 255 characters")
        token = uuid4().hex
        with unit_of_work(self.database) as work:
            row = work.connection.execute(
                """INSERT INTO omnix_device_model_owners (
                       device_id, model_class, holder_id, lease_token, lease_expires_at
                   ) VALUES (%s, %s, %s, %s,
                       clock_timestamp() + (%s * INTERVAL '1 second'))
                   ON CONFLICT (device_id, model_class) DO UPDATE
                       SET holder_id = EXCLUDED.holder_id,
                           lease_token = EXCLUDED.lease_token,
                           lease_expires_at = EXCLUDED.lease_expires_at,
                           acquired_at = clock_timestamp()
                     WHERE omnix_device_model_owners.lease_expires_at <= clock_timestamp()
                        OR omnix_device_model_owners.holder_id = EXCLUDED.holder_id
                   RETURNING lease_expires_at""",
                (
                    self.device_id,
                    model_class,
                    holder,
                    token,
                    self.lease_seconds,
                ),
            ).fetchone()
            if row is None:
                existing = work.connection.execute(
                    """SELECT holder_id FROM omnix_device_model_owners
                        WHERE device_id = %s AND model_class = %s""",
                    (self.device_id, model_class),
                ).fetchone()
                work.commit()
                raise DevicePermitError(
                    f"model {model_class} is already loaded by "
                    f"{str(existing[0]) if existing else 'another process'}"
                )
            work.commit()
        lease = DeviceModelOwnerLease(
            device_id=self.device_id,
            model_class=model_class,
            holder_id=holder,
            lease_token=token,
            lease_expires_at=row[0],
        )
        return lease

    def renew_model_owner(self, lease: DeviceModelOwnerLease) -> bool:
        with unit_of_work(self.database) as work:
            row = work.connection.execute(
                """UPDATE omnix_device_model_owners
                      SET lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
                    WHERE device_id = %s AND model_class = %s
                      AND holder_id = %s AND lease_token = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING lease_expires_at""",
                (
                    self.lease_seconds,
                    lease.device_id,
                    lease.model_class,
                    lease.holder_id,
                    lease.lease_token,
                ),
            ).fetchone()
            work.commit()
        return row is not None

    def release_model_owner(self, lease: DeviceModelOwnerLease) -> bool:
        with unit_of_work(self.database) as work:
            cursor = work.connection.execute(
                """DELETE FROM omnix_device_model_owners
                    WHERE device_id = %s AND model_class = %s
                      AND holder_id = %s AND lease_token = %s""",
                (lease.device_id, lease.model_class, lease.holder_id, lease.lease_token),
            )
            work.commit()
        return cursor.rowcount > 0

    def hold_model_owner(
        self,
        model_class: DeviceModelClass,
        *,
        holder_id: str,
        process_role: str,
        on_lost: Callable[[], None] | None = None,
    ) -> _DeviceModelOwnerGuard:
        if (
            model_class == "tts"
            and self.tts_model_owner is not None
            and process_role != self.tts_model_owner
        ):
            raise DevicePermitError(
                f"local TTS model ownership is configured for {self.tts_model_owner}"
            )
        lease = self.claim_model_owner(model_class, holder_id=holder_id)
        return _DeviceModelOwnerGuard(self, lease, on_lost=on_lost)

    def has_higher_priority_request(
        self,
        model_class: DeviceModelClass,
        *,
        priority: PermitPriority,
    ) -> bool:
        rank = _PRIORITY_ORDER[priority]
        with unit_of_work(self.database) as work:
            row = work.connection.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM omnix_device_permit_requests
                        WHERE device_id = %s AND model_class = %s
                          AND expires_at > clock_timestamp()
                          AND CASE priority
                                WHEN 'realtime' THEN 0
                                WHEN 'interactive' THEN 1
                                ELSE 2 END < %s
                   )""",
                (self.device_id, model_class, rank),
            ).fetchone()
            work.commit()
        return bool(row[0])

    def metrics_snapshot(self) -> list[dict[str, Any]]:
        """Capacity, held units and waiting requests per model class, read-only (WP-10.3).

        Unlike ``diagnostics`` it deletes nothing: expired leases and requests
        are excluded by their expiry, so a metrics scrape never writes.
        """
        with unit_of_work(self.database) as work:
            rows = work.connection.execute(
                """SELECT capacity.model_class, capacity.capacity,
                          COALESCE((SELECT sum(permit.units) FROM omnix_device_permits AS permit
                                     WHERE permit.device_id = capacity.device_id
                                       AND permit.model_class = capacity.model_class
                                       AND permit.lease_expires_at > clock_timestamp()), 0),
                          (SELECT count(*) FROM omnix_device_permit_requests AS request
                            WHERE request.device_id = capacity.device_id
                              AND request.model_class = capacity.model_class
                              AND request.expires_at > clock_timestamp())
                     FROM omnix_device_capacity AS capacity
                    WHERE capacity.device_id = %s
                    ORDER BY capacity.model_class
                    LIMIT 64""",
                (self.device_id,),
            ).fetchall()
            work.rollback()
        return [
            {"device_id": self.device_id, "model_class": str(row[0]), "capacity": int(row[1]),
             "held_units": int(row[2]), "waiting": int(row[3])}
            for row in rows
        ]

    def diagnostics(self) -> tuple[DevicePermitCapacity, ...]:
        with unit_of_work(self.database) as work:
            connection = work.connection
            connection.execute(
                "DELETE FROM omnix_device_permits WHERE lease_expires_at <= clock_timestamp()"
            )
            connection.execute(
                "DELETE FROM omnix_device_permit_requests WHERE expires_at <= clock_timestamp()"
            )
            capacities = connection.execute(
                """SELECT device_id, model_class, capacity, realtime_reserved_units
                     FROM omnix_device_capacity
                    WHERE device_id = %s ORDER BY model_class
                    LIMIT 64""",
                (self.device_id,),
            ).fetchall()
            result: list[DevicePermitCapacity] = []
            for device_id, model_class, capacity, reserved in capacities:
                holders = connection.execute(
                    """SELECT permit_id, holder_id, units, priority, lease_expires_at
                         FROM omnix_device_permits
                        WHERE device_id = %s AND model_class = %s
                          AND lease_expires_at > clock_timestamp()
                        ORDER BY acquired_at, permit_id
                        LIMIT %s""",
                    # Every holder holds at least one unit.
                    (device_id, model_class, max(1, int(capacity))),
                ).fetchall()
                waiting = connection.execute(
                    """SELECT priority, count(*)
                         FROM omnix_device_permit_requests
                        WHERE device_id = %s AND model_class = %s
                          AND expires_at > clock_timestamp()
                        GROUP BY priority ORDER BY priority
                        LIMIT 8""",
                    (device_id, model_class),
                ).fetchall()
                owner_row = connection.execute(
                    """SELECT holder_id, lease_token, lease_expires_at
                         FROM omnix_device_model_owners
                        WHERE device_id = %s AND model_class = %s
                          AND lease_expires_at > clock_timestamp()""",
                    (device_id, model_class),
                ).fetchone()
                result.append(
                    DevicePermitCapacity(
                        device_id=str(device_id),
                        model_class=model_class,
                        capacity=int(capacity),
                        realtime_reserved_units=int(reserved),
                        holders=tuple(
                            DevicePermitHolder(
                                permit_id=str(row[0]),
                                holder_id=str(row[1]),
                                units=int(row[2]),
                                priority=row[3],
                                lease_expires_at=row[4],
                            )
                            for row in holders
                        ),
                        waiting=tuple((row[0], int(row[1])) for row in waiting),
                        model_owner=(
                            (str(owner_row[0]), owner_row[2])
                            if owner_row is not None
                            else None
                        ),
                    )
                )
            work.commit()
        return tuple(result)

    def _remove_request(self, request_id: str) -> None:
        with unit_of_work(self.database) as work:
            work.connection.execute(
                "DELETE FROM omnix_device_permit_requests WHERE request_id = %s",
                (request_id,),
            )
            work.commit()

    @staticmethod
    def _validate_request(
        model_class: DeviceModelClass,
        holder_id: str,
        units: int,
        priority: PermitPriority,
        lease_seconds: int,
    ) -> None:
        if model_class not in {"tts", "stt", "image", "llm-local"}:
            raise ValueError("unsupported device model class")
        if not holder_id or len(holder_id) > 255:
            raise ValueError("holder_id must contain between 1 and 255 characters")
        if units < 1:
            raise ValueError("units must be positive")
        if priority not in _PRIORITY_ORDER:
            raise ValueError("unsupported device permit priority")
        if not 5 <= lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 5 and 86400")

    @contextmanager
    def slot(
        self,
        model_class: DeviceModelClass,
        *,
        holder_id: str,
        priority: PermitPriority = "interactive",
        timeout_seconds: float = 30.0,
        units: int = 1,
    ) -> Iterator[DevicePermitLease]:
        lease = self.acquire(
            model_class,
            holder_id=holder_id,
            units=units,
            priority=priority,
            timeout_seconds=timeout_seconds,
        )
        if lease is None:
            raise DevicePermitUnavailable(
                f"device capacity remained occupied for {model_class}"
            )
        stopped = threading.Event()
        lost = threading.Event()

        def renew_loop() -> None:
            interval = max(1.0, self.lease_seconds / 3.0)
            while not stopped.wait(interval):
                try:
                    if not self.renew(lease):
                        lost.set()
                        return
                except Exception:
                    _LOG.exception("device permit renewal failed device=%s class=%s", lease.device_id, model_class)
                    continue

        heartbeat = threading.Thread(
            target=renew_loop,
            name=f"omnix-device-permit-{model_class}",
            daemon=True,
        )
        heartbeat.start()
        try:
            yield lease
            if lost.is_set():
                raise DevicePermitError("device permit lease was lost during provider execution")
        finally:
            stopped.set()
            heartbeat.join(timeout=max(1.0, self.lease_seconds / 3.0))
            self.release(lease)


class _DeviceModelOwnerGuard:
    def __init__(
        self,
        service: PostgresDevicePermitService,
        lease: DeviceModelOwnerLease,
        *,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        self.service = service
        self.lease = lease
        self._on_lost = on_lost
        self._lost = False
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._renew_loop,
            name=f"omnix-model-owner-{lease.model_class}",
            daemon=True,
        )
        self._thread.start()

    def _renew_loop(self) -> None:
        interval = max(1.0, self.service.lease_seconds / 3.0)
        while not self._stopped.wait(interval):
            try:
                if not self.service.renew_model_owner(self.lease):
                    _LOG.error(
                        "model-owner lease lost device=%s class=%s holder=%s",
                        self.lease.device_id,
                        self.lease.model_class,
                        self.lease.holder_id,
                    )
                    self._notify_lost()
                    return
            except Exception:
                _LOG.exception(
                    "model-owner lease renewal failed device=%s class=%s",
                    self.lease.device_id,
                    self.lease.model_class,
                )
                continue

    def set_on_lost(self, callback: Callable[[], None] | None) -> bool:
        with self._lock:
            self._on_lost = callback
            lost = self._lost
        if lost and callback is not None:
            self._call_on_lost(callback)
        return not lost

    def _notify_lost(self) -> None:
        with self._lock:
            self._lost = True
            callback = self._on_lost
        if callback is None:
            return
        self._call_on_lost(callback)

    def _call_on_lost(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            _LOG.exception(
                "model-owner loss callback failed device=%s class=%s",
                self.lease.device_id,
                self.lease.model_class,
            )

    def close(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=max(1.0, self.service.lease_seconds / 3.0))
        self.service.release_model_owner(self.lease)


_DEFAULT_SERVICE: PostgresDevicePermitService | None = None
_DEFAULT_LOCK = threading.Lock()


def configure_default_device_permit_service(
    database: PostgresDatabase,
    *,
    device_id: str,
    capacities: dict[DeviceModelClass, tuple[int, int]],
    lease_seconds: int = 120,
    tts_model_owner: str | None = None,
) -> PostgresDevicePermitService:
    global _DEFAULT_SERVICE
    service = PostgresDevicePermitService(
        database,
        device_id=device_id,
        lease_seconds=lease_seconds,
        tts_model_owner=tts_model_owner,
    )
    for model_class, (capacity, reserved) in sorted(capacities.items()):
        service.configure_capacity(
            model_class,
            capacity=capacity,
            realtime_reserved_units=reserved,
        )
    with _DEFAULT_LOCK:
        _DEFAULT_SERVICE = service
    return service


def default_device_permit_service() -> PostgresDevicePermitService | None:
    return _DEFAULT_SERVICE


@contextmanager
def device_permit_slot(
    model_class: DeviceModelClass,
    *,
    priority: PermitPriority = "interactive",
    holder_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> Iterator[DevicePermitLease | None]:
    service = default_device_permit_service()
    if service is None:
        yield None
        return
    with service.slot(
        model_class,
        holder_id=holder_id or f"{os.getpid()}:{threading.get_ident()}:{uuid4().hex}",
        priority=priority,
        timeout_seconds=timeout_seconds,
    ) as lease:
        yield lease


__all__ = [
    "DeviceModelClass",
    "DevicePermitCapacity",
    "DevicePermitError",
    "DevicePermitHolder",
    "DevicePermitLease",
    "DevicePermitUnavailable",
    "DeviceModelOwnerLease",
    "PermitPriority",
    "PostgresDevicePermitService",
    "configure_default_device_permit_service",
    "default_device_permit_service",
    "device_permit_slot",
]
