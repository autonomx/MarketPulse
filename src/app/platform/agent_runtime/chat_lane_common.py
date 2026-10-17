"""What every Chat execution lane returns (WP-8.2).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GeneralizedChatResult:
    content: str
    metadata: dict[str, Any]


_TERMINAL_AGENT = {"completed", "failed", "cancelled"}
