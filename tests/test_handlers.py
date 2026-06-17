import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.constants import ParseMode

from app.basic_handlers import start_handler
from app.config import Settings
from app.jav_patterns import DEFAULT_JAV_NAME_REGEX
from app.link_handlers import add_handler, stash_lookup_handler, text_link_handler
from app.add_types import AddBatchResult
from app.add_flow import AddLinksWorkflowResult
from app.runtime_state import runtime_context
from app.stash_client import StashScene
from app.state_store import BotState
from app.torrent_handlers import status_handler


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[dict] = []
        self.photos: list[dict] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append({"text": text, **kwargs})

    async def reply_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)


class FakeQbit:
    async def get_transfer_info(self) -> dict:
        return {
            "dl_info_speed": 1024,
            "up_info_speed": 2048,
            "dl_info_data": 1024 * 1024,
            "up_info_data": 2 * 1024 * 1024,
            "dht_nodes": 8,
            "connection_status": "connected",
        }


class FakeJellyfin:
    enabled = False


class FakeStash:
    def __init__(
        self,
        scenes: list[StashScene] | None = None,
        *,
        enabled: bool = False,
        screenshot_bytes: bytes | None = None,
    ) -> None:
        self.scenes = scenes or []
        self._enabled = enabled
        self.queries: list[str] = []
        self.screenshot_bytes = screenshot_bytes

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def find_scenes_by_query(self, query: str) -> list[StashScene]:
        self.queries.append(query)
        return self.scenes

    async def get_scene_screenshot_bytes(self, scene: StashScene) -> bytes | None:
        return self.screenshot_bytes


class FakeApplication:
    def __init__(
        self,
        allowed_user_ids: list[int] | None = None,
        *,
        stash: FakeStash | None = None,
        include_stash: bool = False,
    ) -> None:
        stash_enabled = bool(stash) or include_stash
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=allowed_user_ids or [1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
                stash_base_url="http://stash.local:9999" if stash_enabled else "",
            ),
            "qbit": FakeQbit(),
            "jellyfin": FakeJellyfin(),
            "jav_name_pattern": re.compile(DEFAULT_JAV_NAME_REGEX),
            "bot_state": BotState(),
        }
        if stash_enabled:
            self.bot_data["stash"] = stash or FakeStash()


def _context(app: FakeApplication, args: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(application=app, args=args or [])


def _update(
    *,
    user_id: int = 1,
    text: str = "",
    chat_id: int = 1,
) -> SimpleNamespace:
    message = FakeMessage(text)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        message=message,
        effective_chat=SimpleNamespace(id=chat_id),
    )


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_rejects_unauthorized_user(self) -> None:
        app = FakeApplication(allowed_user_ids=[1])
        update = _update(user_id=2)

        await start_handler(update, _context(app))

        self.assertEqual(update.message.replies[0]["text"], "无权限使用这个 bot。")

    async def test_start_replies_with_help_for_allowed_user(self) -> None:
        app = FakeApplication()
        update = _update()

        await start_handler(update, _context(app))

        self.assertIn("qBittorrent 管理 bot 已启动", update.message.replies[0]["text"])
        self.assertEqual(update.message.replies[0]["parse_mode"], ParseMode.HTML)

    async def test_add_handler_requires_arguments(self) -> None:
        app = FakeApplication()
        update = _update()

        await add_handler(update, _context(app))

        self.assertEqual(
            update.message.replies[0]["text"],
            "用法: /add <一个或多个 magnet/torrent 链接>",
        )

    async def test_text_jav_lookup_reports_disabled_jellyfin(self) -> None:
        app = FakeApplication()
        update = _update(text="SSIS-123")

        await text_link_handler(update, _context(app))

        self.assertEqual(update.message.replies[0]["text"], "Jellyfin 查询未启用。")

    async def test_text_jav_code_takes_priority_over_stash_lookup(self) -> None:
        stash = FakeStash(
            [
                StashScene(
                    scene_id="scene-1",
                    title="SSIS-123",
                    date="",
                    studio="",
                    performers=(),
                    paths=(),
                    tags=(),
                )
            ],
            enabled=True,
        )
        app = FakeApplication(stash=stash)
        update = _update(text="SSIS-123")

        await text_link_handler(update, _context(app))

        self.assertEqual(update.message.replies[0]["text"], "Jellyfin 查询未启用。")
        self.assertEqual(stash.queries, [])

    async def test_text_torrent_link_takes_priority_over_stash_lookup(self) -> None:
        stash = FakeStash(
            [
                StashScene(
                    scene_id="scene-1",
                    title="Scene with link",
                    date="",
                    studio="",
                    performers=(),
                    paths=(),
                    tags=(),
                )
            ],
            enabled=True,
        )
        app = FakeApplication(stash=stash)
        update = _update(text="Scene name magnet:?xt=urn:btih:" + "a" * 40)
        workflow_result = AddLinksWorkflowResult(
            links=["magnet:?xt=urn:btih:" + "a" * 40],
            batch=AddBatchResult(
                total_links=1,
                success_count=1,
                magnet_count=1,
                contexts=[],
                failures=[],
            ),
            reply_text="<b>added</b>",
        )

        with patch(
            "app.link_handlers.submit_add_links_from_text",
            new=AsyncMock(return_value=workflow_result),
        ) as submit:
            await text_link_handler(update, _context(app))

        submit.assert_awaited_once()
        self.assertEqual(stash.queries, [])
        self.assertEqual(update.message.replies[0]["text"], "<b>added</b>")
        self.assertEqual(update.message.replies[0]["parse_mode"], ParseMode.HTML)

    async def test_text_handler_replies_when_text_is_not_understood(self) -> None:
        app = FakeApplication()
        update = _update(text="hello world")

        await text_link_handler(update, _context(app))

        self.assertEqual(
            update.message.replies[0]["text"],
            "没有识别到下载链接、有效番号或 Stash 可查询的片名。",
        )

    async def test_text_handler_queries_stash_with_plain_scene_name(self) -> None:
        scene = StashScene(
            scene_id="scene-1",
            title="Some AV Scene",
            date="2024-01-02",
            studio="Studio",
            performers=("Actor One",),
            paths=("/media/Some AV Scene.mp4",),
            tags=(),
        )
        stash = FakeStash([scene], enabled=True)
        app = FakeApplication(stash=stash)
        update = _update(text="Some AV Scene")

        await text_link_handler(update, _context(app))

        self.assertEqual(stash.queries, ["Some AV Scene"])
        reply = update.message.replies[0]
        self.assertIn("Stash 查询结果", reply["text"])
        self.assertIn("Some AV Scene", reply["text"])
        self.assertEqual(reply["parse_mode"], ParseMode.HTML)

    async def test_text_handler_replies_with_stash_scene_screenshot(self) -> None:
        scene = StashScene(
            scene_id="scene-1",
            title="Some AV Scene",
            date="2024-01-02",
            studio="Studio",
            performers=("Actor One",),
            paths=("/media/Some AV Scene.mp4",),
            tags=(),
            screenshot_url="http://stash.local:9999/scene/scene-1/screenshot",
        )
        stash = FakeStash([scene], enabled=True, screenshot_bytes=b"image-bytes")
        app = FakeApplication(stash=stash)
        update = _update(text="Some AV Scene")

        await text_link_handler(update, _context(app))

        self.assertEqual(stash.queries, ["Some AV Scene"])
        self.assertEqual(update.message.replies, [])
        photo = update.message.photos[0]
        self.assertIn("Stash 查询结果", photo["caption"])
        self.assertIn("Some AV Scene", photo["caption"])
        self.assertEqual(photo["parse_mode"], ParseMode.HTML)

    async def test_stash_handler_reports_disabled_stash(self) -> None:
        app = FakeApplication(include_stash=True)
        update = _update()

        await stash_lookup_handler(update, _context(app, args=["hello", "world"]))

        self.assertEqual(update.message.replies[0]["text"], "Stash 查询未启用。")

    async def test_status_handler_replies_with_qbit_status(self) -> None:
        app = FakeApplication()
        update = _update()

        await status_handler(update, _context(app))

        reply = update.message.replies[0]
        self.assertIn("qBittorrent 状态", reply["text"])
        self.assertIn("1.0 KB/s", reply["text"])
        self.assertEqual(reply["parse_mode"], ParseMode.HTML)
        self.assertIs(runtime_context(app).qbit, app.bot_data["qbit"])
