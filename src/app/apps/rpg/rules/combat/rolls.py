from __future__ import annotations

import hashlib
from typing import Dict


def deterministic_d20(seed_key: str) -> Dict[str, int | str]:
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    value = (int(digest[:8], 16) % 20) + 1
    return {
        "roll_type": "d20",
        "sides": 20,
        "result": value,
        "seed_key": seed_key,
    }


def deterministic_damage_roll(seed_key: str, sides: int) -> Dict[str, int | str]:
    bounded_sides = max(1, int(sides or 1))
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    value = (int(digest[8:16], 16) % bounded_sides) + 1
    return {
        "roll_type": f"d{bounded_sides}",
        "sides": bounded_sides,
        "result": value,
        "seed_key": seed_key,
    }
