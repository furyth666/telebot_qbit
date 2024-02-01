import qbittorrentapi
import logging
import json
from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes


async def display(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "<b>Current Torrents:</b>\n\n"
    for torrent in sorted(qbt_client.torrents_info(), key=lambda x: x.name):
        # Emoji and state representation
        state_emoji = ""
        if torrent.state == "metaDL":
            state_emoji = "🔍"
            state_text = "Downloading Metadata"
        elif torrent.state in ["downloading", "stalledDL"]:
            state_emoji = "⬇️"
            state_text = "Downloading"
        elif torrent.state in ["uploading", "stalledUP"]:
            state_emoji = "⬆️"
            state_text = "Uploading"
        elif torrent.state == "pausedDL":
            state_emoji = "⏸️"
            state_text = "Paused"
        elif torrent.state == "pausedUP":
            state_emoji = "⏹️"
            state_text = "Completed"
        elif torrent.state == "error":
            state_emoji = "❗"
            state_text = "Error"

        # Progress bar
        progress_bar_length = 10
        progress = torrent.progress if torrent.state != "metaDL" else 0
        filled_length = int(progress_bar_length * progress)
        progress_bar = "█" * filled_length + "-" * \
            (progress_bar_length - filled_length)

        progress_text = f"{progress*100:.2f}%" if torrent.state != "metaDL" else "N/A"

        text += f"🔹 <b>{torrent.name}</b>\n   State: {state_emoji} {state_text}\n   Progress: [{progress_bar}] {progress_text}\n\n"

    # Send the message with formatting
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='HTML')
