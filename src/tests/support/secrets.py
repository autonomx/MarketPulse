"""In-memory secret store for tests (WP-4.9)."""
from __future__ import annotations


class MemorySecretStore:
    name = "memory"
    writable = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self.values)
