"""HTTP client for the external image generation service."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict

from app.security.service_token import service_headers


_LOG = logging.getLogger(__name__)
_NDJSON = "application/x-ndjson"

ProgressCallback = Callable[[int, int, str], None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_image_generation_enabled() -> bool:
    """Return whether image generation is explicitly enabled.

    The lightweight image service can be started without loading model weights.
    ``OMNIX_IMAGE_ENABLED`` controls whether model load and generation actions
    are allowed.
    """

    return _truthy(environment().get("OMNIX_IMAGE_ENABLED", "0"))


def _image_service_url() -> str:
    if not is_image_generation_enabled():
        return ""
    configured = environment().get("OMNIX_IMAGE_URL", "").strip()
    return (configured or "http://127.0.0.1:5301").rstrip("/")


def _launcher_control_url() -> str:
    configured = environment().get("OMNIX_LAUNCHER_URL", "").strip()
    return (configured or "http://127.0.0.1:5055").rstrip("/")


def is_image_service_enabled() -> bool:
    return bool(_image_service_url())


def image_disabled_response(source: str = "app") -> Dict[str, Any]:
    return {
        "ok": False,
        "provider": "disabled",
        "status": "disabled",
        "error": "image_generation_disabled",
        "asset_url": "",
        "local_path": "",
        "seed": None,
        "width": 0,
        "height": 0,
        "mime_type": "",
        "metadata": {
            "source": source,
            "disabled_by": "OMNIX_IMAGE_ENABLED",
            "enable_hint": "Set OMNIX_IMAGE_ENABLED=1 to enable image generation.",
        },
    }


def _request_json(
    method: str,
    url: str,
    payload: Dict[str, Any] | None = None,
    timeout: float = 600.0,
    *, authenticated: bool = False,
) -> Dict[str, Any]:
    method = method.strip().upper() or "GET"
    body = None
    headers = {"Accept": "application/json", "X-Omnix-Client": "gateway"}
    if authenticated:
        headers.update(service_headers())
    if method != "GET":
        body = json.dumps(payload or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        open_request = (urllib.request.build_opener(_NoRedirect()).open
                        if authenticated else urllib.request.urlopen)
        with open_request(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"http_{exc.code}:{raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"unreachable:{exc}") from exc


def request_image_service(
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    base = _image_service_url()
    if not base:
        raise RuntimeError("image_service_not_configured")

    try:
        return _request_json(method, f"{base}{path}", payload, timeout, authenticated=True)
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("http_"):
            raise RuntimeError(f"image_service_{message}") from exc
        raise RuntimeError(f"image_service_{message}") from exc


def post_image_service(path: str, payload: Dict[str, Any] | None = None, timeout: float = 600.0) -> Dict[str, Any]:
    return request_image_service("POST", path, payload, timeout)


def get_image_service_status(provider: str = "") -> Dict[str, Any]:
    provider = str(provider or "").strip().lower()
    query = f"?provider={urllib.parse.quote(provider, safe='')}" if provider else ""
    return request_image_service("GET", f"/provider/status{query}", timeout=5.0)


def _image_service_ready() -> bool:
    """Probe only FastAPI readiness; do not scan model files during startup."""

    payload = request_image_service("GET", "/openapi.json", timeout=2.0)
    return bool(payload.get("openapi"))


def start_image_service_via_launcher(
    provider: str = "flux_klein",
    *,
    startup_timeout: float = 30.0,
) -> Dict[str, Any]:
    """Start the lightweight image service and wait until FastAPI is reachable.

    Starting the service does not download a model or load weights into memory.
    The launcher remains the process owner so service logs and stop/restart actions
    continue to work from the launcher dashboard.
    """

    if not is_image_generation_enabled():
        return {
            "ok": False,
            "provider": provider,
            "loaded": False,
            "state": "disabled",
            "error": "image_generation_disabled",
        }

    # The service may already be running while Launcher Control is unavailable
    # or restarting. In that case there is nothing to start and lifecycle
    # actions should continue directly against the healthy service.
    try:
        if _image_service_ready():
            return {
                "ok": True,
                "service": "image",
                "provider": provider,
                "loaded": False,
                "state": "ready",
                "started": False,
                "already_running": True,
            }
    except RuntimeError:
        pass

    launcher_url = _launcher_control_url()
    if not launcher_url:
        return {
            "ok": False,
            "provider": provider,
            "loaded": False,
            "state": "unavailable",
            "error": "launcher_not_configured",
        }

    try:
        launcher_result = _request_json(
            "POST",
            f"{launcher_url}/api/services/image/start",
            {},
            timeout=10.0,
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "provider": provider,
            "loaded": False,
            "state": "unavailable",
            "error": f"launcher_{exc}",
        }

    if not launcher_result.get("ok"):
        return {
            "ok": False,
            "provider": provider,
            "loaded": False,
            "state": "unavailable",
            "error": launcher_result.get("error") or "image_service_start_failed",
            "launcher": launcher_result,
        }

    deadline = time.monotonic() + max(1.0, float(startup_timeout))
    last_error = "image_service_unreachable"
    while time.monotonic() < deadline:
        try:
            if _image_service_ready():
                return {
                    "ok": True,
                    "service": "image",
                    "provider": provider,
                    "loaded": False,
                    "state": "unloaded",
                    "started": True,
                    "launcher": launcher_result,
                }
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(0.25)

    return {
        "ok": False,
        "provider": provider,
        "loaded": False,
        "state": "unavailable",
        "error": f"image_service_start_timeout:{last_error}",
        "launcher": launcher_result,
    }


def download_image_model_via_service(
    provider: str = "flux_klein",
    hf_token: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"provider": provider}
    token = str(hf_token or "").strip()
    if token:
        payload["hf_token"] = token
    return post_image_service("/provider/download", payload, timeout=7200.0)


def load_image_model_via_service(provider: str = "flux_klein") -> Dict[str, Any]:
    return post_image_service("/provider/load", {"provider": provider}, timeout=900.0)


def unload_image_model_via_service(provider: str = "flux_klein") -> Dict[str, Any]:
    return post_image_service("/provider/unload", {"provider": provider}, timeout=120.0)


def generate_image_via_service(
    payload: Dict[str, Any], *, on_progress: ProgressCallback | None = None,
) -> Dict[str, Any]:
    """Generate on the image service; ``on_progress`` receives its step events."""
    if not is_image_generation_enabled():
        return image_disabled_response(source="image_http_client")

    request_payload = dict(payload if isinstance(payload, dict) else {})
    reference_asset_ids = request_payload.get("reference_asset_ids")
    if isinstance(reference_asset_ids, (list, tuple)) and reference_asset_ids:
        from app.platform.image.reference_transport import REFERENCE_IMAGES_PAYLOAD_KEY, encode_reference_assets

        if not request_payload.get(REFERENCE_IMAGES_PAYLOAD_KEY):
            request_payload[REFERENCE_IMAGES_PAYLOAD_KEY] = encode_reference_assets(reference_asset_ids)

    if on_progress is None:
        return post_image_service("/generate", request_payload, timeout=900.0)
    return _generate_streaming(request_payload, on_progress)


def _generate_streaming(payload: dict[str, Any], on_progress: ProgressCallback) -> dict[str, Any]:
    """POST /generate as NDJSON: step events while it runs, then the result."""
    base = _image_service_url()
    if not base:
        raise RuntimeError("image_service_not_configured")
    headers = {"Accept": _NDJSON, "Content-Type": "application/json",
               "X-Omnix-Client": "gateway", **service_headers()}
    req = urllib.request.Request(f"{base}/generate", data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=900.0) as resp:
            for line in resp:
                if not line.strip():
                    continue
                event = json.loads(line)
                kind = event.pop("event", "")
                if kind == "result":
                    return event
                if kind == "error":
                    raise RuntimeError(f"image_service_http_500:{event.get('error') or 'model_service_error'}")
                if kind == "progress":
                    _report(on_progress, event)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"image_service_http_{exc.code}:{raw}") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"image_service_unreachable:{exc}") from exc
    raise RuntimeError("image_service_unreachable:the stream ended without a result")


def _report(on_progress: ProgressCallback, event: dict[str, Any]) -> None:
    # Progress is advisory: a failed report must not abandon the generation.
    try:
        on_progress(int(event.get("current") or 0), int(event.get("total") or 1),
                    str(event.get("message") or ""))
    except Exception:
        _LOG.warning("image progress report failed", exc_info=True)
