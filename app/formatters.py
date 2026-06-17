from __future__ import annotations

import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.callback_data import build_torrent_callback
from app.config import Settings
from app.jellyfin_client import JellyfinItem
from app.qbit_client import TorrentFile, TorrentProperties, TorrentSummary
from app.stash_client import StashScene


_STATE_INFO = {
    "downloading": ("⬇️", "⬇️ 下载中"),
    "forcedDL": ("🚀", "🚀 强制下载"),
    "forcedMetaDL": ("🧲", "🧲 强制获取元数据"),
    "metaDL": ("🧲", "🧲 获取元数据"),
    "uploading": ("⬆️", "⬆️ 做种中"),
    "forcedUP": ("🚀", "🚀 强制做种"),
    "stalledDL": ("🟡", "⏸️ 等待下载"),
    "stalledUP": ("🟡", "⏸️ 等待上传"),
    "queuedDL": ("🕒", "🕒 排队下载"),
    "queuedUP": ("🕒", "🕒 排队做种"),
    "pausedDL": ("⏸️", "⏸️ 已暂停"),
    "pausedUP": ("⏸️", "⏸️ 已暂停"),
    "checkingDL": ("🔎", "🔎 校验中"),
    "checkingUP": ("🔎", "🔎 校验中"),
    "checkingResumeData": ("🔄", "🔄 恢复数据校验"),
    "moving": ("📦", "📦 移动文件中"),
    "missingFiles": ("📁", "📁 文件缺失"),
    "error": ("❌", "❌ 错误"),
}

__all__ = [
    "build_list_keyboard",
    "format_action_result",
    "format_bytes",
    "format_jellyfin_caption",
    "format_large_file_threshold",
    "format_speed",
    "format_stash_caption",
    "format_torrent_caption",
    "format_torrent_detail",
    "format_torrent_line",
    "format_torrent_overview",
    "short_hash",
]


def format_large_file_threshold(settings: Settings) -> str:
    value = settings.jav_large_file_threshold_gb
    if float(value).is_integer():
        return f"{int(value)} GB"
    return f"{value:g} GB"


def format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_speed(value: int) -> str:
    return f"{format_bytes(value)}/s"


def _fmt_eta(value: int) -> str:
    if value < 0 or value >= 8640000:
        return "∞"
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def short_hash(hash_value: str) -> str:
    return hash_value[:8]


def _fmt_state(value: str) -> str:
    return _STATE_INFO.get(value, ("🎯", value))[1]


def _state_icon(state: str) -> str:
    return _STATE_INFO.get(state, ("🎯", state))[0]


def _fmt_progress_bar(progress: float, width: int = 10) -> str:
    filled = max(0, min(width, round(progress * width)))
    return f"{'█' * filled}{'░' * (width - filled)}"


def _fmt_progress_text(progress: float) -> str:
    return f"{progress * 100:.1f}%"


def format_torrent_caption(item: TorrentSummary, index: int) -> str:
    return f"{index}. {_state_icon(item.state)} <b>{escape(item.name)}</b>"


def _fmt_category(value: str) -> str:
    return value if value else "未分类"


def format_torrent_line(item: TorrentSummary) -> str:
    return (
        f"┣ 🏷️ {_fmt_state(escape(item.state))} | 🗂️ {_fmt_category(escape(item.category))}\n"
        f"┣ 📊 <code>{_fmt_progress_bar(item.progress)}</code> {_fmt_progress_text(item.progress)}"
        f" | ⏳ {_fmt_eta(item.eta)}\n"
        f"┣ 🚦 ⬇️ {format_speed(item.dlspeed)} | ⬆️ {format_speed(item.upspeed)}"
        f" | 💾 {format_bytes(item.size)}\n"
        f"┗ 🔑 <code>{short_hash(item.hash)}</code>"
    )


def format_torrent_overview(title: str, torrents: list[TorrentSummary]) -> str:
    total_size = sum(item.size for item in torrents)
    active_count = sum(1 for item in torrents if item.dlspeed > 0 or item.upspeed > 0)
    completed_count = sum(1 for item in torrents if item.progress >= 1)
    lines = [
        f"<b>📋 {escape(title)}</b>",
        (
            f"📦 共 {len(torrents)} 个任务 | ⚡ 活跃 {active_count} 个 | "
            f"✅ 完成 {completed_count} 个 | 💾 总大小 {format_bytes(total_size)}"
        ),
        "——————————",
    ]
    return "\n".join(lines)


def format_action_result(action: str, torrent_hash: str) -> str:
    return "\n".join(
        [
            f"<b>{escape(action)}</b>",
            f"🔑 任务 Hash: <code>{escape(torrent_hash)}</code>",
        ]
    )


def _fmt_timestamp(value: int) -> str:
    if value <= 0:
        return "未记录"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def build_list_keyboard(
    torrents: list[TorrentSummary],
    *,
    filter_name: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"详情 {short_hash(item.hash)}",
                callback_data=build_torrent_callback("detail", item.hash, filter_name),
            )
        ]
        for item in torrents
    ]
    return InlineKeyboardMarkup(rows)


def _build_detail_keyboard(
    item: TorrentSummary,
    *,
    view: str,
) -> InlineKeyboardMarkup:
    primary_action = (
        InlineKeyboardButton("▶️ 恢复", callback_data=build_torrent_callback("resume", item.hash, view))
        if item.state in {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}
        else InlineKeyboardButton("⏸️ 暂停", callback_data=build_torrent_callback("pause", item.hash, view))
    )
    rows = [
        [primary_action, InlineKeyboardButton("🔄 刷新", callback_data=build_torrent_callback("detail", item.hash, view))],
        [
            InlineKeyboardButton("🗑️ 删除任务", callback_data=build_torrent_callback("delete", item.hash, view)),
            InlineKeyboardButton("🔥 删除含文件", callback_data=build_torrent_callback("deletefiles", item.hash, view)),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def format_torrent_detail(
    item: TorrentSummary,
    files: list[TorrentFile],
    props: TorrentProperties | None,
    *,
    view: str = "all",
) -> tuple[str, InlineKeyboardMarkup]:
    preview_lines: list[str] = []
    for file in files[:5]:
        flag = "⏭️" if file.priority == 0 else "📄"
        preview_lines.append(
            f"{flag} {escape(file.name.rsplit('/', 1)[-1])} ({format_bytes(file.size)})"
        )
    if len(files) > 5:
        preview_lines.append(f"… 还有 {len(files) - 5} 个文件")

    details = [
        "<b>🎯 种子详情</b>",
        f"📦 <b>{escape(item.name)}</b>",
        f"🔑 <code>{escape(item.hash)}</code>",
        f"🏷️ {_fmt_state(escape(item.state))} | 🗂️ {_fmt_category(escape(item.category))}",
        f"📊 <code>{_fmt_progress_bar(item.progress)}</code> {_fmt_progress_text(item.progress)} | ⏳ {_fmt_eta(item.eta)}",
        f"🚦 ⬇️ {format_speed(item.dlspeed)} | ⬆️ {format_speed(item.upspeed)}",
        f"💾 大小 {format_bytes(item.size)} | 📤 已上传 {format_bytes(props.total_uploaded) if props else '未知'}",
        f"📈 分享率 {props.share_ratio:.2f}" if props else "📈 分享率 未知",
        f"🕒 添加时间 {_fmt_timestamp(item.added_on)}",
        f"✅ 完成时间 {_fmt_timestamp(item.completion_on)}",
        f"📁 保存路径 {escape(props.save_path) if props and props.save_path else '未提供'}",
        f"🧾 文件数 {len(files)}",
    ]
    if preview_lines:
        details.append("📄 文件预览")
        details.extend(preview_lines)

    return "\n".join(details), _build_detail_keyboard(item, view=view)


def _fmt_premiere_date(value: str) -> str | None:
    if not value:
        return None
    return value.split("T", 1)[0]


def _jellyfin_item_url(base_url: str, item: JellyfinItem) -> str:
    if not base_url or not item.item_id:
        return ""
    query = f"id={item.item_id}"
    if item.server_id:
        query = f"{query}&serverId={item.server_id}"
    return f"{base_url}/web/index.html#!/details?{query}"


def _jellyfin_person_url(base_url: str, person_id: str, server_id: str) -> str:
    if not base_url or not person_id:
        return ""
    query = f"id={person_id}"
    if server_id:
        query = f"{query}&serverId={server_id}"
    return f"{base_url}/web/index.html#!/details?{query}"


def format_jellyfin_caption(
    code: str,
    item: JellyfinItem,
    total_count: int,
    *,
    public_base_url: str,
) -> str:
    lines = [
        f"<b>🎬 Jellyfin 查询结果</b>",
        f"🏷️ 番号: <code>{escape(code)}</code>",
        f"📁 标题: <b>{escape(item.name)}</b>",
    ]
    meta_parts: list[str] = []
    if item.production_year:
        meta_parts.append(f"📅 {item.production_year}")
    premiere_date = _fmt_premiere_date(item.premiere_date)
    if premiere_date:
        meta_parts.append(f"🗓️ {premiere_date}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))
    if item.actors:
        actor_parts: list[str] = []
        for actor in item.actors[:5]:
            actor_url = _jellyfin_person_url(public_base_url, actor.person_id, item.server_id)
            if actor_url:
                actor_parts.append(
                    f'<a href="{escape(actor_url)}">{escape(actor.name)}</a>'
                )
            else:
                actor_parts.append(escape(actor.name))
        actor_text = " / ".join(actor_parts)
        if len(item.actors) > 5:
            actor_text = f"{actor_text} ..."
        lines.append(f"🎭 演员: {actor_text}")
    item_url = _jellyfin_item_url(public_base_url, item)
    if item_url:
        lines.append(f'🌐 Jellyfin: <a href="{escape(item_url)}">打开视频详情</a>')
    if item.overview:
        overview = item.overview.strip()
        if len(overview) > 300:
            overview = f"{overview[:297].rstrip()}..."
        lines.append(f"📝 {escape(overview)}")
    if total_count > 1:
        lines.append(f"🔎 共找到 {total_count} 条匹配，当前展示第 1 条。")
    return "\n".join(lines)


def _stash_scene_url(base_url: str, scene_id: str) -> str:
    if not base_url or not scene_id:
        return ""
    return f"{base_url.rstrip('/')}/scenes/{scene_id}"


def format_stash_caption(
    query: str,
    scene: StashScene,
    total_count: int,
    *,
    base_url: str,
) -> str:
    lines = [
        "<b>🎬 Stash 查询结果</b>",
        f"🔎 查询: <code>{escape(query)}</code>",
        f"📁 标题: <b>{escape(scene.title or '未命名')}</b>",
    ]
    meta_parts: list[str] = []
    if scene.studio:
        meta_parts.append(f"🏢 {escape(scene.studio)}")
    if scene.date:
        meta_parts.append(f"📅 {escape(scene.date)}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))
    if scene.performers:
        lines.append(f"🎭 演员: {escape(' / '.join(scene.performers))}")
    if scene.tags:
        lines.append(f"🏷️ 标签: {escape(' / '.join(scene.tags[:5]))}")
    if scene.paths:
        lines.append(f"📂 路径: <code>{escape(scene.paths[0])}</code>")
    scene_url = _stash_scene_url(base_url, scene.scene_id)
    if scene_url:
        lines.append(f'🌐 Stash: <a href="{escape(scene_url)}">打开场景详情</a>')
    if total_count > 1:
        lines.append(f"🔎 共找到 {total_count} 条匹配，当前展示第 1 条。")
    return "\n".join(lines)
