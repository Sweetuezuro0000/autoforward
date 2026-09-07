import asyncio
import os
import random
import logging
from datetime import datetime, timezone
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from motor.motor_asyncio import AsyncIOMotorClient

from pyrogram import Client, filters, enums
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
from aiogram.enums import ChatType, ChatMemberStatus


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("autoforward")


# =========================================================
# ENV VARIABLES
# =========================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "autoforward")

PORT = int(os.environ.get("PORT", "10000"))

if not API_ID or not API_HASH or not SESSION_STRING:
    raise RuntimeError("API_ID / API_HASH / SESSION_STRING missing")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID missing")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI missing")


# =========================================================
# MONGODB
# =========================================================

mongo_client = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
)

db = mongo_client[MONGO_DB]
config_col = db["config"]
chats_col = db["chats"]
stats_col = db["stats"]
users_col = db["users"]
groups_col = db["groups"]


async def mongo_init():
    await mongo_client.admin.command("ping")
    await config_col.update_one({"_id": "status"}, {"$setOnInsert": {"value": "OFF"}}, upsert=True)
    await config_col.update_one({"_id": "delay"}, {"$setOnInsert": {"value": 8}}, upsert=True)
    await config_col.update_one({"_id": "cycle_delay"}, {"$setOnInsert": {"value": 15}}, upsert=True)
    await config_col.update_one({"_id": "msg"}, {"$setOnInsert": {"value": "Default Auto Message"}}, upsert=True)
    await config_col.update_one({"_id": "force_sub"}, {"$setOnInsert": {"value": []}}, upsert=True)
    log.info("MongoDB initialized successfully")


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
# CLIENT INITIALIZATION
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
        self.wfile.write(b"AutoForward Userbot + Bot is running!")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"Health server running on port {PORT}")
    server.serve_forever()


Thread(target=start_health_server, daemon=True).start()


# =========================================================
# HELPER FUNCTIONS & PEER FORMATTING
# =========================================================

def is_owner(user_id):
    return int(user_id) == OWNER_ID


def format_channel_peer(value):
    """Formats channel input into proper Pyrogram/Aiogram peer target."""
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


async def add_chat_db(chat_id, title, username=None):
    await chats_col.update_one(
        {"_id": str(chat_id)},
        {
            "$set": {
                "chat_id": str(chat_id),
                "title": title or "Unknown",
                "username": username or "",
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True
    )


async def delete_chat_db(chat_id):
    result = await chats_col.delete_one({"_id": str(chat_id)})
    return result.deleted_count


async def get_all_chats():
    return await chats_col.find().to_list(length=10000)


async def get_force_sub_channels():
    channels = await get_config("force_sub", [])
    if not isinstance(channels, list):
        return []
    return [format_channel_peer(ch) for ch in channels if ch][:3]


async def set_force_sub_channels(channels):
    formatted = [format_channel_peer(ch) for ch in channels if ch][:3]
    await set_config("force_sub", formatted)


# =========================================================
# UNIVERSAL FORCE SUB CHECKER
# =========================================================

async def check_user_membership(client_instance, channel, user_id):
    """Works seamlessly for both Pyrogram Client and Aiogram Bot."""
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
        log.warning(f"ForceSub check failed for {channel} (User: {user_id}): {e}")
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


async def userbot_force_sub_ok():
    channels = await get_force_sub_channels()
    if not channels:
        return True
    me = await userbot.get_me()
    for ch in channels:
        res = await check_user_membership(userbot, ch, me.id)
        if res is False:
            try:
                await userbot.join_chat(ch)
                res = True
            except Exception:
                return False
        if res is None:
            return None
    return True


# =========================================================
# AIOGRAM BOT HANDLERS
# =========================================================

@dp.message(CommandStart())
async def bot_start(message: AiogramMessage):
    user_id = message.from_user.id
    await users_col.update_one(
        {"_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "first_name": message.from_user.first_name or "",
                "username": message.from_user.username or "",
                "updated_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )

    if message.chat.type == ChatType.PRIVATE:
        status = await check_bot_force_sub(user_id)
        if status is False:
            await message.answer(
                "🔒 **Force Subscription Required**\n\nPehle niche diye gaye channels join karo.",
                reply_markup=await bot_force_sub_keyboard()
            )
            return
        elif status is None:
            await message.answer("⚠️ Force-Sub check temporary issue. Please try again.")
            return

    await message.answer("🚀 **AutoForward Manager**\n\nBot online aur working condition me hai!\nUse /help for commands.")


@dp.message(Command("help"))
async def bot_help(message: AiogramMessage):
    if message.chat.type == ChatType.PRIVATE:
        status = await check_bot_force_sub(message.from_user.id)
        if status is not True:
            await message.answer("🔒 Pehle Force-Sub complete karo.", reply_markup=await bot_force_sub_keyboard())
            return

    text = (
        "🤖 **Bot Commands**\n\n"
        "/start - Start Bot\n"
        "/help - Commands List\n"
        "/status - System Status (Owner Only)\n"
        "/groups - Registered Groups\n\n"
        "👥 **Group Admin Commands**\n"
        "/groupstatus\n"
        "/groupenable\n"
        "/groupdisable\n"
        "/groupinfo"
    )
    await message.answer(text)


@dp.message(Command("status"))
async def bot_status(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return
    status = await get_config("status", "OFF")
    delay = await get_config("delay", 8)
    cycle = await get_config("cycle_delay", 15)
    chats = await chats_col.count_documents({})
    await message.answer(
        f"📊 **System Status**\n\n🤖 Auto Msg: `{status}`\n⏱ Delay: `{delay}s`\n🔄 Cycle: `{cycle} min`\n🎯 Targets: `{chats}`"
    )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    status = await check_bot_force_sub(callback.from_user.id)
    if status is True:
        await callback.message.edit_text("✅ **Subscription verified!**\n\nAb aap bot features use kar sakte ho.")
    elif status is False:
        await callback.answer("❌ Pehle sabhi channels join karo!", show_alert=True)
    else:
        await callback.answer("⚠️ Check temporarily failed.", show_alert=True)


async def group_admin(message: AiogramMessage):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        status_str = str(member.status).lower()
        return any(s in status_str for s in ["administrator", "creator", "owner"])
    except Exception:
        return False


@dp.message(Command("groups"))
async def bot_groups(message: AiogramMessage):
    if message.chat.type == ChatType.PRIVATE and await check_bot_force_sub(message.from_user.id) is not True:
        await message.answer("🔒 Please complete Force-Sub first.", reply_markup=await bot_force_sub_keyboard())
        return

    groups = await groups_col.find().sort("updated_at", -1).to_list(length=1000)
    if not groups:
        await message.answer("📭 **Koi group registered nahi hai.**")
        return

    text = "👥 **Added Groups**\n\n"
    for i, g in enumerate(groups, 1):
        state = "ON" if g.get("enabled", True) else "OFF"
        text += f"{i}. **{g.get('title', 'Unknown')}**\n   🆔 `{g.get('chat_id')}` | ⚡ `{state}`\n\n"
    await message.answer(text)


@dp.message(Command("groupstatus"))
async def group_status(message: AiogramMessage):
    if not await group_admin(message):
        return
    g = await groups_col.find_one({"_id": message.chat.id})
    state = g.get("enabled", True) if g else True
    await message.answer(f"👥 **Group Status**\n\n📌 `{message.chat.title}`\n⚡ Status: `{ 'ON' if state else 'OFF' }`")


@dp.message(Command("groupenable"))
async def group_enable(message: AiogramMessage):
    if not await group_admin(message):
        return
    await groups_col.update_one({"_id": message.chat.id}, {"$set": {"enabled": True}}, upsert=True)
    await message.answer("✅ Group system **enabled**.")


@dp.message(Command("groupdisable"))
async def group_disable(message: AiogramMessage):
    if not await group_admin(message):
        return
    await groups_col.update_one({"_id": message.chat.id}, {"$set": {"enabled": False}}, upsert=True)
    await message.answer("🛑 Group system **disabled**.")


@dp.message(Command("groupinfo"))
async def group_info(message: AiogramMessage):
    if not await group_admin(message):
        return
    g = await groups_col.find_one({"_id": message.chat.id})
    enabled = g.get("enabled", True) if g else True
    await message.answer(f"👥 **Group Info**\n\n📌 `{message.chat.title}`\n🆔 `{message.chat.id}`\n⚡ Active: `{enabled}`")


# =========================================================
# PYROGRAM USERBOT COMMAND HANDLERS
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
        if len(args) == 1:
            chat = message.chat
        else:
            chat = await resolve_target(args[1])

        await add_chat_db(chat.id, chat.title or chat.first_name or "Unknown", getattr(chat, "username", ""))
        await message.edit_text(f"✅ **Chat Added**\n\n📌 `{chat.title or chat.first_name or 'Unknown'}`\n🆔 `{chat.id}`")
    except Exception as e:
        await message.edit_text(f"❌ **Add Chat Failed:** `{e}`")


@userbot.on_message(filters.me & filters.command("delchat", prefixes="."))
async def delchat_handler(client, message: Message):
    args = message.command
    try:
        if len(args) == 1:
            chat_id = message.chat.id
        else:
            chat = await resolve_target(args[1])
            chat_id = chat.id

        deleted = await delete_chat_db(chat_id)
        if deleted:
            await message.edit_text(f"🗑 **Removed:** `{chat_id}`")
        else:
            await message.edit_text("ℹ️ Chat database me nahi mila.")
    except Exception as e:
        await message.edit_text(f"❌ `{e}`")


@userbot.on_message(filters.me & filters.command("listchats", prefixes="."))
async def listchats_handler(client, message: Message):
    chats = await get_all_chats()
    if not chats:
        await message.edit_text("ℹ️ **No target chats.**")
        return

    text = "📋 **Target Chats**\n\n"
    for idx, c in enumerate(chats, 1):
        line = f"{idx}. **{c.get('title', 'Unknown')}**\n   `{c.get('chat_id')}`\n\n"
        if len(text) + len(line) > 3900:
            await message.edit_text(text)
            return
        text += line
    await message.edit_text(text)


@userbot.on_message(filters.me & filters.command("setmsg", prefixes="."))
async def setmsg_handler(client, message: Message):
    if len(message.command) < 2:
        await message.edit_text("❌ Example: `.setmsg Hello World`")
        return
    text = message.text.split(maxsplit=1)[1]
    await set_config("msg", text)
    await message.edit_text("✅ **Auto-Message updated.**")


@userbot.on_message(filters.me & filters.command("setdelay", prefixes="."))
async def setdelay_handler(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.edit_text("❌ Example: `.setdelay 10`")
        return
    val = int(message.command[1])
    await set_config("delay", max(1, val))
    await message.edit_text(f"✅ Delay set to `{val}s`")


@userbot.on_message(filters.me & filters.command("setcycle", prefixes="."))
async def setcycle_handler(client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        await message.edit_text("❌ Example: `.setcycle 15`")
        return
    val = int(message.command[1])
    await set_config("cycle_delay", max(1, val))
    await message.edit_text(f"✅ Cycle break set to `{val} minutes`")


@userbot.on_message(filters.me & filters.command("forcesub", prefixes="."))
async def forcesub_handler(client, message: Message):
    args = message.command
    if len(args) < 2:
        await message.edit_text("❌ Example:\n`.forcesub @channel1 @channel2`\n`.forcesub -100123456789`")
        return

    channels = [format_channel_peer(x) for x in args[1:4]]
    await set_force_sub_channels(channels)
    await message.edit_text("🔒 **Force-Sub Channels Updated:**\n" + "\n".join(f"{i}. `{ch}`" for i, ch in enumerate(channels, 1)))


@userbot.on_message(filters.me & filters.command("forcesuboff", prefixes="."))
async def forcesuboff_handler(client, message: Message):
    await set_force_sub_channels([])
    await message.edit_text("🔓 **Force-Sub system disabled.**")


@userbot.on_message(filters.me & filters.command("automsg", prefixes="."))
async def automsg_handler(client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        await message.edit_text("❌ Usage: `.automsg on` or `.automsg off`")
        return

    state = message.command[1].upper()
    if state == "ON":
        f_ok = await userbot_force_sub_ok()
        if f_ok is False:
            await message.edit_text("❌ Userbot account Force-Sub channels me joined nahi hai. Pehle join karein.")
            return

    await set_config("status", state)
    await message.edit_text(f"🤖 **Auto Messaging State:** `{state}`")


@userbot.on_message(filters.me & filters.command("stats", prefixes="."))
async def stats_handler(client, message: Message):
    status = await get_config("status", "OFF")
    delay = await get_config("delay", 8)
    cycle = await get_config("cycle_delay", 15)
    channels = await get_force_sub_channels()
    chats = await chats_col.count_documents({})
    total = await get_stat("total_attempts")
    success = await get_stat("success_sent")
    failed = await get_stat("failed_sent")

    await message.edit_text(
        f"📊 **AutoForward Dashboard**\n━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Status: `{status}`\n⏱ Delay: `{delay}s`\n🔄 Cycle: `{cycle} min`\n"
        f"🔒 ForceSub: `{len(channels)}` channels\n🎯 Targets: `{chats}`\n\n"
        f"📨 Attempts: `{total}`\n✅ Success: `{success}`\n❌ Failed: `{failed}`"
    )


@userbot.on_message(filters.me & filters.command("help", prefixes="."))
async def userbot_help(client, message: Message):
    await message.edit_text(
        "🤖 **Userbot Commands**\n\n"
        "`.addchat @group` | `.addchat -10012345`\n"
        "`.delchat @group` | `.listchats`\n\n"
        "`.setmsg text` | `.setdelay 10` | `.setcycle 15`\n\n"
        "`.forcesub @ch1 @ch2` | `.forcesuboff`\n\n"
        "`.automsg on` | `.automsg off` | `.stats`"
    )


# =========================================================
# BROADCAST WORKER & SAFE SEND
# =========================================================

async def safe_send(chat_id, text):
    try:
        await userbot.send_message(chat_id, text)
        return "success"
    except FloodWait as e:
        wait = max(int(e.value), 1)
        log.warning(f"FloodWait encountered: sleeping {wait}s")
        await asyncio.sleep(wait)
        try:
            await userbot.send_message(chat_id, text)
            return "success"
        except Exception:
            return "failed"
    except (RPCError, Exception) as e:
        log.error(f"Send failed to {chat_id}: {e}")
        return "failed"


async def broadcast_worker():
    while True:
        try:
            await asyncio.sleep(2)
            if await get_config("status", "OFF") != "ON":
                continue

            if await userbot_force_sub_ok() is False:
                await set_config("status", "OFF")
                try:
                    await userbot.send_message("me", "❌ **Auto Msg stopped:** Force-Sub missing.")
                except Exception:
                    pass
                continue

            msg_text = await get_config("msg", "")
            if not msg_text:
                continue

            delay = max(3, int(await get_config("delay", 8)))
            cycle = max(1, int(await get_config("cycle_delay", 15)))
            chats = await get_all_chats()

            if not chats:
                continue

            log.info(f"Broadcast round started for {len(chats)} targets.")
            for chat in chats:
                if await get_config("status", "OFF") != "ON":
                    break

                chat_id = format_channel_peer(chat.get("chat_id"))
                await inc_stat("total_attempts")
                res = await safe_send(chat_id, msg_text)

                if res == "success":
                    await inc_stat("success_sent")
                else:
                    await inc_stat("failed_sent")

                await asyncio.sleep(random.randint(max(3, delay - 2), delay + 4))

            if await get_config("status", "OFF") == "ON":
                log.info(f"Round completed. Waiting {cycle} minutes.")
                await asyncio.sleep(cycle * 60)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f"Broadcast worker error: {e}")
            await asyncio.sleep(10)


async def warmup_peers():
    try:
        async for dialog in userbot.get_dialogs(limit=200):
            pass
    except Exception as e:
        log.warning(f"Peer warmup error: {e}")


# =========================================================
# MAIN EXECUTION
# =========================================================

async def main():
    await mongo_init()
    await userbot.start()

    me = await userbot.get_me()
    log.info(f"Userbot Started: {me.id} | @{me.username or 'NoUsername'}")

    await warmup_peers()

    await bot.delete_webhook(drop_pending_updates=True)
    broadcast_task = asyncio.create_task(broadcast_worker())

    log.info("🚀 Userbot + Aiogram Bot both operational!")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        broadcast_task.cancel()
        await userbot.stop()
        await bot.session.close()
        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(main())
