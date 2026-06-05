import re
import unittest

from app.jav_rules import (
    DEFAULT_JAV_NAME_REGEX,
    _extract_jav_code,
    _extract_jav_lookup_code,
    _is_jav_title,
)


class JavRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pattern = re.compile(DEFAULT_JAV_NAME_REGEX)

    def test_extracts_standard_code_from_messy_title(self) -> None:
        self.assertEqual(
            _extract_jav_code("[FHD] ssis 123 sample title", self.pattern),
            "SSIS-123",
        )
        self.assertEqual(
            _extract_jav_code("PRWF_010-uncensored", self.pattern),
            "PRWF-010",
        )
        self.assertEqual(
            _extract_jav_code("abp.987.mp4", self.pattern),
            "ABP-987",
        )

    def test_extracts_vendor_specific_codes(self) -> None:
        cases = {
            "FC2 PPV 1234567": "FC2-PPV-1234567",
            "fc2-1234567": "FC2-PPV-1234567",
            "heyzo_hd_1234": "HEYZO-1234",
            "1pondo_010123_001": "1PONDO-010123-001",
            "caribbeancom 010123-001": "CARIB-010123-001",
            "Tokyo Hot n1234": "N-1234",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_extract_jav_code(title, self.pattern), expected)

    def test_ignores_noisy_prefixes_and_suffixes(self) -> None:
        cases = {
            "[FHD-1080] [hhd800.com] SSIS-123-C": "SSIS-123",
            "[4K][UHD-2160][domain.example] MIDV_777_ch.mp4": "MIDV-777",
            "h264-1080p FC2-PPVDB 1234567 leaked": "FC2-PPV-1234567",
            "ＳＳＩＳ－１２３　字幕": "SSIS-123",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_extract_jav_code(title, self.pattern), expected)

    def test_lookup_rejects_long_free_text_and_non_codes(self) -> None:
        self.assertIsNone(_extract_jav_lookup_code("hello world", self.pattern))
        self.assertIsNone(_extract_jav_lookup_code("x" * 81 + " ABP-123", self.pattern))

    def test_is_jav_title_uses_expanded_default_pattern(self) -> None:
        self.assertTrue(_is_jav_title("FC2-PPV-1234567", self.pattern))
        self.assertTrue(_is_jav_title("1pondo_010123_001", self.pattern))
        self.assertTrue(_is_jav_title("[FHD-1080] SSIS-123-C", self.pattern))
        self.assertFalse(_is_jav_title("ubuntu-24.04-live-server.iso", self.pattern))
        self.assertFalse(_is_jav_title("[FHD-1080] sample video.mp4", self.pattern))
