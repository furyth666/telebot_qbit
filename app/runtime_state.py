from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from telegram.ext import Application

from app.config import Settings
from app.jellyfin_client import JellyfinClient
from app.qbit_client import QbitClient
from app.state_store import BotState, StateStore

__all__ = [
    "RuntimeContext",
    "get_jav_pattern",
    "get_state",
    "get_state_store",
    "persist_state",
    "runtime_context",
]


@dataclass
class RuntimeContext:
    application: Application

    @property
    def data(self) -> dict[str, Any]:
        return self.application.bot_data

    @property
    def settings(self) -> Settings:
        return self.data["settings"]

    @settings.setter
    def settings(self, value: Settings) -> None:
        self.data["settings"] = value

    @property
    def qbit(self) -> QbitClient:
        return self.data["qbit"]

    @qbit.setter
    def qbit(self, value: QbitClient) -> None:
        self.data["qbit"] = value

    @property
    def jellyfin(self) -> JellyfinClient:
        return self.data["jellyfin"]

    @jellyfin.setter
    def jellyfin(self, value: JellyfinClient) -> None:
        self.data["jellyfin"] = value

    @property
    def jav_pattern(self) -> re.Pattern[str]:
        return self.data["jav_name_pattern"]

    @jav_pattern.setter
    def jav_pattern(self, value: re.Pattern[str]) -> None:
        self.data["jav_name_pattern"] = value

    @property
    def state_store(self) -> StateStore:
        return self.data["state_store"]

    @state_store.setter
    def state_store(self, value: StateStore) -> None:
        self.data["state_store"] = value

    @property
    def state(self) -> BotState:
        return self.data["bot_state"]

    @state.setter
    def state(self, value: BotState) -> None:
        self.data["bot_state"] = value

    @property
    def has_persistent_state(self) -> bool:
        return "state_store" in self.data and "bot_state" in self.data

    @property
    def completion_monitor_initialized(self) -> bool:
        return bool(self.data.get("completion_monitor_initialized", False))

    @completion_monitor_initialized.setter
    def completion_monitor_initialized(self, value: bool) -> None:
        self.data["completion_monitor_initialized"] = value

    @property
    def telegram_network_error_times(self) -> list[float]:
        return self.data.setdefault("telegram_network_error_times", [])

    @telegram_network_error_times.setter
    def telegram_network_error_times(self, value: list[float]) -> None:
        self.data["telegram_network_error_times"] = value

    def category_prompt_lock(self) -> asyncio.Lock:
        lock = self.data.get("category_prompt_lock")
        if lock is None:
            lock = asyncio.Lock()
            self.data["category_prompt_lock"] = lock
        return lock

    @property
    def prompted_category_hashes(self) -> set[str]:
        return self.data.setdefault("prompted_category_hashes", set())

    @property
    def pending_category_choices(self) -> dict[str, list[str]]:
        return self.data.setdefault("pending_category_choices", {})

    @property
    def add_finalize_tasks(self) -> set[asyncio.Task]:
        return self.data.setdefault("add_finalize_tasks", set())

    @property
    def llm_auto_apply_tasks(self) -> set[asyncio.Task]:
        return self.data.setdefault("llm_auto_apply_tasks", set())

    @property
    def add_finalize_semaphore(self) -> asyncio.Semaphore | None:
        return self.data.get("add_finalize_semaphore")

    @add_finalize_semaphore.setter
    def add_finalize_semaphore(self, value: asyncio.Semaphore) -> None:
        self.data["add_finalize_semaphore"] = value

    @property
    def completion_monitor_task(self) -> asyncio.Task | None:
        return self.data.get("completion_monitor_task")

    @completion_monitor_task.setter
    def completion_monitor_task(self, value: asyncio.Task) -> None:
        self.data["completion_monitor_task"] = value

    @property
    def watchdog_task(self) -> asyncio.Task | None:
        return self.data.get("watchdog_task")

    @watchdog_task.setter
    def watchdog_task(self, value: asyncio.Task) -> None:
        self.data["watchdog_task"] = value

    def get_known_hashes_cache(self, *, ttl_seconds: float) -> set[str] | None:
        cache = self.data.get("known_hashes_cache")
        if not cache:
            return None
        cached_at, cached_hashes = cache
        if time.time() - cached_at > ttl_seconds:
            return None
        return set(cached_hashes)

    def set_known_hashes_cache(self, known_hashes: set[str]) -> None:
        self.data["known_hashes_cache"] = (time.time(), set(known_hashes))

    def get_jellyfin_jav_prefix_cache(self, *, ttl_seconds: float) -> list[str] | None:
        cache = self.data.get("jellyfin_jav_prefix_cache")
        if not cache:
            return None
        cached_at, prefixes = cache
        if time.time() - cached_at > ttl_seconds:
            return None
        return list(prefixes)

    def set_jellyfin_jav_prefix_cache(self, prefixes: list[str]) -> None:
        self.data["jellyfin_jav_prefix_cache"] = (time.time(), list(prefixes))


def runtime_context(application: Application) -> RuntimeContext:
    return RuntimeContext(application)


def get_jav_pattern(application: Application) -> re.Pattern[str]:
    return runtime_context(application).jav_pattern


def get_state_store(application: Application) -> StateStore:
    return runtime_context(application).state_store


def get_state(application: Application) -> BotState:
    return runtime_context(application).state


async def persist_state(application: Application) -> None:
    context = runtime_context(application)
    await context.state_store.save_async(context.state)
