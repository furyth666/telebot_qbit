from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class JellyfinItem:
    item_id: str
    name: str
    path: str
    overview: str
    production_year: int | None
    premiere_date: str


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
                "Fields": "Path,Overview,ProductionYear,PremiereDate",
            },
        )
        response.raise_for_status()
        payload = response.json()
        return [
            JellyfinItem(
                item_id=str(item.get("Id", "")),
                name=str(item.get("Name", "")),
                path=str(item.get("Path", "")),
                overview=str(item.get("Overview", "")),
                production_year=(
                    int(item["ProductionYear"])
                    if item.get("ProductionYear") is not None
                    else None
                ),
                premiere_date=str(item.get("PremiereDate", "")),
            )
            for item in payload.get("Items", [])
        ]

    async def get_primary_image_bytes(self, item_id: str, *, max_width: int = 720) -> bytes | None:
        if not self.enabled or not item_id:
            return None
        response = await self._client.get(
            f"/Items/{item_id}/Images/Primary",
            params={"maxWidth": str(max_width), "quality": "90"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content
