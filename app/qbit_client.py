from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

__all__ = [
    "QbitClient",
    "TorrentCategory",
    "TorrentFile",
    "TorrentProperties",
    "TorrentSummary",
]


@dataclass
class TorrentSummary:
    name: str
    hash: str
    category: str
    state: str
    progress: float
    dlspeed: int
    upspeed: int
    eta: int
    size: int
    completion_on: int
    added_on: int


@dataclass
class TorrentFile:
    index: int
    name: str
    size: int
    priority: int


@dataclass
class TorrentProperties:
    save_path: str
    share_ratio: float
    total_uploaded: int


@dataclass(frozen=True)
class TorrentCategory:
    name: str
    save_path: str


class QbitClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        api_token: str = "",
    ) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
        self.api_token = api_token
        self._api_token_failed = False
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=20.0,
            headers={"Referer": base_url},
            trust_env=False,
        )
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _ensure_login(self) -> None:
        if self._logged_in:
            return

        async with self._login_lock:
            if self._logged_in:
                return

            if self.api_token and not self._api_token_failed:
                self._client.headers["Authorization"] = f"Bearer {self.api_token}"
                self._logged_in = True
                return

            self._client.headers.pop("Authorization", None)
            response = await self._client.post(
                "/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
            )
            response.raise_for_status()

            login_accepted = (
                response.text.strip() == "Ok."
                or (
                    response.status_code == 204
                    and bool(response.headers.get("set-cookie"))
                )
            )
            if not login_accepted:
                raise RuntimeError("qBittorrent 登录失败，请检查账号密码或 WebUI 配置。")

            self._logged_in = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        await self._ensure_login()
        response = await self._client.request(method, path, params=params, data=data)
        if response.status_code in {401, 403}:
            if self.api_token and not self._api_token_failed:
                self._api_token_failed = True
            self._logged_in = False
            await self._ensure_login()
            response = await self._client.request(method, path, params=params, data=data)
        response.raise_for_status()
        return response

    async def _request_with_fallbacks(
        self,
        method: str,
        paths: list[str],
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_error: httpx.HTTPStatusError | None = None
        for path in paths:
            try:
                return await self._request(method, path, params=params, data=data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_error = exc
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("qBittorrent 请求失败，未找到可用接口。")

    async def get_transfer_info(self) -> dict[str, Any]:
        response = await self._request("GET", "/api/v2/transfer/info")
        return response.json()

    def _parse_torrent_summary(self, item: dict[str, Any]) -> TorrentSummary:
        return TorrentSummary(
            name=item["name"],
            hash=item["hash"],
            category=item.get("category", ""),
            state=item["state"],
            progress=float(item.get("progress", 0)),
            dlspeed=int(item.get("dlspeed", 0)),
            upspeed=int(item.get("upspeed", 0)),
            eta=int(item.get("eta", 0)),
            size=int(item.get("size", 0)),
            completion_on=int(item.get("completion_on", 0)),
            added_on=int(item.get("added_on", 0)),
        )

    async def list_torrents(self, *, filter_name: str = "all") -> list[TorrentSummary]:
        response = await self._request(
            "GET",
            "/api/v2/torrents/info",
            params={"filter": filter_name, "sort": "added_on", "reverse": "true"},
        )
        items = response.json()
        return [self._parse_torrent_summary(item) for item in items]

    async def get_torrent(self, torrent_hash: str) -> TorrentSummary | None:
        response = await self._request(
            "GET",
            "/api/v2/torrents/info",
            params={"hashes": torrent_hash},
        )
        items = response.json()
        for item in items:
            if item.get("hash", "").lower() == torrent_hash.lower():
                return self._parse_torrent_summary(item)
        return None

    async def resolve_torrent(self, hash_prefix: str) -> TorrentSummary:
        torrents = await self.list_torrents(filter_name="all")
        matched = [
            item for item in torrents if item.hash.lower().startswith(hash_prefix.lower())
        ]
        if not matched:
            raise ValueError("没有找到对应的任务 hash。")
        if len(matched) > 1:
            raise ValueError("匹配到多个任务，请提供更长一点的 hash。")
        return matched[0]

    async def get_torrent_properties(self, torrent_hash: str) -> TorrentProperties:
        response = await self._request(
            "GET",
            "/api/v2/torrents/properties",
            params={"hash": torrent_hash},
        )
        item = response.json()
        return TorrentProperties(
            save_path=str(item.get("save_path", "")),
            share_ratio=float(item.get("share_ratio", 0)),
            total_uploaded=int(item.get("total_uploaded", 0)),
        )

    async def pause_torrent(self, torrent_hash: str) -> None:
        await self._request_with_fallbacks(
            "POST",
            ["/api/v2/torrents/stop", "/api/v2/torrents/pause"],
            data={"hashes": torrent_hash},
        )

    async def resume_torrent(self, torrent_hash: str) -> None:
        await self._request_with_fallbacks(
            "POST",
            ["/api/v2/torrents/start", "/api/v2/torrents/resume"],
            data={"hashes": torrent_hash},
        )

    async def delete_torrent(self, torrent_hash: str, *, delete_files: bool) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": str(delete_files).lower()},
        )

    async def add_torrent_url(self, url: str) -> None:
        await self.add_torrent_url_with_options(url)

    async def add_torrent_url_with_options(
        self,
        url: str,
        *,
        upload_limit: int | None = None,
        category: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"urls": url}
        if upload_limit is not None:
            data["upLimit"] = upload_limit
        if category:
            data["category"] = category
        await self._request(
            "POST",
            "/api/v2/torrents/add",
            data=data,
        )

    async def create_category(self, name: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/createCategory",
            data={"category": name},
        )

    async def set_category(self, torrent_hash: str, category: str) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/setCategory",
            data={"hashes": torrent_hash, "category": category},
        )

    async def list_categories(self) -> list[TorrentCategory]:
        response = await self._request("GET", "/api/v2/torrents/categories")
        payload = response.json()
        return [
            TorrentCategory(
                name=str(item.get("name") or name),
                save_path=str(item.get("savePath", "")),
            )
            for name, item in sorted(payload.items())
        ]

    async def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        response = await self._request(
            "GET",
            "/api/v2/torrents/files",
            params={"hash": torrent_hash},
        )
        items = response.json()
        return [
            TorrentFile(
                index=int(item["index"]),
                name=item["name"],
                size=int(item["size"]),
                priority=int(item.get("priority", 1)),
            )
            for item in items
        ]

    async def set_file_priority(
        self,
        torrent_hash: str,
        file_ids: list[int],
        priority: int,
    ) -> None:
        if not file_ids:
            return
        await self._request(
            "POST",
            "/api/v2/torrents/filePrio",
            data={
                "hash": torrent_hash,
                "id": "|".join(str(file_id) for file_id in file_ids),
                "priority": priority,
            },
        )

    async def resolve_hash(self, hash_prefix: str) -> str:
        return (await self.resolve_torrent(hash_prefix)).hash
