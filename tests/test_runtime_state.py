import asyncio
import time
import unittest

import app.runtime_state as runtime_state
from app.runtime_state import runtime_context


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data = {}


class RuntimeContextTests(unittest.IsolatedAsyncioTestCase):
    def test_public_exports_exclude_compatibility_aliases(self) -> None:
        self.assertIn("RuntimeContext", runtime_state.__all__)
        self.assertIn("runtime_context", runtime_state.__all__)
        self.assertNotIn("_get_state", runtime_state.__all__)
        self.assertNotIn("_persist_state", runtime_state.__all__)

    async def test_category_prompt_lock_is_reused(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)

        self.assertIs(context.category_prompt_lock(), context.category_prompt_lock())

    async def test_add_submission_lock_is_reused(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)

        self.assertIs(context.add_submission_lock(), context.add_submission_lock())

    async def test_task_sets_are_stable(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)
        task = asyncio.create_task(asyncio.sleep(60))

        try:
            context.llm_auto_apply_tasks.add(task)

            self.assertIs(context.llm_auto_apply_tasks, context.llm_auto_apply_tasks)
            self.assertEqual(context.llm_auto_apply_tasks, {task})
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    def test_known_hash_cache_respects_ttl(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)

        context.set_known_hashes_cache({"abc"})

        self.assertEqual(context.get_known_hashes_cache(ttl_seconds=60), {"abc"})
        context.data["known_hashes_cache"] = (time.time() - 120, {"abc"})
        self.assertIsNone(context.get_known_hashes_cache(ttl_seconds=60))

    def test_has_persistent_state_requires_store_and_state(self) -> None:
        app = FakeApplication()
        context = runtime_context(app)

        self.assertFalse(context.has_persistent_state)
        context.data["state_store"] = object()
        self.assertFalse(context.has_persistent_state)
        context.data["bot_state"] = object()
        self.assertTrue(context.has_persistent_state)
