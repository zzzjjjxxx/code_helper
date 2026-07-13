from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class MemoryEntry:
    key: str
    value: str


@dataclass(slots=True)
class MemoryStore:
    root: Path
    entries: dict[str, MemoryEntry] = field(default_factory=dict)

    def put(self, key: str, value: str) -> None:
        self.entries[key] = MemoryEntry(key=key, value=value)

    def get(self, key: str) -> str | None:
        entry = self.entries.get(key)
        return entry.value if entry else None

    def all(self) -> Iterable[MemoryEntry]:
        return self.entries.values()
