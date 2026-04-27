from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class JellyfinItem:
    item_id: str
    name: str
    path: str


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=20.0,
            headers={"X-Emby-Token": api_key},
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def close(self) -> None:
        await self._client.aclose()

    async def find_by_code(self, code: str) -> list[JellyfinItem]:
        if not self.enabled:
            return []
        response = await self._client.get(
            "/Items",
            params={
                "Recursive": "true",
                "SearchTerm": code,
                "IncludeItemTypes": "Movie,Episode,Video",
                "Limit": "10",
                "Fields": "Path",
            },
        )
        response.raise_for_status()
        payload = response.json()
        return [
            JellyfinItem(
                item_id=str(item.get("Id", "")),
                name=str(item.get("Name", "")),
                path=str(item.get("Path", "")),
            )
            for item in payload.get("Items", [])
        ]
