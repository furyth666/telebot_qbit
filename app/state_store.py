from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BotState:
    notified_completed_hashes: set[str] = field(default_factory=set)
    jav_processed_hashes: set[str] = field(default_factory=set)
    jellyfin_duplicate_codes: dict[str, int] = field(default_factory=dict)


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.state = BotState()

    def load(self) -> BotState:
        if not self.path.exists():
            return self.state

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.state = BotState(
            notified_completed_hashes=set(payload.get("notified_completed_hashes", [])),
            jav_processed_hashes=set(payload.get("jav_processed_hashes", [])),
            jellyfin_duplicate_codes={
                str(key): int(value)
                for key, value in payload.get("jellyfin_duplicate_codes", {}).items()
            },
        )
        return self.state

    def save(self, state: BotState | None = None) -> None:
        current = state or self.state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "notified_completed_hashes": sorted(current.notified_completed_hashes),
            "jav_processed_hashes": sorted(current.jav_processed_hashes),
            "jellyfin_duplicate_codes": current.jellyfin_duplicate_codes,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
