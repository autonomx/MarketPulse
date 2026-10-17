from __future__ import annotations

import ast

from pathlib import Path

from app.apps.trading.models import AdjustmentMode, AssetClass, FeedType, InstrumentType, UsageScope


TRADING_ROUTES_PATH = Path("src/app/apps/trading/route_registration.py")


def test_string_enums_preserve_wire_values() -> None:
    values = (
        AssetClass.CRYPTO,
        InstrumentType.SPOT,
        FeedType.REST,
        UsageScope.PERSONAL_LOCAL,
        AdjustmentMode.RAW,
    )
    for value in values:
        assert isinstance(value, str)
        assert str(value) == value.value


def test_trading_uses_python311_stdlib_strenum() -> None:
    source = Path("src/app/apps/trading/models.py").read_text(encoding="utf-8")
    assert "from enum import StrEnum" in source
    assert "except ImportError" not in source


def test_trading_route_module_does_not_eagerly_import_trading_stack() -> None:
    tree = ast.parse(TRADING_ROUTES_PATH.read_text(encoding="utf-8"))
    eager_imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.apps.trading"):
            eager_imports.append(node.module)
        elif isinstance(node, ast.Import):
            eager_imports.extend(
                alias.name for alias in node.names if alias.name.startswith("app.apps.trading")
            )

    assert eager_imports == [], (
        "Trading gateway dependencies must stay lazy so lightweight gateway "
        f"submodule imports cannot initialize the Trading stack: {eager_imports}"
    )
