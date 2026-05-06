from __future__ import annotations

import re

from telegram.ext import Application

from app.state_store import BotState, StateStore


def _get_jav_pattern(application: Application) -> re.Pattern[str]:
    return application.bot_data["jav_name_pattern"]


def _get_state_store(application: Application) -> StateStore:
    return application.bot_data["state_store"]


def _get_state(application: Application) -> BotState:
    return application.bot_data["bot_state"]


async def _persist_state(application: Application) -> None:
    await _get_state_store(application).save_async(_get_state(application))
