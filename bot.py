import nest_asyncio
nest_asyncio.apply()

import os
import json
import logging
import asyncio
import subprocess
import shutil
import html
from pathlib import Path
from telegram import Bot, InputFile, Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

# --- Setup and Environment Variables ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in .env. Please set it in your .env file.")
    exit(1)
if not OWNER_CHAT_ID:
    logger.error("OWNER_CHAT_ID not found in .env. Please set it in your .env file.")
    exit(1)
try:
    OWNER_CHAT_ID = int(OWNER_CHAT_ID)
except Exception:
    logger.error("Invalid OWNER_CHAT_ID. Must be an integer.")
    exit(1)

# Path to chat IDs JSON file.
chat_ids_file = "chat_ids.json"
if os.path.exists(chat_ids_file):
    try:
        with open(chat_ids_file, 'r') as f:
            chat_ids = json.load(f)
            if not isinstance(chat_ids, list):
                logger.error("chat_ids.json does not contain a list. Setting to empty list.")
                chat_ids = []
    except json.JSONDecodeError as e:
        logger.error("Error decoding JSON from chat_ids.json: %s", e)
        chat_ids = []
else:
    chat_ids = []

# Folder path to monitor.
downloads_folder = Path("users/downloads")
if not downloads_folder.is_dir():
    logger.error("Downloads folder '%s' does not exist.", downloads_folder)
    exit(1)

# Create a Bot instance (for one-off messaging when needed).
bot = Bot(token=BOT_TOKEN)

# --- Allowed File Extensions ---

photo_ext     = {'.jpg', '.jpeg', '.png'}
video_ext     = {'.mp4', '.mov', '.avi', '.mkv'}
animation_ext = {'.gif', '.webm'}
sticker_ext   = {'.webp'}
voice_ext     = {'.oga'}
music_ext     = {'.mp3'}
allowed_extensions = photo_ext | video_ext | animation_ext | sticker_ext | voice_ext | music_ext

# --- Helper Functions ---

def update_chat_ids_file():
    """Write the current chat_ids list to chat_ids.json."""
    try:
        with open(chat_ids_file, 'w') as f:
            json.dump(chat_ids, f, indent=2)
        logger.info("Updated chat_ids.json: %s", chat_ids)
    except Exception as e:
        logger.error("Failed to update chat_ids.json: %s", e)

def clear_downloads_folder(folder: Path):
    """Completely clear the downloads folder by deleting all files and subdirectories."""
    for item in folder.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
                logger.info("Deleted directory '%s' from downloads folder.", item.name)
            else:
                item.unlink()
                logger.info("Deleted file '%s' from downloads folder.", item.name)
        except Exception as e:
            logger.error("Failed to delete '%s': %s", item.name, e)

def has_audio(file_path: Path) -> bool:
    """
    Use ffprobe (from FFmpeg) to check if a video file has an audio stream.
    Returns True if an audio stream is found; otherwise, False.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        audio_output = result.stdout.strip()
        return bool(audio_output)
    except Exception as e:
        logger.error("Error checking audio for '%s': %s", file_path.name, e)
        return True  # Safer default

# --- Media Sending ---

async def send_media(file_path: Path):
    """
    Send the media file to every chat in chat_ids.
    - For .mp4 files: if audio exists, send as video; otherwise, as animation.
    - For voice (.oga), music (.mp3) and others, use respective send methods.
    - All media (except stickers) include a caption with the file name in monospaced (HTML) text.
    - Retries up to 3 times per chat. After processing, the file is deleted.
    - Waits 3 seconds before processing to ensure the file is fully downloaded.
    """
    ext = file_path.suffix.lower()
    
    if ext in photo_ext:
        send_method = bot.send_photo
        param = "photo"
        method_name = "photo"
    elif ext in video_ext:
        if ext == ".mp4":
            if has_audio(file_path):
                send_method = bot.send_video
                param = "video"
                method_name = "video"
            else:
                send_method = bot.send_animation
                param = "animation"
                method_name = "animation"
        else:
            send_method = bot.send_video
            param = "video"
            method_name = "video"
    elif ext in voice_ext:
        send_method = bot.send_voice
        param = "voice"
        method_name = "voice"
    elif ext in music_ext:
        send_method = bot.send_audio
        param = "audio"
        method_name = "audio"
    elif ext in animation_ext:
        send_method = bot.send_animation
        param = "animation"
        method_name = "animation"
    elif ext in sticker_ext:
        send_method = bot.send_sticker
        param = "sticker"
        method_name = "sticker"
    else:
        send_method = bot.send_document
        param = "document"
        method_name = "document"

    logger.info("Preparing to send file '%s' as %s.", file_path.name, method_name)
    await asyncio.sleep(3)  # Allow the file to be fully available

    caption = None
    if method_name != "sticker":
        caption = f"<code>{html.escape(file_path.name)}</code>"

    pending_ids = set(chat_ids)
    for attempt in range(1, 4):
        if not pending_ids:
            break
        logger.info("Attempt %d for file '%s' to chats: %s", attempt, file_path.name, pending_ids)
        for chat_id in list(pending_ids):
            try:
                with open(file_path, 'rb') as media_file:
                    if ((ext in {'.mp4', '.webm'}) and method_name == "animation"):
                        new_filename = f"{file_path.stem}.gif"
                    else:
                        new_filename = file_path.name
                    input_file = InputFile(media_file, filename=new_filename)
                    kwargs = {param: input_file}
                    if caption:
                        kwargs["caption"] = caption
                        kwargs["parse_mode"] = "HTML"
                    await send_method(chat_id=chat_id, **kwargs)
                logger.info("Sent '%s' to chat %s on attempt %d.", file_path.name, chat_id, attempt)
                pending_ids.remove(chat_id)
            except TelegramError as te:
                logger.error("Telegram error sending '%s' to chat %s on attempt %d: %s",
                             file_path.name, chat_id, attempt, te)
            except Exception as ex:
                logger.error("Unexpected error sending '%s' to chat %s on attempt %d: %s",
                             file_path.name, chat_id, attempt, ex)
        if pending_ids:
            await asyncio.sleep(1)

    if pending_ids:
        logger.error("Failed to send '%s' to chats %s after 3 attempts.", file_path.name, pending_ids)
    else:
        logger.info("Successfully sent '%s' to all chats.", file_path.name)

    try:
        file_path.unlink()
        logger.info("Deleted file '%s' after processing.", file_path.name)
    except Exception as del_ex:
        logger.error("Failed to delete file '%s': %s", file_path.name, del_ex)

async def monitor_folder():
    """
    Continuously monitor the downloads folder:
    - Process allowed media files.
    - Delete any file or directory that is not allowed.
    """
    logger.info("Monitoring folder '%s' for new media...", downloads_folder)
    while True:
        for item in downloads_folder.iterdir():
            if item.is_file():
                if item.suffix.lower() in allowed_extensions:
                    await send_media(item)
                else:
                    try:
                        item.unlink()
                        logger.info("Deleted non-media file '%s'.", item.name)
                    except Exception as e:
                        logger.error("Failed to delete non-media file '%s': %s", item.name, e)
            elif item.is_dir():
                try:
                    shutil.rmtree(item)
                    logger.info("Deleted directory '%s' from downloads folder.", item.name)
                except Exception as e:
                    logger.error("Failed to delete directory '%s': %s", item.name, e)
        await asyncio.sleep(5)

# --- Telegram Bot Handlers for Owner Commands ---

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /add <chatid>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat id format. Must be an integer.")
        return
    global chat_ids
    if new_id in chat_ids:
        await update.message.reply_text(f"Chat id {new_id} is already in the list.")
    else:
        chat_ids.append(new_id)
        update_chat_ids_file()
        # Send the updated chat_ids.json as a document:
        with open(chat_ids_file, 'rb') as doc:
            await update.message.reply_document(
                document=doc,
                filename="chat_ids.json",
                caption="Updated chat_ids.json file"
            )

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <chatid>")
        return
    try:
        rem_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat id format. Must be an integer.")
        return
    global chat_ids
    if rem_id in chat_ids:
        chat_ids.remove(rem_id)
        update_chat_ids_file()
        # Send the updated JSON file as a document:
        with open(chat_ids_file, 'rb') as doc:
            await update.message.reply_document(
                document=doc,
                filename="chat_ids.json",
                caption="Updated chat_ids.json file"
            )
    else:
        await update.message.reply_text(f"Chat id {rem_id} not found in the list.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    files = [f for f in downloads_folder.iterdir() if f.is_file()]
    if not files:
        text = "Number of all files: 0\nNo files found."
    else:
        text = f"Number of all files: {len(files)}\n\nName of all files:\n"
        for idx, f in enumerate(files, start=1):
            text += f"{idx}. {f.name}\n"
    await update.message.reply_text(text)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    clear_downloads_folder(downloads_folder)
    await update.message.reply_text("Downloads folder cleared.")

async def json_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    document = update.message.document
    if document.file_name.endswith(".json"):
        new_file = await document.get_file()
        json_data = await new_file.download_as_bytearray()
        try:
            data = json.loads(json_data.decode("utf-8"))
            if isinstance(data, list):
                global chat_ids
                chat_ids = data
                update_chat_ids_file()
                with open(chat_ids_file, 'rb') as doc:
                    await update.message.reply_document(
                        document=doc,
                        filename="chat_ids.json",
                        caption="Updated chat_ids.json file"
                    )
            else:
                await update.message.reply_text("JSON data is not a list.")
        except Exception as e:
            await update.message.reply_text(f"Failed to parse JSON: {e}")
    else:
        await update.message.reply_text("Please send a JSON file.")

# --- Telegram Bot Setup ---

async def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json") & filters.Chat(OWNER_CHAT_ID), json_document_handler))
    
    # Send an initial message to the owner.
    try:
        await app.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=(
                "Bot started. Use /add and /remove to manage target chat IDs.\n"
                "Send a JSON file to update the chat IDs.\n"
                "Use /list to list files, and /delete to clear the downloads folder."
            )
        )
    except Exception as ex:
        logger.error("Error sending startup message to owner: %s", ex)
    
    # IMPORTANT: Set close_loop=False to avoid closing an already running event loop.
    await app.run_polling(close_loop=False)

# --- Main Execution ---

async def main():
    logger.info("Clearing entire downloads folder '%s'...", downloads_folder)
    clear_downloads_folder(downloads_folder)
    logger.info("Downloads folder cleared. Starting monitor and bot...")
    await asyncio.gather(
        monitor_folder(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
