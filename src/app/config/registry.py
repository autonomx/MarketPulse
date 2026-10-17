"""Machine-readable environment variable registry."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    name: str
    type: str
    default: str | None
    feature: str
    description: str


_VARIABLES_PATH = Path(__file__).with_name("variables.json")
VARIABLES: tuple[EnvironmentVariable, ...] = tuple(
    EnvironmentVariable(**row)
    for row in json.loads(_VARIABLES_PATH.read_text(encoding="utf-8"))
)


def registry_payload() -> list[dict[str, str | None]]:
    return [asdict(item) for item in VARIABLES]


def render_configuration_markdown() -> str:
    lines = [
        "# Omnix configuration",
        "",
        "This file is generated from `app.config.registry`. Values are intentionally never emitted.",
        "",
        "| Variable | Type | Default | Owner | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in VARIABLES:
        default = f"`{item.default}`" if item.default not in (None, "") else "—"
        lines.append(
            f"| `{item.name}` | {item.type} | {default} | {item.feature} | {item.description} |"
        )
    return "\n".join(lines) + "\n"
