import os
import unittest
from unittest.mock import patch

from app.config import Settings, _split_user_ids


class ConfigTests(unittest.TestCase):
    def test_split_user_ids_deduplicates_while_preserving_order(self) -> None:
        self.assertEqual(_split_user_ids("1, 2, 1,3"), [1, 2, 3])

    def test_validate_reports_multiple_errors(self) -> None:
        settings = Settings(
            telegram_bot_token="",
            telegram_allowed_user_ids=[],
            qbit_base_url="",
            qbit_username="",
            qbit_password="",
            jav_large_file_threshold_gb=0,
            llm_min_confidence=2,
        )

        with self.assertRaises(ValueError) as caught:
            settings.validate()

        message = str(caught.exception)
        self.assertIn("TELEGRAM_BOT_TOKEN 不能为空", message)
        self.assertIn("TELEGRAM_ALLOWED_USER_IDS 至少需要配置一个用户 ID", message)
        self.assertIn("JAV_LARGE_FILE_THRESHOLD_GB 必须大于 0", message)
        self.assertIn("LLM_MIN_CONFIDENCE 必须在 0 到 1 之间", message)

    def test_from_env_parses_new_poll_settings(self) -> None:
        env = {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_IDS": "1,1,2",
            "QBIT_BASE_URL": "http://qbit/",
            "QBIT_USERNAME": "user",
            "QBIT_PASSWORD": "pass",
            "JAV_FILE_POLL_ATTEMPTS": "4",
            "JAV_FILE_POLL_INTERVAL_SECONDS": "0.25",
            "ADD_CONTEXT_POLL_ATTEMPTS": "5",
            "ADD_CONTEXT_POLL_INTERVAL_SECONDS": "0.5",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.telegram_allowed_user_ids, [1, 2])
        self.assertEqual(settings.qbit_base_url, "http://qbit")
        self.assertEqual(settings.jav_file_poll_attempts, 4)
        self.assertEqual(settings.jav_file_poll_interval_seconds, 0.25)
        self.assertEqual(settings.add_context_poll_attempts, 5)
        self.assertEqual(settings.add_context_poll_interval_seconds, 0.5)

    def test_validate_rejects_bad_poll_settings(self) -> None:
        settings = Settings(
            telegram_bot_token="token",
            telegram_allowed_user_ids=[1],
            qbit_base_url="http://qbit",
            qbit_username="user",
            qbit_password="pass",
            jav_file_poll_attempts=0,
            jav_file_poll_interval_seconds=0,
            add_context_poll_attempts=0,
            add_context_poll_interval_seconds=0,
        )

        with self.assertRaises(ValueError) as caught:
            settings.validate()

        message = str(caught.exception)
        self.assertIn("JAV_FILE_POLL_ATTEMPTS 必须大于 0", message)
        self.assertIn("JAV_FILE_POLL_INTERVAL_SECONDS 必须大于 0", message)
        self.assertIn("ADD_CONTEXT_POLL_ATTEMPTS 必须大于 0", message)
        self.assertIn("ADD_CONTEXT_POLL_INTERVAL_SECONDS 必须大于 0", message)
