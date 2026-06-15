import re
import unittest
from types import SimpleNamespace

from telegram.constants import ParseMode

from app.basic_handlers import start_handler
from app.config import Settings
from app.jav_patterns import DEFAULT_JAV_NAME_REGEX
from app.link_handlers import add_handler, text_link_handler
from app.runtime_state import runtime_context
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


class FakeApplication:
    def __init__(self, allowed_user_ids: list[int] | None = None) -> None:
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=allowed_user_ids or [1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
            ),
            "qbit": FakeQbit(),
            "jellyfin": FakeJellyfin(),
            "jav_name_pattern": re.compile(DEFAULT_JAV_NAME_REGEX),
            "bot_state": BotState(),
        }


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

    async def test_text_handler_replies_when_text_is_not_understood(self) -> None:
        app = FakeApplication()
        update = _update(text="hello world")

        await text_link_handler(update, _context(app))

        self.assertEqual(
            update.message.replies[0]["text"],
            "没有识别到下载链接或有效番号。",
        )

    async def test_status_handler_replies_with_qbit_status(self) -> None:
        app = FakeApplication()
        update = _update()

        await status_handler(update, _context(app))

        reply = update.message.replies[0]
        self.assertIn("qBittorrent 状态", reply["text"])
        self.assertIn("1.0 KB/s", reply["text"])
        self.assertEqual(reply["parse_mode"], ParseMode.HTML)
        self.assertIs(runtime_context(app).qbit, app.bot_data["qbit"])
