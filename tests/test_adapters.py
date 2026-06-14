import json
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.jellyfin_client import JellyfinClient
from app.llm_classifier import classify_torrent
from app.qbit_client import TorrentCategory, TorrentSummary


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_allowed_user_ids": [1],
        "qbit_base_url": "http://qbit",
        "qbit_username": "user",
        "qbit_password": "pass",
        "llm_api_key": "llm-token",
    }
    values.update(overrides)
    return Settings(**values)


def make_torrent() -> TorrentSummary:
    return TorrentSummary(
        name="SSIS-123",
        hash="a" * 40,
        category="",
        state="downloading",
        progress=0,
        dlspeed=0,
        upspeed=0,
        eta=0,
        size=100,
        completion_on=0,
        added_on=100,
    )


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class RecordingAsyncClient:
    calls: list[dict] = []
    posts: list[tuple[str, dict]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, path: str, **kwargs) -> FakeResponse:
        self.posts.append((path, kwargs))
        if path == "/api/chat":
            return FakeResponse(
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "category": "JAV",
                                "confidence": 0.91,
                                "reason": "matched product code",
                            }
                        )
                    }
                }
            )
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "category": "JAV",
                                    "confidence": 0.88,
                                    "reason": "matched product code",
                                }
                            )
                        }
                    }
                ]
            }
        )


class JellyfinAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_jellyfin_client_disables_environment_proxy(self) -> None:
        client = JellyfinClient("http://jellyfin.local", "api-key")
        try:
            self.assertFalse(client._client._trust_env)
        finally:
            await client.close()

    async def test_find_by_code_parses_people_and_uses_api_request_shape(self) -> None:
        seen_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append(request)
            self.assertEqual(request.headers["X-Emby-Token"], "api-key")
            self.assertEqual(request.url.path, "/Items")
            self.assertEqual(request.url.params["SearchTerm"], "SSIS-123")
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "item-1",
                            "ServerId": "server-1",
                            "Name": "SSIS-123",
                            "Path": "/media/SSIS-123.mkv",
                            "Overview": "overview",
                            "ProductionYear": 2024,
                            "PremiereDate": "2024-01-02T00:00:00.000Z",
                            "People": [
                                {"Id": "actor-1", "Name": " Actor ", "Type": "Actor"},
                                {"Id": "director-1", "Name": "Director", "Type": "Director"},
                            ],
                        }
                    ]
                },
                request=request,
            )

        client = JellyfinClient("http://jellyfin.local", "api-key")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://jellyfin.local",
            headers={"X-Emby-Token": "api-key"},
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

        try:
            items = await client.find_by_code("SSIS-123")
        finally:
            await client.close()

        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(items[0].item_id, "item-1")
        self.assertEqual(items[0].actors[0].name, "Actor")
        self.assertEqual(len(items[0].actors), 1)

    async def test_list_media_identity_texts_reads_names_and_paths(self) -> None:
        seen_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append(request)
            self.assertEqual(request.url.path, "/Items")
            self.assertEqual(request.url.params["Recursive"], "true")
            self.assertEqual(request.url.params["IncludeItemTypes"], "Movie,Episode,Video")
            self.assertEqual(request.url.params["Limit"], "2")
            self.assertEqual(request.url.params["Fields"], "Path")
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {"Name": "SSIS-123", "Path": "/media/SSIS-123.mkv"},
                        {"Name": "ABP-456", "Path": "ABP-456"},
                    ]
                },
                request=request,
            )

        client = JellyfinClient("http://jellyfin.local", "api-key")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://jellyfin.local",
            headers={"X-Emby-Token": "api-key"},
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

        try:
            texts = await client.list_media_identity_texts(limit=2)
        finally:
            await client.close()

        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(texts, ["SSIS-123", "/media/SSIS-123.mkv", "ABP-456"])


class LlmAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        RecordingAsyncClient.calls = []
        RecordingAsyncClient.posts = []

    async def test_openai_compatible_request_disables_environment_proxy(self) -> None:
        settings = make_settings(llm_api_base_url="https://api.openai.com/v1")

        with patch("app.llm_classifier.httpx.AsyncClient", RecordingAsyncClient):
            decision = await classify_torrent(
                settings,
                make_torrent(),
                files=[],
                categories=[TorrentCategory("JAV", "/downloads/jav")],
            )

        self.assertEqual(decision.category, "JAV")
        self.assertFalse(RecordingAsyncClient.calls[0]["trust_env"])
        self.assertEqual(RecordingAsyncClient.posts[0][0], "/chat/completions")

    async def test_llm_jav_guidance_uses_configured_regex(self) -> None:
        settings = make_settings(
            jav_name_regex=r"(?i)CUSTOM[-_.\s]*\d{3}",
            llm_api_base_url="https://api.openai.com/v1",
        )

        with patch("app.llm_classifier.httpx.AsyncClient", RecordingAsyncClient):
            await classify_torrent(
                settings,
                make_torrent(),
                files=[],
                categories=[TorrentCategory("JAV", "/downloads/jav")],
            )

        request_json = RecordingAsyncClient.posts[0][1]["json"]
        guidance = request_json["messages"][1]["content"]
        self.assertIn(r"(?i)CUSTOM[-_.\s]*\d{3}", guidance)
        self.assertIn("source of truth", guidance)
        self.assertNotIn("IPX, IPZZ, SNOS", guidance)

    async def test_llm_jav_guidance_includes_jellyfin_prefixes(self) -> None:
        settings = make_settings(llm_api_base_url="https://api.openai.com/v1")

        with patch("app.llm_classifier.httpx.AsyncClient", RecordingAsyncClient):
            await classify_torrent(
                settings,
                make_torrent(),
                files=[],
                categories=[TorrentCategory("JAV", "/downloads/jav")],
                jav_prefixes=["SSIS", "FC2-PPV"],
            )

        request_json = RecordingAsyncClient.posts[0][1]["json"]
        guidance = request_json["messages"][1]["content"]
        self.assertIn("Jellyfin currently contains", guidance)
        self.assertIn("SSIS, FC2-PPV", guidance)

    async def test_local_ollama_request_disables_environment_proxy(self) -> None:
        settings = make_settings(llm_api_base_url="http://127.0.0.1:11434/v1")

        with patch("app.llm_classifier.httpx.AsyncClient", RecordingAsyncClient):
            decision = await classify_torrent(
                settings,
                make_torrent(),
                files=[],
                categories=[TorrentCategory("JAV", "/downloads/jav")],
            )

        self.assertEqual(decision.category, "JAV")
        self.assertEqual(str(RecordingAsyncClient.calls[0]["base_url"]), "http://127.0.0.1:11434")
        self.assertFalse(RecordingAsyncClient.calls[0]["trust_env"])
        self.assertEqual(RecordingAsyncClient.posts[0][0], "/api/chat")
