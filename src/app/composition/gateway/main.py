"""ASGI entry point; application composition lives in ``app_factory``."""

from app.composition.production import app
from app.config.env import environment

from .app_factory import create_gateway_app

DEFAULT_GATEWAY_PORT = 8000


if __name__ == "__main__":
    import uvicorn

    from app.runtime.net import bind_host

    host = bind_host()
    port = int(environment().get("OMNIX_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT)))
    uvicorn.run("app.composition.gateway.main:app", host=host, port=port, reload=False)
