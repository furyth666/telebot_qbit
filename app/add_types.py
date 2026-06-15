from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AddBatchResult",
    "AddContext",
]


@dataclass(frozen=True)
class AddContext:
    known_hashes: set[str]
    started_at: int
    name_hint: str | None
    is_magnet: bool = False
    expected_hashes: set[str] | None = None


@dataclass(frozen=True)
class AddBatchResult:
    total_links: int
    success_count: int
    magnet_count: int
    contexts: list[AddContext]
    failures: list[str]
