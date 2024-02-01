import qbittorrentapi
import logging
import json
from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")


async def sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = f"qBittorrent: {qbt_client.app.version}"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=info)


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


async def add_torrent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = context.args
    if not url:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please provide a url")
        return
    text = ''
    torrent = qbt_client.torrents_add(urls=url)
    if torrent:
        qbt_client.torrents_reannounce(hashes=torrent)
        newest_torrent = None
        for torrent in qbt_client.torrents_info():
            if newest_torrent is None or torrent.added_on > newest_torrent.added_on:
                newest_torrent = torrent
        text = f"Added {newest_torrent.name} to qBittorrent"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Failed to add torrent")

# delete the torrent


async def delete_torrent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # filter the hash
    hash = context.args
    if not hash:
        # get all the torrents and display them
        text = "<b>Current Torrents:</b>\n\n"
        for torrent in sorted(qbt_client.torrents_info(), key=lambda x: x.name):
            text += f"🔹 <b>{torrent.name}</b>\n   Hash: {torrent.hash}\n\n"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='HTML')
        return
    # check the torrent exists
    found = False
    for torrent in qbt_client.torrents_info():
        if torrent.hash == hash:
            found = True
            break
    if found:
        qbt_client.torrents_delete(torrent_hashes=hash, delete_files=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="torrent deleted")
        return
    # check if the torrent is deleted
    for torrent in qbt_client.torrents_info():
        if torrent.hash == hash:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Failed to delete torrent")
            return
    if not found:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="torrent not found")


def get_token():
    with open('token.txt', 'r') as file:
        return file.read().strip()


if __name__ == '__main__':
    application = ApplicationBuilder().token(get_token()).build()
    # load the json file
    try:
        conn_info = json.load(open('qbit.json'))
    except FileNotFoundError:
        context.bot.send_message(
            chat_id=update.effective_chat.id, text="Please provide a qbit.json file")

    qbt_client = qbittorrentapi.Client(**conn_info)
    try:
        qbt_client.auth_log_in()
    except qbittorrentapi.LoginFailed as e:
        print(e)

    start_handler = CommandHandler('start', start)
    sysinfo_handler = CommandHandler('sysinfo', sysinfo)
    display_handler = CommandHandler('display', display)
    add_torrent_handler = CommandHandler('add', add_torrent)
    delete_torrent_handler = CommandHandler('del', delete_torrent)

    application.add_handler(start_handler)
    application.add_handler(sysinfo_handler)
    application.add_handler(display_handler)
    application.add_handler(add_torrent_handler)
    application.add_handler(delete_torrent_handler)

    application.run_polling()
