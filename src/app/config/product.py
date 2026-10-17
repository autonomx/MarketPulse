"""The product name shown in user-visible text such as alert emails and notifications (MarketPulse S-3)."""
from __future__ import annotations

from app.config.env import env_str


def product_name() -> str:
    """``OMNIX_PRODUCT_NAME``, or "Omnix" when unset; internal identifiers never use it."""
    return (env_str("OMNIX_PRODUCT_NAME", "") or "").strip() or "Omnix"
