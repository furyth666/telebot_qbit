import logging
import unittest

from app.main import _SensitiveFormatter


class SensitiveFormatterTests(unittest.TestCase):
    def test_redacts_common_secret_patterns(self) -> None:
        formatter = _SensitiveFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=(
                "url=https://api.telegram.org/bot123456789:AAsecret_token/send "
                "Authorization: Bearer llm-secret "
                "X-Emby-Token=emby-secret "
                "password=qb-secret"
            ),
            args=(),
            exc_info=None,
        )

        message = formatter.format(record)

        self.assertIn("bot<redacted>", message)
        self.assertIn("Authorization: Bearer <redacted>", message)
        self.assertIn("X-Emby-Token=<redacted>", message)
        self.assertIn("password=<redacted>", message)
        self.assertNotIn("llm-secret", message)
        self.assertNotIn("emby-secret", message)
        self.assertNotIn("qb-secret", message)
