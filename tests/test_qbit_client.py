import asyncio
import unittest

import httpx

from app.qbit_client import QbitClient


class QbitClientLoginTests(unittest.IsolatedAsyncioTestCase):
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
