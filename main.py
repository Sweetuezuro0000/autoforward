import asyncio
import os
import random
import logging
from datetime import datetime, timezone
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from motor.motor_asyncio import AsyncIOMotorClient

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message as AiogramMessage,
    CallbackQuery,
    InlineKeyboardMarkup as AioInlineKeyboardMarkup,
    InlineKeyboardButton as AioInlineKeyboardButton,
)
from aiogram.enums import ChatType


# =========================================================
# LOGGING & ENV
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("autoforward")

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "autoforward")

PORT = int(os.environ.get("PORT", "10000"))

if not API_ID or not API_HASH or not SESSION_STRING or not BOT_TOKEN or not OWNER_ID or not MONGO_URI:
    raise RuntimeError("Missing required Environment Variables!")


# =========================================================
# MONGODB SETUP
# =========================================================

mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client[MONGO_DB]
config_col = db["config"]
chats_col = db["chats"]
stats_col = db["stats"]
users_col = db["users"]


async def mongo_init():
    await mongo_client.admin.command("ping")
    await config_col.update_one({"_id": "status"}, {"$setOnInsert": {"value": "OFF"}}, upsert=True)
    await config_col.update_one({"_id": "delay"}, {"$setOnInsert": {"value": 8}}, upsert=True)
    await config_col.update_one({"_id": "cycle_delay"}, {"$setOnInsert": {"value": 15}}, upsert=True)
    await config_col.update_one({"_id": "msg"}, {"$setOnInsert": {"value": "Default Auto Message"}}, upsert=True)
    await config_col.update_one({"_id": "force_sub"}, {"$setOnInsert": {"value": []}}, upsert=True)
    log.info("MongoDB Initialized Successfully")


async def get_config(key, default=None):
    doc = await config_col.find_one({"_id": key})
    return doc.get("value", default) if doc else default


async def set_config(key, value):
    await config_col.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


async def inc_stat(key, amount=1):
    await stats_col.update_one({"_id": key}, {"$inc": {"value": amount}}, upsert=True)


async def get_stat(key):
    doc = await stats_col.find_one({"_id": key})
    return int(doc.get("value", 0)) if doc else 0


# =========================================================
# CLIENTS
# =========================================================

userbot = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


Thread(target=start_health_server, daemon=True).start()


# =========================================================
# HELPERS & FORCE-SUB LOGIC
# =========================================================

def format_channel_peer(value):
    if not value:
        return None
    val = str(value).strip()
    if val.startswith("https://t.me/"):
        val = val.replace("https://t.me/", "", 1)
    elif val.startswith("http://t.me/"):
        val = val.replace("http://t.me/", "", 1)
    val = val.rstrip("/")

    if val.lstrip("-").isdigit():
        return int(val)
    if not val.startswith("@") and not val.startswith("+"):
        val = "@" + val
    return val


async def get_force_sub_channels():
    channels = await get_config("force_sub", [])
    if not isinstance(channels, list):
        return []
    return [format_channel_peer(ch) for ch in channels if ch][:3]


async def set_force_sub_channels(channels):
    formatted = [format_channel_peer(ch) for ch in channels if ch][:3]
    await set_config("force_sub", formatted)


async def check_user_membership(client_instance, channel, user_id):
    try:
        peer = format_channel_peer(channel)
        if isinstance(client_instance, Bot):
            member = await client_instance.get_chat_member(chat_id=peer, user_id=user_id)
        else:
            member = await client_instance.get_chat_member(peer, user_id)

        status_str = str(getattr(member, "status", "")).lower()
        if any(s in status_str for s in ["owner", "administrator", "creator", "member"]):
            return True
        if "restricted" in status_str:
            return bool(getattr(member, "is_member", False))
        return False
    except Exception as e:
        log.warning(f"ForceSub check error ({channel}): {e}")
        return None


async def bot_force_sub_keyboard():
    channels = await get_force_sub_channels()
    buttons = []
    for index, ch in enumerate(channels, 1):
        link = f"https://t.me/{str(ch).lstrip('@')}" if str(ch).startswith("@") else "https://t.me/"
        buttons.append([AioInlineKeyboardButton(text=f"📢 Join Channel {index}", url=link)])
    buttons.append([AioInlineKeyboardButton(text="✅ Check Subscription", callback_data="check_sub")])
    return AioInlineKeyboardMarkup(inline_keyboard=buttons)


async def check_bot_force_sub(user_id):
    channels = await get_force_sub_channels()
    if not channels:
        return True
    for ch in channels:
        res = await check_user_membership(bot, ch, user_id)
        if res is False:
            return False
        if res is None:
            return None
    return True


# =========================================================
# AIOGRAM BOT (GROUPS & BOT PM GUARD)
# =========================================================

# 1. GROUP GUARD: Checks every message in groups
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group_message_guard(message: AiogramMessage):
    if not message.from_user or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    status = await check_bot_force_sub(user_id)

    # Agar user channels me joined NAHI hai, toh message bhejega
    if status is False:
        try:
            await message.reply(
                f"⚠️ **{message.from_user.first_name}**, aapne humare required channels join nahi kiye hain!\n\n"
                f"Group me chat karne ke liye pehle niche diye gaye channels join karein:",
                reply_markup=await bot_force_sub_keyboard()
            )
        except Exception as e:
            log.warning(f"Failed to send group warning: {e}")
    # Agar JOINED hai (status == True), toh BOT SHANT RAHEGA (Nothing happens)


# 2. BOT PM GUARD: Private messages to bot
@dp.message(F.chat.type == ChatType.PRIVATE)
async def bot_pm_guard(message: AiogramMessage):
    user_id = message.from_user.id
    status = await check_bot_force_sub(user_id)

    if status is False:
        await message.answer(
            "🔒 **Force Subscription Required**\n\nPehle niche diye gaye channels join karo:",
            reply_markup=await bot_force_sub_keyboard()
        )
        return

    await message.answer("🚀 Bot online hai! Message received.")


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    status = await check_bot_force_sub(callback.from_user.id)
    if status is True:
        await callback.message.edit_text("✅ **Subscription verified!** Ab aap chat kar sakte ho.")
    elif status is False:
        await callback.answer("❌ Pehle sabhi channels join karo!", show_alert=True)
    else:
        await callback.answer("⚠️ Subscription check temporarily failed.", show_alert=True)


# =========================================================
# USERBOT PM FORCE-SUB (SESSION PM GUARD)
# =========================================================

@userbot.on_message(filters.private & ~filters.me & ~filters.bot)
async def userbot_pm_forcesub(client, message: Message):
    channels = await get_force_sub_channels()
    if not channels:
        return

    user_id = message.from_user.id
    for ch in channels:
        res = await check_user_membership(userbot, ch, user_id)
        if res is False:
            links = "\n".join([f"📢 https://t.me/{str(c).lstrip('@')}" if str(c).startswith("@") else f"📢 {c}" for c in channels])
            await message.reply_text(
                f"🔒 **Force Subscription Required**\n\n"
                f"Mujhe PM me message karne ke liye pehle yeh channels join karo:\n\n{links}"
            )
            return


# =========================================================
# USERBOT MANAGEMENT COMMANDS (SESSION CONTROL)
# =========================================================

async def resolve_target(value):
    peer = format_channel_peer(value)
    if isinstance(peer, int):
        return await userbot.get_chat(peer)
    if str(peer).startswith("+"):
        return await userbot.join_chat(peer)
    return await userbot.get_chat(peer)


@userbot.on_message(filters.me & filters.command("addchat", prefixes="."))
async def addchat_handler(client, message: Message):
    args = message.command
    try:
        chat = message.chat if len(args) == 1 else await resolve_target(args[1])
        await chats_col.update_one(
            {"_id": str(chat.id)},
            {"$set": {"chat_id": str(chat.id), "title": chat.title or "Unknown", "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        await message.edit_text(f"✅ **Chat Added:** `{chat.title or chat.id}`")
    except Exception as e:
        await message.edit_text(f"❌ **Error:** `{e}`")


@userbot.on_message(filters.me & filters.command("delchat", prefixes="."))
async def delchat_handler(client, message: Message):
    args = message.command
    try:
        chat_id = message.chat.id if len(args) == 1 else (await resolve_target(args[1])).id
        res = await chats_col.delete_one({"_id": str(chat_id)})
        await message.edit_text("🗑 **Removed**" if res.deleted_count else "ℹ️ Chat not found.")
    except Exception as e:
        await message.edit_text(f"❌ `{e}`")


@userbot.on_message(filters.me & filters.command("listchats", prefixes="."))
async def listchats_handler(client, message: Message):
    chats = await chats_col.find().to_list(length=1000)
    if not chats:
        await message.edit_text("ℹ️ **No target chats.**")
        return
    text = "📋 **Target Chats**\n\n" + "\n".join(f"{i}. **{c.get('title')}** (`{c.get('chat_id')}`)" for i, c in enumerate(chats, 1))
    await message.edit_text(text)


@userbot.on_message(filters.me & filters.command("setmsg", prefixes="."))
async def setmsg_handler(client, message: Message):
    if len(message.command) < 2:
        await message.edit_text("❌ `.setmsg Hello text`")
        return
    await set_config("msg", message.text.split(maxsplit=1)[1])
    await message.edit_text("✅ **Auto-Message updated.**")


@userbot.on_message(filters.me & filters.command("setdelay", prefixes="."))
async def setdelay_handler(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        return
    await set_config("delay", max(1, int(message.command[1])))
    await message.edit_text(f"✅ Delay set to `{message.command[1]}s`")


@userbot.on_message(filters.me & filters.command("setcycle", prefixes="."))
async def setcycle_handler(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        return
    await set_config("cycle_delay", max(1, int(message.command[1])))
    await message.edit_text(f"✅ Cycle set to `{message.command[1]} min`")


@userbot.on_message(filters.me & filters.command("forcesub", prefixes="."))
async def forcesub_handler(client, message: Message):
    args = message.command
    if len(args) < 2:
        await message.edit_text("❌ Example: `.forcesub @ch1 @ch2`")
        return
    channels = [format_channel_peer(x) for x in args[1:4]]
    await set_force_sub_channels(channels)
    await message.edit_text("🔒 **Force-Sub Channels Set:**\n" + "\n".join(f"{i}. `{ch}`" for i, ch in enumerate(channels, 1)))


@userbot.on_message(filters.me & filters.command("forcesuboff", prefixes="."))
async def forcesuboff_handler(client, message: Message):
    await set_force_sub_channels([])
    await message.edit_text("🔓 **Force-Sub disabled.**")


@userbot.on_message(filters.me & filters.command("automsg", prefixes="."))
async def automsg_handler(client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return
    state = message.command[1].upper()
    await set_config("status", state)
    await message.edit_text(f"🤖 **Auto Msg State:** `{state}`")


@userbot.on_message(filters.me & filters.command("help", prefixes="."))
async def userbot_help(client, message: Message):
    await message.edit_text(
        "🤖 **Session Commands**\n\n"
        "`.addchat` | `.delchat` | `.listchats`\n"
        "`.setmsg text` | `.setdelay 10` | `.setcycle 15`\n"
        "`.forcesub @ch1 @ch2` | `.forcesuboff`\n"
        "`.automsg on/off`"
    )


# =========================================================
# BROADCAST WORKER
# =========================================================

async def safe_send(chat_id, text):
    try:
        await userbot.send_message(chat_id, text)
        return True
    except FloodWait as e:
        await asyncio.sleep(int(e.value))
        try:
            await userbot.send_message(chat_id, text)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def broadcast_worker():
    while True:
        try:
            await asyncio.sleep(2)
            if await get_config("status", "OFF") != "ON":
                continue

            msg_text = await get_config("msg", "")
            delay = max(3, int(await get_config("delay", 8)))
            cycle = max(1, int(await get_config("cycle_delay", 15)))
            chats = await chats_col.find().to_list(length=1000)

            if msg_text and chats:
                for chat in chats:
                    if await get_config("status", "OFF") != "ON":
                        break
                    await safe_send(format_channel_peer(chat.get("chat_id")), msg_text)
                    await asyncio.sleep(random.randint(max(3, delay - 2), delay + 4))

                if await get_config("status", "OFF") == "ON":
                    await asyncio.sleep(cycle * 60)
        except Exception as e:
            log.exception(f"Broadcast error: {e}")
            await asyncio.sleep(10)


# =========================================================
# MAIN EXECUTION
# =========================================================

async def main():
    await mongo_init()
    await userbot.start()
    log.info("Userbot Session Started!")

    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(broadcast_worker())

    log.info("Bot & Userbot fully operational!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
