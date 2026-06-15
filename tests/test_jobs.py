import re
import unittest
from unittest.mock import patch

from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.config import Settings
from app.jav_patterns import DEFAULT_JAV_NAME_REGEX
from app.category_flow import (
    apply_manual_category_choice,
    auto_apply_llm_category_after_delay,
    category_choice_keyboard,
    category_choices,
)
from app.jobs import background_finalize_torrent
from app.add_links import AddContext
from app.jellyfin_client import JellyfinItem
from app.llm_classifier import (
    LlmCategoryDecision,
    _ollama_native_base_url,
    _strip_source_markers,
)
from app.qbit_client import TorrentCategory, TorrentFile, TorrentSummary
from app.runtime_state import runtime_context
from app.state_store import BotState


def _torrent(name: str, torrent_hash: str = "a" * 40) -> TorrentSummary:
    return TorrentSummary(
        name=name,
        hash=torrent_hash,
        category="",
        state="downloading",
        progress=0,
        dlspeed=0,
        upspeed=0,
        eta=0,
        size=0,
        completion_on=0,
        added_on=100,
    )


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        if kwargs.get("parse_mode") == ParseMode.HTML and "<hash>" in kwargs.get("text", ""):
            raise BadRequest("Can't parse entities: unsupported start tag \"hash\"")
        self.messages.append(kwargs)


class FakeStateStore:
    def __init__(self) -> None:
        self.save_calls = 0

    async def save_async(self, state: BotState) -> None:
        self.save_calls += 1


class FakeJellyfin:
    def __init__(
        self,
        items: list[JellyfinItem] | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self.items = items or []
        self._enabled = enabled
        self.queries: list[str] = []
        self.identity_text_calls = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def find_by_code(self, code: str) -> list[JellyfinItem]:
        self.queries.append(code)
        return self.items

    async def list_media_identity_texts(self, *, limit: int = 300) -> list[str]:
        self.identity_text_calls += 1
        texts: list[str] = []
        for item in self.items:
            texts.extend([item.name, item.path])
        return texts


class FakeQbit:
    def __init__(
        self,
        torrents: list[TorrentSummary],
        *,
        files: list[TorrentFile] | None = None,
        fail_small_file_priority: bool = False,
        lock_to_check=None,
    ) -> None:
        self.torrents = torrents
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str, str]] = []
        self.deleted_torrents: list[tuple[str, bool]] = []
        self.files = files or [
            TorrentFile(index=0, name="movie.mp4", size=2 * 1024 * 1024 * 1024, priority=1)
        ]
        self.fail_small_file_priority = fail_small_file_priority
        self.lock_to_check = lock_to_check
        self.lock_was_held_during_set = False

    async def list_torrents(self, *, filter_name: str = "all") -> list[TorrentSummary]:
        return self.torrents

    async def list_categories(self) -> list[TorrentCategory]:
        return [
            TorrentCategory(name="AV", save_path="/downloads/av"),
            TorrentCategory(name="JAV", save_path="/downloads/jav"),
            TorrentCategory(name="TV", save_path="/downloads/tv"),
        ]

    async def create_category(self, category: str) -> None:
        self.created_categories.append(category)

    async def set_category(self, torrent_hash: str, category: str) -> None:
        if self.lock_to_check is not None:
            self.lock_was_held_during_set = self.lock_to_check.locked()
        self.set_categories.append((torrent_hash, category))

    async def delete_torrent(self, torrent_hash: str, *, delete_files: bool) -> None:
        self.deleted_torrents.append((torrent_hash, delete_files))

    async def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        return self.files

    async def set_file_priority(
        self,
        torrent_hash: str,
        file_indexes: list[int],
        priority: int,
    ) -> None:
        if self.fail_small_file_priority and priority == 0:
            raise RuntimeError("file metadata changed")


class FakeApplication:
    def __init__(
        self,
        *,
        jellyfin: FakeJellyfin | None = None,
        jellyfin_duplicate_delete_enabled: bool = False,
        llm_classify_enabled: bool = False,
    ) -> None:
        self.bot = FakeBot()
        self.state_store = FakeStateStore()
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
                jellyfin_duplicate_delete_enabled=jellyfin_duplicate_delete_enabled,
                llm_classify_enabled=llm_classify_enabled,
                llm_api_key="llm-key" if llm_classify_enabled else "",
                llm_auto_apply_delay_seconds=0,
            ),
            "jav_name_pattern": re.compile(DEFAULT_JAV_NAME_REGEX),
            "bot_state": BotState(),
            "state_store": self.state_store,
            "jellyfin": jellyfin or FakeJellyfin(),
        }


def _add_context() -> AddContext:
    return AddContext(
        known_hashes=set(),
        started_at=100,
        name_hint=None,
        is_magnet=True,
    )


def _jellyfin_item(code: str = "SSIS-123", *, path_suffix: str = "") -> JellyfinItem:
    return JellyfinItem(
        item_id="item-1",
        server_id="server-1",
        name=code,
        path=f"/media/{code}{path_suffix}.mp4",
        overview="",
        production_year=None,
        premiere_date="",
        actors=(),
    )


class CategoryPromptTests(unittest.TestCase):
    def test_category_choices_include_uncategorized_first(self) -> None:
        choices = category_choices(
            [
                TorrentCategory(name="AV", save_path="/downloads/av"),
                TorrentCategory(name="JAV", save_path="/downloads/jav"),
                TorrentCategory(name="TV", save_path="/downloads/tv"),
            ]
        )

        self.assertEqual(choices, ["", "AV", "JAV", "TV"])

    def test_category_keyboard_uses_hash_and_index_payloads(self) -> None:
        torrent_hash = "a" * 40
        keyboard = category_choice_keyboard(torrent_hash, ["", "AV", "JAV", "TV"])
        payloads = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]

        self.assertEqual(
            payloads,
            [
                f"tor:cat:all:{torrent_hash}:0",
                f"tor:cat:all:{torrent_hash}:1",
                f"tor:cat:all:{torrent_hash}:2",
                f"tor:cat:all:{torrent_hash}:3",
            ],
        )
        self.assertTrue(all(len(item.encode("utf-8")) <= 64 for item in payloads))


class ManualCategoryChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_manual_category_choice_sets_category_and_clears_state(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])
        torrent_hash = "a" * 40
        context = runtime_context(app)
        context.pending_category_choices[torrent_hash] = ["", "AV", "JAV", "TV"]
        context.prompted_category_hashes.add(torrent_hash)

        choice = await apply_manual_category_choice(
            app,
            qbit,
            torrent_hash=torrent_hash,
            category_index=2,
        )

        self.assertIsNotNone(choice)
        self.assertEqual(choice.category, "JAV")
        self.assertEqual(choice.label, "JAV")
        self.assertEqual(qbit.set_categories, [(torrent_hash, "JAV")])
        self.assertFalse(qbit.lock_was_held_during_set)
        self.assertNotIn(torrent_hash, context.pending_category_choices)
        self.assertNotIn(torrent_hash, context.prompted_category_hashes)

    async def test_apply_manual_category_choice_does_not_hold_lock_during_qbit_call(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)
        qbit = FakeQbit(
            [_torrent("The.Show.S01E01.mkv")],
            lock_to_check=context.category_prompt_lock(),
        )
        torrent_hash = "a" * 40
        context.pending_category_choices[torrent_hash] = ["", "AV"]
        context.prompted_category_hashes.add(torrent_hash)

        choice = await apply_manual_category_choice(
            app,
            qbit,
            torrent_hash=torrent_hash,
            category_index=1,
        )

        self.assertIsNotNone(choice)
        self.assertFalse(qbit.lock_was_held_during_set)

    async def test_apply_manual_category_choice_rejects_expired_index(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])
        torrent_hash = "a" * 40
        runtime_context(app).pending_category_choices[torrent_hash] = ["", "AV"]

        choice = await apply_manual_category_choice(
            app,
            qbit,
            torrent_hash=torrent_hash,
            category_index=3,
        )

        self.assertIsNone(choice)
        self.assertEqual(qbit.set_categories, [])


class LlmClassifierTests(unittest.TestCase):
    def test_local_ollama_v1_url_uses_native_base_url(self) -> None:
        self.assertEqual(
            _ollama_native_base_url("http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434",
        )
        self.assertEqual(
            _ollama_native_base_url("http://localhost:11434/v1"),
            "http://localhost:11434",
        )
        self.assertIsNone(_ollama_native_base_url("https://api.openai.com/v1"))

    def test_strip_source_markers_removes_javdb_noise(self) -> None:
        self.assertEqual(
            _strip_source_markers("[JAVdb.com] SSIS-123-C.mp4"),
            "SSIS-123-C.mp4",
        )
        self.assertEqual(
            _strip_source_markers("JAVDB SSIS-123-C"),
            "SSIS-123-C",
        )


class FinalizeTorrentTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_disabled_torrent_gets_category_prompt(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("ubuntu-24.04-live-server.iso")])

        await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.created_categories, [])
        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("请选择移动到哪个分类", app.bot.messages[0]["text"])
        self.assertIn("reply_markup", app.bot.messages[0])

    async def test_finalize_notifies_when_new_torrent_cannot_be_located_without_name_hint(self) -> None:
        app = FakeApplication()
        runtime_context(app).settings = Settings(
            telegram_bot_token="token",
            telegram_allowed_user_ids=[1],
            qbit_base_url="http://qbit",
            qbit_username="user",
            qbit_password="pass",
            add_context_poll_attempts=1,
            add_context_poll_interval_seconds=0.001,
        )
        qbit = FakeQbit([])

        await background_finalize_torrent(
            app,
            qbit,
            AddContext(
                known_hashes=set(),
                started_at=100,
                name_hint=None,
                is_magnet=True,
                expected_hashes={"a" * 40},
            ),
            chat_id=1,
        )

        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("暂时没有定位到新任务", app.bot.messages[0]["text"])

    async def test_jav_torrent_uses_duplicate_policy_before_category_prompt(self) -> None:
        app = FakeApplication(
            jellyfin=FakeJellyfin([_jellyfin_item("SSIS-123")], enabled=True),
            jellyfin_duplicate_delete_enabled=True,
        )
        qbit = FakeQbit([_torrent("SSIS-123")])

        await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.deleted_torrents, [("a" * 40, False)])
        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("Jellyfin 已存在同番号短片", app.bot.messages[0]["text"])

    async def test_llm_applies_valid_high_confidence_category(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="TV",
                confidence=0.92,
                reason="episode naming",
            ),
        ) as classifier:
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        classifier.assert_awaited_once()
        self.assertEqual(qbit.set_categories, [("a" * 40, "TV")])
        self.assertEqual(len(app.bot.messages), 2)
        self.assertIn("大模型推荐分类", app.bot.messages[0]["text"])
        self.assertIn("推荐: <code>TV</code>", app.bot.messages[0]["text"])
        self.assertIn("reply_markup", app.bot.messages[0])
        self.assertIn("已按大模型推荐自动分类", app.bot.messages[1]["text"])
        self.assertIn("分类: <code>TV</code>", app.bot.messages[1]["text"])

    async def test_llm_recommendation_schedules_auto_apply_after_delay(
        self,
    ) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        runtime_context(app).settings = Settings(
            telegram_bot_token="token",
            telegram_allowed_user_ids=[1],
            qbit_base_url="http://qbit",
            qbit_username="user",
            qbit_password="pass",
            llm_classify_enabled=True,
            llm_api_key="llm-key",
            llm_auto_apply_delay_seconds=30,
        )
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])

        with patch("app.category_flow.classify_torrent", return_value=LlmCategoryDecision(
            category="TV",
            confidence=0.92,
            reason="episode naming",
        )) as classifier:
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        classifier.assert_awaited_once()
        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("大模型推荐分类", app.bot.messages[0]["text"])
        self.assertIn("请在 <code>30</code> 秒内手动选择", app.bot.messages[0]["text"])
        self.assertIn("llm_auto_apply_tasks", app.bot_data)

    async def test_llm_auto_apply_skips_when_manual_choice_was_made(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])
        torrent_hash = "a" * 40
        runtime_context(app).pending_category_choices[torrent_hash] = ["", "AV", "JAV", "TV"]
        runtime_context(app).pending_category_choices.pop(torrent_hash)

        await auto_apply_llm_category_after_delay(
            app,
            qbit,
            torrent_hash=torrent_hash,
            torrent_name="The.Show.S01E01.mkv",
            category="TV",
            confidence=0.92,
            delay_seconds=0,
            chat_id=1,
        )

        self.assertEqual(qbit.set_categories, [])

    async def test_llm_auto_apply_sets_category_when_no_manual_choice(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="TV",
                confidence=0.92,
                reason="episode naming",
            ),
        ):
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.set_categories, [("a" * 40, "TV")])
        self.assertNotIn("a" * 40, runtime_context(app).pending_category_choices)
        self.assertNotIn("a" * 40, runtime_context(app).prompted_category_hashes)

    async def test_llm_classification_uses_jellyfin_extracted_jav_prefixes(self) -> None:
        jellyfin = FakeJellyfin(
            [
                _jellyfin_item("SSIS-123"),
                _jellyfin_item("FC2-PPV-1234567"),
            ],
            enabled=True,
        )
        app = FakeApplication(llm_classify_enabled=True, jellyfin=jellyfin)
        qbit = FakeQbit([_torrent("new-download.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="TV",
                confidence=0.92,
                reason="episode naming",
            ),
        ) as classifier:
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        classifier.assert_awaited_once()
        self.assertEqual(jellyfin.identity_text_calls, 1)
        self.assertEqual(
            classifier.await_args.kwargs["jav_prefixes"],
            ["SSIS", "FC2-PPV"],
        )

    async def test_llm_javdb_source_marker_is_ignored_before_llm_classification(
        self,
    ) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("[JAVdb.com] IPZZ-744-C.torrent")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="JAV",
                confidence=0.99,
                reason="Japanese adult product code after ignoring source marker",
            ),
        ) as classifier:
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        classifier.assert_not_awaited()
        self.assertEqual(qbit.created_categories, ["JAV"])
        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("已识别 JAV 并移动到", app.bot.messages[0]["text"])
        self.assertIn("番号: <code>IPZZ-744</code>", app.bot.messages[0]["text"])

    async def test_llm_jav_decision_without_javdb_marker_keeps_jav_category(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("JAV-release-example.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="JAV",
                confidence=0.99,
                reason="belongs to standalone JAV category",
            ),
        ):
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])
        self.assertEqual(len(app.bot.messages), 2)
        self.assertIn("推荐: <code>JAV</code>", app.bot.messages[0]["text"])
        self.assertIn("分类: <code>JAV</code>", app.bot.messages[1]["text"])

    async def test_llm_low_confidence_falls_back_to_category_prompt(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("unclear-download")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="TV",
                confidence=0.5,
                reason="unclear",
            ),
        ):
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("大模型没有给出可靠分类", app.bot.messages[0]["text"])
        self.assertIn("reply_markup", app.bot.messages[0])

    async def test_llm_invalid_category_falls_back_to_category_prompt(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("movie.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="Movies",
                confidence=0.95,
                reason="movie title",
            ),
        ):
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("大模型没有给出可靠分类", app.bot.messages[0]["text"])
        self.assertIn("reply_markup", app.bot.messages[0])

    async def test_auto_applied_llm_choice_clears_prompt_state_for_future_processing(self) -> None:
        app = FakeApplication(llm_classify_enabled=True)
        qbit = FakeQbit([_torrent("The.Show.S01E01.mkv")])

        with patch(
            "app.category_flow.classify_torrent",
            return_value=LlmCategoryDecision(
                category="TV",
                confidence=0.92,
                reason="episode naming",
            ),
        ) as classifier:
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)
            await background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(classifier.await_count, 2)
        self.assertEqual(len(app.bot.messages), 4)
        self.assertEqual(qbit.set_categories, [("a" * 40, "TV"), ("a" * 40, "TV")])
        self.assertNotIn("a" * 40, runtime_context(app).prompted_category_hashes)
