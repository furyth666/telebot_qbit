from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from telegram.ext import Application

from app.add_links import (
    AddBatchResult,
    AddContext,
    add_torrent_links,
    extract_torrent_links,
    format_add_batch_reply,
)
from app.jobs import background_finalize_torrent
from app.qbit_client import QbitClient
from app.runtime_state import runtime_context


_MAX_BACKGROUND_FINALIZE_CONCURRENCY = 3

__all__ = [
    "AddLinksWorkflowResult",
    "finalize_added_torrents_batch",
    "start_add_background_tasks",
    "submit_add_links_from_text",
]


@dataclass(frozen=True)
class AddLinksWorkflowResult:
    links: list[str]
    batch: AddBatchResult
    reply_text: str


def _get_finalize_semaphore(application: Application) -> asyncio.Semaphore:
    context = runtime_context(application)
    semaphore = context.add_finalize_semaphore
    if semaphore is None:
        semaphore = asyncio.Semaphore(_MAX_BACKGROUND_FINALIZE_CONCURRENCY)
        context.add_finalize_semaphore = semaphore
    return semaphore


async def finalize_added_torrents_batch(
    application: Application,
    qbit: QbitClient,
    contexts: list[AddContext],
    chat_id: int,
) -> None:
    semaphore = _get_finalize_semaphore(application)
    queue: asyncio.Queue[AddContext] = asyncio.Queue()
    for add_context in contexts:
        queue.put_nowait(add_context)

    async def worker() -> None:
        while True:
            try:
                add_context = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with semaphore:
                    await background_finalize_torrent(
                        application,
                        qbit,
                        add_context,
                        chat_id,
                    )
            except Exception:
                logging.exception("Failed while finalizing added torrent in batch")
            finally:
                queue.task_done()

    worker_count = min(_MAX_BACKGROUND_FINALIZE_CONCURRENCY, len(contexts))
    await asyncio.gather(*(worker() for _ in range(worker_count)))


def start_add_background_tasks(
    application: Application,
    qbit: QbitClient,
    contexts: list[AddContext],
    chat_id: int,
) -> None:
    if not contexts:
        return
    task = application.create_task(
        finalize_added_torrents_batch(
            application,
            qbit,
            contexts,
            chat_id,
        )
    )
    tasks = runtime_context(application).add_finalize_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def submit_add_links_from_text(
    application: Application,
    text: str,
    *,
    auto_detected: bool,
    chat_id: int,
) -> AddLinksWorkflowResult | None:
    links = extract_torrent_links(text)
    if not links:
        return None

    context = runtime_context(application)
    qbit = context.qbit
    batch = await add_torrent_links(application, qbit, links)
    start_add_background_tasks(application, qbit, batch.contexts, chat_id)
    return AddLinksWorkflowResult(
        links=links,
        batch=batch,
        reply_text=format_add_batch_reply(
            batch,
            auto_detected=auto_detected,
            settings=context.settings,
        ),
    )
