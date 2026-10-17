"""Runtime gateway composition used by the local launcher."""
from __future__ import annotations

from app.composition.production import ProductionApplication, create_production_app


def create_runtime_app():
    return create_production_app()


app = ProductionApplication(factory=create_runtime_app)

__all__ = ["app"]
