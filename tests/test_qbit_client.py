import asyncio
import unittest

import httpx

from app.qbit_client import QbitClient


class QbitClientLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_configured_timeout(self) -> None:
        qbit = QbitClient("http://qbit.local", "user", "pass", timeout=4.5)
        try:
            self.assertEqual(qbit._client.timeout.connect, 4.5)
        finally:
            await qbit.close()

    async def test_concurrent_requests_share_one_login(self) -> None:
        login_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal login_calls
            if request.url.path == "/api/v2/auth/login":
                login_calls += 1
                await asyncio.sleep(0.01)
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/transfer/info":
                return httpx.Response(200, json={}, request=request)
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            await asyncio.gather(*(qbit.get_transfer_info() for _ in range(5)))
        finally:
            await qbit.close()

        self.assertEqual(login_calls, 1)

    async def test_list_categories_returns_sorted_names(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/categories":
                return httpx.Response(
                    200,
                    json={
                        "TV": {"name": "TV", "savePath": "/downloads/tv"},
                        "JAV": {"name": "JAV", "savePath": "/downloads/jav"},
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            categories = await qbit.list_categories()
        finally:
            await qbit.close()

        self.assertEqual([item.name for item in categories], ["JAV", "TV"])
        self.assertEqual(categories[0].save_path, "/downloads/jav")

    async def test_add_torrent_accepts_ok_response_body(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/add":
                return httpx.Response(200, text="Ok.", request=request)
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            await qbit.add_torrent_url_with_options("magnet:?xt=urn:btih:" + "a" * 40)
        finally:
            await qbit.close()

    async def test_add_torrent_rejects_fails_response_body(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/add":
                return httpx.Response(200, text="Fails.", request=request)
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            with self.assertRaisesRegex(RuntimeError, "添加任务失败"):
                await qbit.add_torrent_url_with_options(
                    "magnet:?xt=urn:btih:" + "a" * 40
                )
        finally:
            await qbit.close()

    async def test_add_torrent_accepts_json_success_response_body(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/add":
                return httpx.Response(
                    200,
                    json={
                        "added_torrent_ids": ["b92c008337a66291187252f70732153c979450c5"],
                        "failure_count": 0,
                        "pending_count": 0,
                        "success_count": 1,
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            await qbit.add_torrent_url_with_options("magnet:?xt=urn:btih:" + "a" * 40)
        finally:
            await qbit.close()

    async def test_add_torrent_treats_409_as_already_added(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/add":
                return httpx.Response(409, text="Torrent already exists", request=request)
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            await qbit.add_torrent_url_with_options("magnet:?xt=urn:btih:" + "a" * 40)
        finally:
            await qbit.close()

    async def test_create_category_treats_409_as_already_exists(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(
                    204,
                    headers={"set-cookie": "SID=test"},
                    request=request,
                )
            if request.url.path == "/api/v2/torrents/createCategory":
                return httpx.Response(409, request=request)
            return httpx.Response(404, request=request)

        qbit = QbitClient("http://qbit.local", "user", "pass")
        await qbit._client.aclose()
        qbit._client = httpx.AsyncClient(
            base_url="http://qbit.local",
            transport=httpx.MockTransport(handler),
            headers={"Referer": "http://qbit.local"},
            trust_env=False,
        )

        try:
            await qbit.create_category("JAV")
        finally:
            await qbit.close()
