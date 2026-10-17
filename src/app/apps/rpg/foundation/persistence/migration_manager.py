from __future__ import annotations

from typing import Any, Dict

from .migrations import (
    migrate_v1_to_v2,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    migrate_v5_to_v6,
    migrate_v6_to_v7,
)
from .save_schema import CURRENT_RPG_SCHEMA_VERSION


def _safe_int(v: Any, default: int = 1) -> int:
    try:
        return int(v)
    except Exception:
        return default


def migrate_package_to_current(package: Dict[str, Any]) -> Dict[str, Any]:
    package = dict(package or {})
    version = _safe_int(package.get("schema_version"), 1)

    while version < CURRENT_RPG_SCHEMA_VERSION:
        prev_version = version
        if version == 1:
            package = migrate_v1_to_v2(package)
        elif version == 2:
            package = migrate_v2_to_v3(package)
        elif version == 3:
            package = migrate_v3_to_v4(package)
        elif version == 4:
            package = migrate_v4_to_v5(package)
        elif version == 5:
            package = migrate_v5_to_v6(package)
        elif version == 6:
            package = migrate_v6_to_v7(package)
        else:
            raise ValueError(f"Unsupported schema migration path from version {version}")
        version = _safe_int(package.get("schema_version"), version + 1)

        if version <= prev_version:
            raise ValueError(f"Migration did not advance schema version (stuck at {version})")

    return package