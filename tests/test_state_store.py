import json
import tempfile
import time
import unittest
from pathlib import Path

from app.state_store import BotState, StateStore


class StateStoreTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.sqlite3"
            store = StateStore(str(path))
            state = BotState(
                notified_completed_hashes={"a" * 40},
                jav_processed_hashes={"b" * 40},
                jellyfin_duplicate_codes={"SSIS-123": int(time.time()) + 3600},
            )

            store.save(state)
            loaded = StateStore(str(path)).load()

            self.assertEqual(loaded.notified_completed_hashes, {"a" * 40})
            self.assertEqual(loaded.jav_processed_hashes, {"b" * 40})
            self.assertEqual(set(loaded.jellyfin_duplicate_codes), {"SSIS-123"})

    def test_load_migrates_legacy_json_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "bot_state.json"
            json_path.write_text(
                json.dumps(
                    {
                        "notified_completed_hashes": ["a" * 40],
                        "jav_processed_hashes": ["b" * 40],
                        "jellyfin_duplicate_codes": {
                            "SSIS-123": int(time.time()) + 3600,
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = StateStore(str(json_path))
            loaded = store.load()

            self.assertEqual(store.path, json_path.with_suffix(".sqlite3"))
            self.assertEqual(loaded.notified_completed_hashes, {"a" * 40})
            self.assertEqual(loaded.jav_processed_hashes, {"b" * 40})
            self.assertIn("SSIS-123", loaded.jellyfin_duplicate_codes)

    def test_expired_values_are_purged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.sqlite3"
            expired = int(time.time()) - 1
            store = StateStore(str(path))
            state = BotState(
                notified_completed_hashes={"a" * 40},
                jav_processed_hashes={"b" * 40},
                jellyfin_duplicate_codes={"SSIS-123": expired},
            )
            state.notified_completed_at["a" * 40] = 1
            state.jav_processed_at["b" * 40] = 1

            store.save(state)
            loaded = StateStore(str(path)).load()

            self.assertEqual(loaded.notified_completed_hashes, set())
            self.assertEqual(loaded.jav_processed_hashes, set())
            self.assertEqual(loaded.jellyfin_duplicate_codes, {})
