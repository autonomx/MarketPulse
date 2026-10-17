"""Private HTTP readiness and Prometheus metrics for job worker pools."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse


def create_worker_health_app(runtime: Any) -> FastAPI:
    app = FastAPI(
        title="Omnix Job Worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        diagnostics = runtime.diagnostics()
        ready = bool(diagnostics["ready"])
        return JSONResponse(
            status_code=200 if ready else 503,
            content=diagnostics,
        )

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        lines = [
            "# HELP omnix_job_worker_pool_ready Whether a configured resource pool is ready.",
            "# TYPE omnix_job_worker_pool_ready gauge",
            "# HELP omnix_job_worker_pool_active_jobs Jobs currently executing in a pool.",
            "# TYPE omnix_job_worker_pool_active_jobs gauge",
            "# HELP omnix_job_worker_pool_concurrency_limit Configured pool execution concurrency.",
            "# TYPE omnix_job_worker_pool_concurrency_limit gauge",
            "# HELP omnix_job_worker_pool_claimed_total Jobs claimed by the pool.",
            "# TYPE omnix_job_worker_pool_claimed_total counter",
            "# HELP omnix_job_worker_pool_completed_total Terminal jobs reported by the pool.",
            "# TYPE omnix_job_worker_pool_completed_total counter",
            "# HELP omnix_job_worker_pool_failures_total Poll or handler failures reported by the pool.",
            "# TYPE omnix_job_worker_pool_failures_total counter",
        ]
        for name, pool in runtime.diagnostics()["pools"].items():
            label = name.replace("\\", "\\\\").replace('"', '\\"')
            labels = f'pool="{label}"'
            lines.extend(
                (
                    f"omnix_job_worker_pool_ready{{{labels}}} {int(pool['ready'])}",
                    f"omnix_job_worker_pool_active_jobs{{{labels}}} {pool['active_jobs']}",
                    f"omnix_job_worker_pool_concurrency_limit{{{labels}}} {pool['max_concurrency']}",
                    f"omnix_job_worker_pool_claimed_total{{{labels}}} {pool['claim_count']}",
                    f"omnix_job_worker_pool_completed_total{{{labels}}} {pool['completed_count']}",
                    f"omnix_job_worker_pool_failures_total{{{labels}}} {pool['failure_count']}",
                )
            )
        from app.observability.metrics import exposition

        # Then the process registry: job execution times and provider calls.
        return "\n".join(lines) + "\n" + exposition()[0].decode()

    return app


__all__ = ["create_worker_health_app"]

