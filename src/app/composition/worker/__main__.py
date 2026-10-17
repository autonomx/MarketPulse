"""Run standalone PostgreSQL-backed durable job worker pools."""
from __future__ import annotations

import argparse
import asyncio
import logging
import threading
import time
from typing import Sequence

from app.config.env import environment, env_int, env_str
from app.runtime.config import GatewayRole, RuntimeConfig, install_runtime_config
from app.composition.worker_runtime.pool_runtime import JobWorkerPoolRuntime
from app.composition.worker_runtime.pools import DEFAULT_POOLS, parse_pools


logger = logging.getLogger("app.composition.worker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pools",
        default=environment().get("OMNIX_JOB_WORKER_POOLS", DEFAULT_POOLS),
        help="comma-separated resource pool limits, for example llm=2,image=1,tts=1,research=2,cpu=4",
    )
    parser.add_argument("--metrics-host", default=environment().get("OMNIX_JOB_WORKER_METRICS_HOST", "127.0.0.1"))
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=env_int("OMNIX_JOB_WORKER_METRICS_PORT", 8090, minimum=1, maximum=65535),
    )
    parser.add_argument("--managed-stdin", action="store_true", help=argparse.SUPPRESS)
    return parser


def _worker_runtime_config() -> RuntimeConfig:
    source = dict(environment())
    source["OMNIX_GATEWAY_BACKGROUND_ROLE"] = GatewayRole.JOB_WORKER.value
    source.pop("OMNIX_GATEWAY_OWNS_BACKGROUND_RUNTIME", None)
    source.pop("OMNIX_GATEWAY_ALLOW_LOCAL_TTS", None)
    config = RuntimeConfig.from_environment(source)
    if config.gateway_role is not GatewayRole.JOB_WORKER:
        raise RuntimeError("Standalone job workers require the job-worker runtime role")
    return config


async def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.composition.production import create_production_app
    from app.runtime.process_control import wait_for_parent_control
    from app.composition.worker.health import create_worker_health_app

    pools = parse_pools(args.pools)
    config = _worker_runtime_config()
    install_runtime_config(config)
    startup_deadline = time.monotonic() + env_int(
        "OMNIX_JOB_WORKER_STARTUP_TIMEOUT_SECONDS",
        120,
        minimum=1,
        maximum=3600,
    )
    while True:
        try:
            gateway = create_production_app(config=config)
            break
        except RuntimeError as exc:
            if str(exc) != "Production gateway requires ready PostgreSQL authority":
                raise
            if time.monotonic() >= startup_deadline:
                raise TimeoutError(
                    "PostgreSQL authority did not become ready before job worker startup timed out"
                ) from exc
            logger.info("Waiting for gateway migration/bootstrap before starting job pools")
            await asyncio.sleep(1)
    services = gateway.state.runtime_services
    runtime = JobWorkerPoolRuntime(
        services.jobs,
        gateway.state.job_handler_registry,
        pools,
        services=services,
        shutdown_grace_seconds=env_int(
            "OMNIX_JOB_WORKER_SHUTDOWN_GRACE_SECONDS",
            30,
            minimum=0,
            maximum=3600,
        ),
    )
    health_app = create_worker_health_app(runtime)
    server = uvicorn.Server(
        uvicorn.Config(
            health_app,
            host=args.metrics_host,
            port=args.metrics_port,
            log_level=(env_str("OMNIX_LOG_LEVEL", "info") or "info").lower(),
        )
    )

    async with gateway.router.lifespan_context(gateway):
        runtime.start()
        logger.info(
            "Standalone job worker ready pools=%s metrics=http://%s:%s",
            ",".join(f"{pool.name}={pool.concurrency}" for pool in pools),
            args.metrics_host,
            args.metrics_port,
        )
        if args.managed_stdin:
            def watch_parent() -> None:
                wait_for_parent_control()
                server.should_exit = True

            threading.Thread(
                target=watch_parent,
                name="job-worker-parent-control",
                daemon=True,
            ).start()
        try:
            await server.serve()
        finally:
            runtime.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from app.observability.logging import configure_logging

    configure_logging()
    from app.observability.tracing import configure_tracing

    configure_tracing(service_name="omnix-job-worker")
    try:
        parse_pools(args.pools)
        return asyncio.run(_serve(args))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
