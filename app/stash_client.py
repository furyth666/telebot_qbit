from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

__all__ = [
    "StashClient",
    "StashScene",
]


@dataclass(frozen=True)
class StashScene:
    scene_id: str
    title: str
    date: str
    studio: str
    performers: tuple[str, ...]
    paths: tuple[str, ...]
    tags: tuple[str, ...]


class StashClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            trust_env=False,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def find_scenes_by_query(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[StashScene]:
        if not self.enabled:
            return []

        request_payload = {
            "query": _FIND_SCENES_QUERY,
            "variables": {
                "filter": {
                    "q": query,
                    "per_page": limit,
                },
                "scene_filter": {},
            },
        }
        response = await self._client.post(
            "/graphql",
            json=request_payload,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        find_scenes = data.get("findScenes") or {}
        scenes = find_scenes.get("scenes") or []
        return [_parse_scene(scene) for scene in scenes]


def _parse_scene(scene: dict[str, Any]) -> StashScene:
    title = _str_or_empty(scene.get("title"))
    date = _str_or_empty(scene.get("date"))
    studio_obj = scene.get("studio") or {}
    studio = _str_or_empty(studio_obj.get("name"))
    performers = tuple(
        _str_or_empty(performer.get("name"))
        for performer in scene.get("performers") or []
        if _str_or_empty(performer.get("name"))
    )
    paths = tuple(
        _str_or_empty(media_file.get("path"))
        for media_file in scene.get("files") or []
        if _str_or_empty(media_file.get("path"))
    )
    tags = tuple(
        _str_or_empty(tag.get("name"))
        for tag in scene.get("tags") or []
        if _str_or_empty(tag.get("name"))
    )
    return StashScene(
        scene_id=_str_or_empty(scene.get("id")),
        title=title,
        date=date,
        studio=studio,
        performers=performers,
        paths=paths,
        tags=tags,
    )


def _str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


_FIND_SCENES_QUERY = """
query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
  findScenes(filter: $filter, scene_filter: $scene_filter) {
    scenes {
      id
      title
      date
      studio {
        name
      }
      performers {
        name
      }
      files {
        path
      }
      tags {
        name
      }
    }
  }
}
"""
