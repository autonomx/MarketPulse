"""Governed TP-Link Kasa runtime adapter for local smart plugs."""
from __future__ import annotations

import logging

from app.config.env import env_str, environment

import asyncio
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .models import AssistantToolRequest, AssistantToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KasaRuntimeConfig:
    enabled: bool = False
    host: str | None = None
    alias: str | None = None
    discovery_target: str = "255.255.255.255"
    timeout_seconds: float = 4.0
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class KasaDeviceRecord:
    alias: str
    host: str
    model: str
    device_id: str
    is_on: bool
    rssi: int | None = None


class KasaRuntimeAdapter(Protocol):
    def discover_devices(self) -> list[KasaDeviceRecord]: ...

    def get_state(self, *, target: str = "") -> KasaDeviceRecord: ...

    def set_state(self, *, target: str = "", on: bool) -> tuple[KasaDeviceRecord, KasaDeviceRecord]: ...


class PythonKasaRuntimeAdapter:
    def __init__(self, config: KasaRuntimeConfig | None = None) -> None:
        self.config = config or kasa_runtime_config()

    def discover_devices(self) -> list[KasaDeviceRecord]:
        return _run_async(self._discover_records(), timeout=self.config.timeout_seconds + 3.0)

    def get_state(self, *, target: str = "") -> KasaDeviceRecord:
        return _run_async(self._get_state(target), timeout=self.config.timeout_seconds + 3.0)

    def set_state(self, *, target: str = "", on: bool) -> tuple[KasaDeviceRecord, KasaDeviceRecord]:
        return _run_async(self._set_state(target, on), timeout=self.config.timeout_seconds + 5.0)

    async def _discover_records(self) -> list[KasaDeviceRecord]:
        devices = await self._discover()
        records: list[KasaDeviceRecord] = []
        try:
            for device in devices:
                await device.update()
                records.append(_record(device))
            return sorted(records, key=lambda item: (item.alias.casefold(), item.host))
        finally:
            await _disconnect_all(devices)

    async def _get_state(self, target: str) -> KasaDeviceRecord:
        devices = await self._discover()
        try:
            device = _select_device(devices, target=target, configured_alias=self.config.alias)
            await device.update()
            return _record(device)
        finally:
            await _disconnect_all(devices)

    async def _set_state(self, target: str, on: bool) -> tuple[KasaDeviceRecord, KasaDeviceRecord]:
        devices = await self._discover()
        try:
            device = _select_device(devices, target=target, configured_alias=self.config.alias)
            await device.update()
            before = _record(device)
            if on:
                await device.turn_on()
            else:
                await device.turn_off()
            await device.update()
            after = _record(device)
            if after.is_on is not on:
                raise RuntimeError("Kasa state verification failed")
            return before, after
        finally:
            await _disconnect_all(devices)

    async def _discover(self) -> list[Any]:
        if not self.config.enabled:
            raise RuntimeError("Kasa integration is disabled")
        try:
            from kasa import Discover
        except ImportError as exc:
            raise RuntimeError(
                "python-kasa is missing from the locked image runtime; rerun setup.bat or setup.sh"
            ) from exc
        timeout = max(1, int(math.ceil(self.config.timeout_seconds)))
        auth = _auth_kwargs(self.config)
        if self.config.host:
            device = await Discover.discover_single(
                self.config.host,
                discovery_timeout=timeout,
                timeout=timeout,
                **auth,
            )
            if device is None:
                raise RuntimeError(f"No Kasa device responded at {self.config.host}")
            return [device]
        discovered = await Discover.discover(
            target=self.config.discovery_target,
            discovery_timeout=timeout,
            timeout=timeout,
            **auth,
        )
        devices = list(discovered.values())
        if not devices:
            raise RuntimeError("No Kasa devices were discovered on the local network")
        return devices


def kasa_runtime_config() -> KasaRuntimeConfig:
    return KasaRuntimeConfig(
        enabled=_flag("OMNIX_KASA_ENABLED"),
        host=_optional("OMNIX_KASA_DEVICE_HOST"),
        alias=_optional("OMNIX_KASA_DEVICE_ALIAS"),
        discovery_target=_optional("OMNIX_KASA_DISCOVERY_TARGET") or "255.255.255.255",
        timeout_seconds=_float("OMNIX_KASA_TIMEOUT_SECONDS", 4.0, 1.0, 15.0),
        username=_optional("KASA_USERNAME"),
        password=_optional("KASA_PASSWORD"),
    )


def run_kasa_tool_request(
    request: AssistantToolRequest,
    adapter: KasaRuntimeAdapter | None = None,
) -> AssistantToolResult:
    runtime = adapter or PythonKasaRuntimeAdapter()
    target = str(
        request.input.get("target")
        or request.input.get("alias")
        or request.input.get("host")
        or ""
    ).strip()
    try:
        if request.action_id == "kasa.discover_devices":
            devices = runtime.discover_devices()
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="low",
                state_changed=False,
                result_summary=f"Discovered {len(devices)} Kasa device{'s' if len(devices) != 1 else ''}.",
                output={"devices": [asdict(device) for device in devices]},
            )
        if request.action_id == "kasa.get_state":
            device = runtime.get_state(target=target)
            state = "on" if device.is_on else "off"
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="low",
                state_changed=False,
                result_summary=f"{device.alias} is {state}.",
                output={"device": asdict(device)},
            )
        if request.action_id in {"kasa.turn_on", "kasa.turn_off"}:
            desired = request.action_id == "kasa.turn_on"
            before, after = runtime.set_state(target=target, on=desired)
            state = "on" if after.is_on else "off"
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="medium",
                state_changed=before.is_on != after.is_on,
                result_summary=f"Verified {after.alias} is {state}.",
                output={"before": asdict(before), "after": asdict(after), "verified": True},
            )
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            error="kasa_action_not_available",
        )
    except Exception as exc:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level="medium" if request.action_id in {"kasa.turn_on", "kasa.turn_off"} else "low",
            state_changed=False,
            result_summary="Kasa action failed.",
            error=str(exc)[:500],
        )


def _record(device: Any) -> KasaDeviceRecord:
    return KasaDeviceRecord(
        alias=str(getattr(device, "alias", None) or getattr(device, "host", "Kasa device")),
        host=str(getattr(device, "host", "")),
        model=str(getattr(device, "model", "unknown")),
        device_id=str(getattr(device, "device_id", "")),
        is_on=bool(getattr(device, "is_on", False)),
        rssi=_optional_int(getattr(device, "rssi", None)),
    )


def _select_device(devices: list[Any], *, target: str, configured_alias: str | None) -> Any:
    desired = _normalize(target or configured_alias or "")
    if not desired:
        if len(devices) == 1:
            return devices[0]
        aliases = ", ".join(str(getattr(item, "alias", getattr(item, "host", "unknown"))) for item in devices)
        raise RuntimeError(f"Multiple Kasa devices found; specify one of: {aliases}")
    for device in devices:
        values = {
            _normalize(getattr(device, "alias", "")),
            _normalize(getattr(device, "host", "")),
            _normalize(getattr(device, "device_id", "")),
        }
        if desired in values or any(desired in value or value in desired for value in values if value):
            return device
    raise RuntimeError(f"No Kasa device matched '{target or configured_alias}'")


async def _disconnect_all(devices: list[Any]) -> None:
    for device in devices:
        disconnect = getattr(device, "disconnect", None)
        if disconnect is None:
            continue
        try:
            await disconnect()
        except Exception:
            logger.debug("suppressed error in %s", "_disconnect_all", exc_info=True)


def _run_async(coro: Any, *, timeout: float) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="omnix-kasa") as executor:
        return executor.submit(asyncio.run, coro).result(timeout=timeout)


def _auth_kwargs(config: KasaRuntimeConfig) -> dict[str, str]:
    values: dict[str, str] = {}
    if config.username:
        values["username"] = config.username
    if config.password:
        values["password"] = config.password
    return values


def _normalize(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").split())


def _optional(name: str) -> str | None:
    value = environment().get(name, "").strip()
    return value or None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _flag(name: str, default: bool = False) -> bool:
    value = environment().get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(environment().get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))
