from __future__ import annotations

from dataclasses import dataclass


CALLBACK_PREFIX = "tor"
DEFAULT_CALLBACK_VIEW = "all"

__all__ = [
    "CALLBACK_PREFIX",
    "DEFAULT_CALLBACK_VIEW",
    "CategoryCallbackPayload",
    "TorrentCallback",
    "build_category_callback",
    "build_torrent_callback",
    "parse_category_callback_payload",
    "parse_torrent_callback",
]


@dataclass(frozen=True)
class TorrentCallback:
    action: str
    view: str
    payload: str


@dataclass(frozen=True)
class CategoryCallbackPayload:
    torrent_hash: str
    category_index: int


def build_torrent_callback(
    action: str,
    payload: str,
    view: str = DEFAULT_CALLBACK_VIEW,
) -> str:
    return f"{CALLBACK_PREFIX}:{action}:{view}:{payload}"


def build_category_callback(torrent_hash: str, category_index: int) -> str:
    return build_torrent_callback(
        "cat",
        f"{torrent_hash}:{category_index}",
        DEFAULT_CALLBACK_VIEW,
    )


def parse_torrent_callback(value: str) -> TorrentCallback | None:
    try:
        prefix, action, view, payload = value.split(":", 3)
    except ValueError:
        return None
    if prefix != CALLBACK_PREFIX:
        return None
    return TorrentCallback(action=action, view=view, payload=payload)


def parse_category_callback_payload(
    payload: str,
) -> CategoryCallbackPayload | None:
    try:
        torrent_hash, index_text = payload.rsplit(":", 1)
        category_index = int(index_text)
    except ValueError:
        return None
    return CategoryCallbackPayload(
        torrent_hash=torrent_hash,
        category_index=category_index,
    )
