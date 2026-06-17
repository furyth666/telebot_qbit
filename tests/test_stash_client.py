import unittest

import httpx

from app.stash_client import StashClient, StashScene


class StashClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_disables_environment_proxy(self) -> None:
        client = StashClient("http://stash.local:9999")
        try:
            self.assertFalse(client._client._trust_env)
        finally:
            await client.close()

    async def test_client_disabled_when_base_url_empty(self) -> None:
        client = StashClient("")
        try:
            self.assertFalse(client.enabled)
            scenes = await client.find_scenes_by_query("test")
            self.assertEqual(scenes, [])
        finally:
            await client.close()

    async def test_client_sends_api_key_header_when_configured(self) -> None:
        seen_requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "findScenes": {
                            "scenes": []
                        }
                    }
                },
                request=request,
            )

        client = StashClient("http://stash.local:9999", api_key="secret")
        headers = dict(client._client.headers)
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://stash.local:9999",
            headers=headers,
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

        try:
            await client.find_scenes_by_query("test query")
        finally:
            await client.close()

        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(
            seen_requests[0].headers.get("Authorization"),
            "ApiKey secret",
        )
        body = seen_requests[0].read()
        self.assertIn(b"findScenes", body)
        self.assertIn(b"test query", body)

    async def test_find_scenes_by_query_parses_response(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "findScenes": {
                            "scenes": [
                                {
                                    "id": "scene-1",
                                    "title": "Test Scene",
                                    "date": "2023-01-01",
                                    "studio": {"name": "Test Studio"},
                                    "performers": [
                                        {"name": "Performer One"},
                                        {"name": "Performer Two"},
                                    ],
                                    "files": [
                                        {"path": "/media/test.mp4"},
                                    ],
                                    "tags": [
                                        {"name": "tag-one"},
                                    ],
                                }
                            ]
                        }
                    }
                },
                request=request,
            )

        client = StashClient("http://stash.local:9999")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://stash.local:9999",
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

        try:
            scenes = await client.find_scenes_by_query("test")
        finally:
            await client.close()

        self.assertEqual(len(scenes), 1)
        scene = scenes[0]
        self.assertEqual(scene.scene_id, "scene-1")
        self.assertEqual(scene.title, "Test Scene")
        self.assertEqual(scene.date, "2023-01-01")
        self.assertEqual(scene.studio, "Test Studio")
        self.assertEqual(scene.performers, ("Performer One", "Performer Two"))
        self.assertEqual(scene.paths, ("/media/test.mp4",))
        self.assertEqual(scene.tags, ("tag-one",))


class StashSceneTests(unittest.TestCase):
    def test_scene_dataclass(self) -> None:
        scene = StashScene(
            scene_id="1",
            title="Title",
            date="2023-01-01",
            studio="Studio",
            performers=(),
            paths=(),
            tags=(),
        )
        self.assertEqual(scene.scene_id, "1")


if __name__ == "__main__":
    unittest.main()
