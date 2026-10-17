"""Start the Omnix gateway with PostgreSQL authority established first."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--app", default="app.composition.production:app")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--api-replicas", type=int, default=None)
    parser.add_argument("--managed-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify PostgreSQL startup and exit without serving",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    from app.runtime.net import bind_host

    args.host = bind_host(args.host)

    if args.check:
        from app.persistence.startup import bootstrap_status_payload

        print(json.dumps(bootstrap_status_payload(), sort_keys=True))
        return 0

    import uvicorn
    from app.observability.logging import configure_logging
    from app.security.service_credentials import initialize_run_token_key, initialize_service_token

    # Structured logs (OMNIX_LOG_FORMAT, OMNIX_LOG_LEVEL) for this process and
    # every replica, which runs this script too.
    configure_logging()
    initialize_service_token()
    # Run tokens get their own key, never one derived from the service token
    # that model services also hold. Replicas and the job worker inherit it.
    os.environ["OMNIX_RUN_TOKEN_KEY"] = initialize_run_token_key()

    from gateway_cluster import (
        replica_ports,
        serve_cluster,
        start_local_job_worker,
        stop_children,
        watch_parent_stdin,
    )

    count = args.api_replicas
    if count is None:
        configured = os.environ.get("OMNIX_GATEWAY_API_REPLICAS")
        path = Path(__file__).resolve().parents[1] / "resources/data/gateway-deployment.json"
        if configured is None and path.exists():
            configured = json.loads(path.read_text(encoding="utf-8")).get("api_replicas", 0)
        count = int(configured or 0)
    replica_ports(args.port, count)
    if count:
        return serve_cluster(args, count)
    job_worker = start_local_job_worker(Path(__file__).resolve().parents[1], os.environ)
    try:
        if args.reload:
            uvicorn.run(args.app, host=args.host, port=args.port, reload=True)
            return 0
        from app.runtime.drain import create_draining_server

        # SIGTERM drains first (WP-6.7); the short graceful timeout only covers
        # what is still open after the drain window.
        server = create_draining_server(
            uvicorn.Config(
                args.app,
                host=args.host,
                port=args.port,
                timeout_graceful_shutdown=5,
                # Outlive the ingress's pooled upstream connections (Nginx
                # keeps them 60 s): if uvicorn closes first, Nginx can reuse a
                # dying connection and return 502 without retrying.
                timeout_keep_alive=75,
            )
        )
        if args.managed_stdin:
            watch_parent_stdin(server)
        server.run()
        return 0
    finally:
        if job_worker is not None:
            stop_children([job_worker])


if __name__ == "__main__":
    raise SystemExit(main())
