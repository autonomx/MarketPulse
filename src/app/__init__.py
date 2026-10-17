"""Omnix application package. Production callers use the shared PostgreSQL gateway."""
from __future__ import annotations


def create_fastapi_app():
    """Create a provider-free gateway for explicit tests and dependency injection."""
    from app.composition.gateway.main import create_gateway_app

    return create_gateway_app()


def create_app():
    """Create the production PostgreSQL-authoritative gateway."""
    from app.composition.production import create_production_app

    return create_production_app()
