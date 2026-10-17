"""Launcher composition for on-demand models and live voice services."""
from __future__ import annotations

from app.config.env import environment

from dataclasses import replace
from pathlib import Path

from app.composition.launcher import control_app
from app.composition.launcher.service_manager import (
    LauncherServiceManager,
    build_default_service_specs,
    reset_default_manager_for_tests,
)

app = control_app.app
IMAGE_SERVICE_URL = "http://127.0.0.1:5301"
LIVE_STT_URL = "/api/stt?language=en&authority=auto&endpoint_threshold=0.5"


def build_runtime_service_specs():
    root = Path(__file__).resolve().parents[4]
    specs = []

    for spec in build_default_service_specs(root):
        if spec.service_id == "stt":
            specs.append(
                replace(
                    spec,
                    label="Nemotron + Parakeet EOU STT",
                    command=[spec.command[0], "-m", "services.stt.nemotron_eou_stt_server"],
                    description=(
                        "Nemotron transcript + Parakeet EOU websocket service "
                        "on 127.0.0.1:5201."
                    ),
                )
            )
            continue
        if spec.service_id == "gateway":
            image_enabled = environment().get("OMNIX_IMAGE_ENABLED", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            image_start = environment().get("OMNIX_START_IMAGE_SERVICE", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            image_available = image_enabled and image_start
            specs.append(
                replace(
                    spec,
                    env={
                        **spec.env,
                        "OMNIX_IMAGE_ENABLED": "1" if image_available else "0",
                        "OMNIX_IMAGE_URL": IMAGE_SERVICE_URL if image_available else "",
                        "OMNIX_LIVE_LMSTUDIO_STATEFUL_RESPONSES": environment().get(
                            "OMNIX_LIVE_LMSTUDIO_STATEFUL_RESPONSES",
                            "true",
                        ),
                        "OMNIX_LIVE_TTS_SPECULATIVE_CHUNK_STEPS": environment().get(
                            "OMNIX_LIVE_TTS_SPECULATIVE_CHUNK_STEPS",
                            "2",
                        ),
                    },
                )
            )
            continue
        if spec.service_id == "web":
            specs.append(
                replace(
                    spec,
                    env={
                        **spec.env,
                        "VITE_ASSISTANT_STT_URL": environment().get(
                            "VITE_ASSISTANT_STT_URL",
                            LIVE_STT_URL,
                        ),
                        "VITE_LIVE_SPECULATION_ENABLED": environment().get(
                            "VITE_LIVE_SPECULATION_ENABLED",
                            "true",
                        ),
                        "VITE_LIVE_TTS_ADAPTIVE_BUFFER": environment().get(
                            "VITE_LIVE_TTS_ADAPTIVE_BUFFER",
                            "true",
                        ),
                    },
                    description=(
                        "React/Vite browser app on 127.0.0.1:5173. "
                        "Live voice uses Nemotron transcript + Parakeet EOU on port 5201."
                    ),
                )
            )
            continue
        if spec.service_id == "image":
            specs.append(
                replace(
                    spec,
                    env={
                        **spec.env,
                        "OMNIX_IMAGE_ENABLED": "1" if spec.enabled else "0",
                        "OMNIX_IMAGE_SERVICE_MODE": "1",
                        "OMNIX_IMAGE_PRELOAD": environment().get("OMNIX_IMAGE_PRELOAD", "0"),
                        "OMNIX_IMAGE_WARMUP": environment().get("OMNIX_IMAGE_WARMUP", "0"),
                        "OMNIX_IMAGE_REQUIRE_EXPLICIT_LOAD": environment().get(
                            "OMNIX_IMAGE_REQUIRE_EXPLICIT_LOAD",
                            "1",
                        ),
                        "OMNIX_IMAGE_URL": "",
                    },
                    description=(
                        "Lightweight image service on 127.0.0.1:5301. "
                        "FLUX.2 [klein] 4B stays unloaded until requested from the web UI."
                    ),
                )
            )
            continue
        specs.append(spec)

    return specs


reset_default_manager_for_tests(LauncherServiceManager(build_runtime_service_specs()))

__all__ = ["app", "build_runtime_service_specs"]
