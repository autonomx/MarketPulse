"""Stable UTF-8 and canonical JSON identities shared by source and render layers."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def object_hash(value: Any) -> str:
    return text_hash(canonical_json(value))
