from __future__ import annotations

import ast
from pathlib import Path

TRADING_INIT = Path("src/app/apps/trading/__init__.py")


def test_trading_package_does_not_patch_stdlib_enum() -> None:
    tree = ast.parse(TRADING_INIT.read_text(encoding="utf-8"))
    foreign_enum_writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in {"enum", "_enum"}
            ):
                foreign_enum_writes.append(target.attr)
    assert foreign_enum_writes == []
