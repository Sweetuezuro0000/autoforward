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
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

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
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("autoforward")


# =========================================================
# ENV
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

    defaults = {
        "status": "OFF",
        "delay": 8,
        "cycle_delay": 15,
        "msg": "Default Auto Message",
        "force_sub": [],
        "group_force_sub": True,
    }

    for key, value in defaults.items():
        await config_col.update_one(
            {"_id": key},
            {"$setOnInsert": {"value": value}},
            upsert=True
        )

    log.info("MongoDB connected")


async def get_config(key, default=None):
    doc = await config_col.find_one({"_id": key})
    if not doc:
        return default
    return doc.get("value", default)


async def set_config(key, value):
    await config_col.update_one(
        {"_id": key},
        {"$set": {"value": value}},
        upsert=True
    )


async def inc_stat(key, amount=1):
    await stats_col.update_one(
        {"_id": key},
        {"$inc": {"value": amount}},
        upsert=True
    )


async def get_stat(key):
    doc = await stats_col.find_one({"_id": key})
    if not doc:
        return 0
    return int(doc.get("value", 0))


# =========================================================
# PYROGRAM + AIOGRAM
# =========================================================

userbot = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# HEALTH
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
# HELPERS
# =========================================================

def is_owner(user_id):
    try:
        return int(user_id) == OWNER_ID
    except Exception:
        return False


def now():
    return datetime.now(timezone.utc)


async def edit_or_reply(message, text):
    try:
        await message.edit_text(text)
    except Exception:
        try:
            await message.reply_text(text)
        except Exception:
            pass


async def answer_bot(message, text, **kwargs):
    try:
        await message.answer(text, **kwargs)
    except Exception as e:
        log.warning(f"Bot answer failed: {e}")


# =========================================================
# CHAT DATABASE
# =========================================================

async def add_chat_db(chat_id, title, username=None):
    await chats_col.update_one(
        {"_id": str(chat_id)},
        {
            "$set": {
                "chat_id": str(chat_id),
                "title": title or "Unknown",
                "username": username or "",
                "updated_at": now(),
            }
        },
        upsert=True
    )


async def delete_chat_db(chat_id):
    result = await chats_col.delete_one({"_id": str(chat_id)})
    return result.deleted_count


async def get_all_chats():
    return await chats_col.find().sort("updated_at", 1).to_list(length=10000)


# =========================================================
# FORCE SUB
# =========================================================

def clean_channel(value):
    value = str(value).strip()

    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break

    return value.rstrip("/")


async def get_force_sub_channels():
    channels = await get_config("force_sub", [])
    if not isinstance(channels, list):
        return []
    return channels[:3]


async def set_force_sub_channels(channels):
    await set_config("force_sub", channels[:3])


def channel_link(channel):
    value = str(channel).strip()

    if value.startswith(("https://t.me/", "http://t.me/")):
        return value

    if value.startswith("+"):
        return f"https://t.me/{value}"

    if value.startswith("-100"):
        # Telegram cannot create a public URL from a private numeric ID.
        return None

    return f"https://t.me/{value.lstrip('@')}"


async def check_user_membership(client, channel, user_id):
    try:
        member = await client.get_chat_member(channel, user_id)

        status = member.status

        if status in (
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.MEMBER,
        ):
            return True

        if status == enums.ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", False))

        return False

    except Exception as e:
        log.warning(f"ForceSub check failed {channel}: {e}")
        return None


async def bot_force_sub_keyboard():
    channels = await get_force_sub_channels()
    buttons = []

    for index, channel in enumerate(channels, 1):
        link = channel_link(channel)

        if link:
            buttons.append([
                AioInlineKeyboardButton(
                    text=f"📢 Join Channel {index}",
                    url=link
                )
            ])

    buttons.append([
        AioInlineKeyboardButton(
            text="✅ Check Subscription",
            callback_data="check_sub"
        )
    ])

    return AioInlineKeyboardMarkup(inline_keyboard=buttons)


async def check_bot_force_sub(user_id):
    channels = await get_force_sub_channels()

    if not channels:
        return True

    for channel in channels:
        result = await check_user_membership(bot, channel, user_id)

        if result is False:
            return False

        if result is None:
            return None

    return True


async def userbot_force_sub_ok():
    channels = await get_force_sub_channels()

    if not channels:
        return True

    me = await userbot.get_me()

    for channel in channels:
        result = await check_user_membership(userbot, channel, me.id)

        if result is False:
            return False

        if result is None:
            return None

    return True


# =========================================================
# BOT USER SAVE
# =========================================================

async def save_bot_user(message):
    if not message.from_user:
        return

    await users_col.update_one(
        {"_id": message.from_user.id},
        {
            "$set": {
                "user_id": message.from_user.id,
                "first_name": message.from_user.first_name or "",
                "username": message.from_user.username or "",
                "updated_at": now(),
            }
        },
        upsert=True
    )


async def bot_private_guard(message):
    if message.chat.type != ChatType.PRIVATE:
        return True

    if is_owner(message.from_user.id):
        return True

    status = await check_bot_force_sub(message.from_user.id)

    if status is True:
        return True

    if status is None:
        await answer_bot(message, "⚠️ Force-Sub check temporarily unavailable.")
        return False

    await answer_bot(
        message,
        "🔒 **Force Subscription Required**\n\nPehle required channels join karo.",
        reply_markup=await bot_force_sub_keyboard()
    )
    return False


# =========================================================
# BOT START / HELP / STATUS
# =========================================================

@dp.message(CommandStart())
async def bot_start(message: AiogramMessage):
    await save_bot_user(message)

    if not await bot_private_guard(message):
        return

    await answer_bot(
        message,
        "🚀 **AutoForward Manager**\n\n"
        "Bot successfully working.\n\n"
        "Use /help for all commands."
    )


@dp.message(Command("help"))
async def bot_help(message: AiogramMessage):
    await save_bot_user(message)

    if not await bot_private_guard(message):
        return

    text = (
        "🤖 **AutoForward Manager**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 **General**\n"
        "/start\n"
        "/help\n"
        "/status\n"
        "/groups\n\n"
        "👥 **Group**\n"
        "/groupstatus\n"
        "/groupenable\n"
        "/groupdisable\n"
        "/groupinfo\n"
        "/groupforcesub on\n"
        "/groupforcesub off\n\n"
        "🔐 **Owner commands**\n"
        "/setmsg TEXT\n"
        "/setdelay SECONDS\n"
        "/setcycle MINUTES\n"
        "/forcesub CHANNEL1 CHANNEL2 CHANNEL3\n"
        "/forcesuboff\n"
        "/autobroadcast on\n"
        "/autobroadcast off\n"
        "/stats"
    )
    await answer_bot(message, text)


@dp.message(Command("status"))
async def bot_status(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        await answer_bot(message, "⛔ Owner only.")
        return

    status = await get_config("status", "OFF")
    delay = await get_config("delay", 8)
    cycle = await get_config("cycle_delay", 15)
    msg = await get_config("msg", "")
    chats = await chats_col.count_documents({})
    groups = await groups_col.count_documents({})

    await answer_bot(
        message,
        "📊 **System Status**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Auto Broadcast: `{status}`\n"
        f"⏱ Delay: `{delay}s`\n"
        f"🔄 Cycle: `{cycle} min`\n"
        f"🎯 Targets: `{chats}`\n"
        f"👥 Groups: `{groups}`\n"
        f"📝 Message: `{len(msg)} chars`"
    )


# =========================================================
# BOT GROUP TRACKING
# =========================================================

@dp.my_chat_member()
async def bot_group_update(update):
    chat = update.chat

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    new_status = update.new_chat_member.status

    if new_status in ("member", "administrator"):
        await groups_col.update_one(
            {"_id": chat.id},
            {
                "$set": {
                    "chat_id": chat.id,
                    "title": chat.title or "Unknown",
                    "enabled": True,
                    "updated_at": now(),
                },
                "$setOnInsert": {
                    "force_sub": True,
                },
            },
            upsert=True
        )
        log.info(f"Bot added to group {chat.id}")

    elif new_status in ("left", "kicked"):
        await groups_col.delete_one({"_id": chat.id})
        log.info(f"Bot removed from group {chat.id}")


async def group_admin(message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False

    try:
        member = await bot.get_chat_member(
            message.chat.id,
            message.from_user.id
        )
        return member.status in ("administrator", "creator")
    except Exception as e:
        log.warning(f"Group admin check failed: {e}")
        return False


async def ensure_group_record(chat):
    await groups_col.update_one(
        {"_id": chat.id},
        {
            "$set": {
                "chat_id": chat.id,
                "title": chat.title or "Unknown",
                "updated_at": now(),
            },
            "$setOnInsert": {
                "enabled": True,
                "force_sub": True,
            },
        },
        upsert=True
    )


# =========================================================
# GROUP COMMANDS
# =========================================================

@dp.message(Command("groups"))
async def groups_list(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        await answer_bot(message, "⛔ Owner only.")
        return

    groups = await groups_col.find().sort("updated_at", -1).to_list(length=1000)

    if not groups:
        await answer_bot(message, "ℹ️ No groups registered.")
        return

    text = "👥 **Registered Groups**\n\n"

    for i, group in enumerate(groups, 1):
        state = "ON" if group.get("enabled", True) else "OFF"
        text += (
            f"{i}. **{group.get('title', 'Unknown')}**\n"
            f"🆔 `{group.get('chat_id')}`\n"
            f"⚡ `{state}`\n\n"
        )

        if len(text) > 3900:
            await answer_bot(message, text)
            text = ""

    if text:
        await answer_bot(message, text)


@dp.message(Command("groupstatus"))
async def group_status(message: AiogramMessage):
    if not await group_admin(message):
        return

    await ensure_group_record(message.chat)

    group = await groups_col.find_one({"_id": message.chat.id})

    await answer_bot(
        message,
        "👥 **Group Status**\n\n"
        f"📌 `{message.chat.title}`\n"
        f"⚡ System: `{'ON' if group.get('enabled', True) else 'OFF'}`\n"
        f"🔒 ForceSub: `{'ON' if group.get('force_sub', True) else 'OFF'}`"
    )


@dp.message(Command("groupenable"))
async def group_enable(message: AiogramMessage):
    if not await group_admin(message):
        return

    await ensure_group_record(message.chat)

    await groups_col.update_one(
        {"_id": message.chat.id},
        {"$set": {"enabled": True, "updated_at": now()}}
    )

    await answer_bot(message, "✅ Group system **enabled**.")


@dp.message(Command("groupdisable"))
async def group_disable(message: AiogramMessage):
    if not await group_admin(message):
        return

    await ensure_group_record(message.chat)

    await groups_col.update_one(
        {"_id": message.chat.id},
        {"$set": {"enabled": False, "updated_at": now()}}
    )

    await answer_bot(message, "🛑 Group system **disabled**.")


@dp.message(Command("groupinfo"))
async def group_info(message: AiogramMessage):
    if not await group_admin(message):
        return

    await ensure_group_record(message.chat)

    group = await groups_col.find_one({"_id": message.chat.id})

    await answer_bot(
        message,
        "👥 **Group Information**\n\n"
        f"📌 `{group.get('title', 'Unknown')}`\n"
        f"🆔 `{group.get('chat_id')}`\n"
        f"⚡ Enabled: `{group.get('enabled', True)}`\n"
        f"🔒 ForceSub: `{group.get('force_sub', True)}`"
    )


@dp.message(Command("groupforcesub"))
async def group_forcesub(message: AiogramMessage):
    if not await group_admin(message):
        return

    await ensure_group_record(message.chat)

    args = (message.text or "").split()

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await answer_bot(message, "Usage: `/groupforcesub on` or `/groupforcesub off`")
        return

    enabled = args[1].lower() == "on"

    await groups_col.update_one(
        {"_id": message.chat.id},
        {"$set": {"force_sub": enabled, "updated_at": now()}}
    )

    await answer_bot(
        message,
        f"🔒 Group ForceSub: `{'ON' if enabled else 'OFF'}`"
    )


# =========================================================
# GROUP FORCE SUB MESSAGE CHECK
# =========================================================

async def group_guard(message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return True

    if not message.from_user or message.from_user.is_bot:
        return True

    group = await groups_col.find_one({"_id": message.chat.id})

    if not group or not group.get("enabled", True):
        return True

    if not group.get("force_sub", True):
        return True

    if is_owner(message.from_user.id):
        return True

    status = await check_bot_force_sub(message.from_user.id)

    if status is True:
        return True

    if status is None:
        return True

    try:
        await message.reply(
            "🔒 **Force Subscription Required**\n\n"
            "Required channels join karke message send karo.",
            reply_markup=await bot_force_sub_keyboard()
        )
    except Exception as e:
        log.warning(f"Group force-sub reply failed: {e}")

    return False


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message_guard(message: AiogramMessage):
    await group_guard(message)


# =========================================================
# CALLBACK
# =========================================================

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    status = await check_bot_force_sub(callback.from_user.id)

    if status is True:
        await callback.answer("✅ Subscription verified!", show_alert=True)

        try:
            await callback.message.edit_text(
                "✅ **Subscription verified!**\n\n"
                "Ab bot use kar sakte ho."
            )
        except Exception:
            pass

    elif status is False:
        await callback.answer(
            "❌ Pehle sab channels join karo.",
            show_alert=True
        )
    else:
        await callback.answer(
            "⚠️ Check temporarily failed.",
            show_alert=True
        )


# =========================================================
# OWNER BOT SETTINGS
# =========================================================

@dp.message(Command("setmsg"))
async def bot_setmsg(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    args = (message.text or "").split(maxsplit=1)

    if len(args) < 2:
        await answer_bot(message, "Usage: `/setmsg Your message`")
        return

    text = args[1]

    if len(text) > 4096:
        await answer_bot(message, "❌ Message too long.")
        return

    await set_config("msg", text)
    await answer_bot(message, "✅ Broadcast message updated.")


@dp.message(Command("setdelay"))
async def bot_setdelay(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    args = (message.text or "").split()

    if len(args) < 2 or not args[1].isdigit():
        await answer_bot(message, "Usage: `/setdelay 10`")
        return

    value = int(args[1])

    if value < 3:
        await answer_bot(message, "❌ Minimum delay is 3 seconds.")
        return

    await set_config("delay", value)
    await answer_bot(message, f"✅ Delay set to `{value}s`.")


@dp.message(Command("setcycle"))
async def bot_setcycle(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    args = (message.text or "").split()

    if len(args) < 2 or not args[1].isdigit():
        await answer_bot(message, "Usage: `/setcycle 15`")
        return

    value = int(args[1])

    if value < 1:
        await answer_bot(message, "❌ Minimum cycle is 1 minute.")
        return

    await set_config("cycle_delay", value)
    await answer_bot(message, f"✅ Cycle set to `{value} minutes`.")


@dp.message(Command("forcesub"))
async def bot_forcesub(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    args = (message.text or "").split()

    if len(args) < 2:
        await answer_bot(
            message,
            "Usage:\n"
            "`/forcesub @channel1 @channel2 @channel3`\n\n"
            "Private:\n"
            "`/forcesub -1001234567890 -1009876543210`"
        )
        return

    channels = [clean_channel(x) for x in args[1:4]]

    # Validate userbot access to configured channels before saving.
    for channel in channels:
        try:
            await userbot.get_chat(channel)
        except Exception as e:
            await answer_bot(
                message,
                f"⚠️ User account cannot access `{channel}`.\n\n"
                f"`{e}`"
            )
            return

    await set_force_sub_channels(channels)

    await answer_bot(
        message,
        "🔒 **ForceSub Updated**\n\n" +
        "\n".join(
            f"{i}. `{ch}`"
            for i, ch in enumerate(channels, 1)
        )
    )


@dp.message(Command("forcesuboff"))
async def bot_forcesuboff(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    await set_force_sub_channels([])
    await answer_bot(message, "🔓 ForceSub disabled.")


@dp.message(Command("autobroadcast"))
async def bot_autobroadcast(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    args = (message.text or "").split()

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await answer_bot(message, "Usage: `/autobroadcast on` or `/autobroadcast off`")
        return

    state = args[1].upper()

    if state == "ON":
        force_status = await userbot_force_sub_ok()

        if force_status is False:
            await answer_bot(
                message,
                "❌ User account required ForceSub channels me joined nahi hai."
            )
            return

        if force_status is None:
            await answer_bot(message, "⚠️ ForceSub check failed.")
            return

    await set_config("status", state)
    await answer_bot(message, f"🤖 Auto Broadcast `{state}`")


@dp.message(Command("stats"))
async def bot_stats(message: AiogramMessage):
    if not is_owner(message.from_user.id):
        return

    status = await get_config("status", "OFF")
    delay = await get_config("delay", 8)
    cycle = await get_config("cycle_delay", 15)
    channels = await get_force_sub_channels()
    chats = await chats_col.count_documents({})
    groups = await groups_col.count_documents({})

    total = await get_stat("total_attempts")
    success = await get_stat("success_sent")
    failed = await get_stat("failed_sent")

    await answer_bot(
        message,
        "📊 **AutoForward Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Status: `{status}`\n"
        f"⏱ Delay: `{delay}s`\n"
        f"🔄 Cycle: `{cycle} min`\n"
        f"🔒 ForceSub: `{len(channels)} channels`\n"
        f"🎯 Targets: `{chats}`\n"
        f"👥 Groups: `{groups}`\n\n"
        f"📨 Attempts: `{total}`\n"
        f"✅ Success: `{success}`\n"
        f"❌ Failed: `{failed}`"
    )


# =========================================================
# USERBOT FORCE-SUB FOR INCOMING PRIVATE MESSAGES
# =========================================================

@userbot.on_message(filters.private & ~filters.me & filters.incoming)
async def userbot_private_guard(client, message: Message):
    if not message.from_user:
        return

    channels = await get_force_sub_channels()

    if not channels:
        return

    # Owner's own account is not checked here.
    if message.from_user.id == OWNER_ID:
        return

    status = await check_user_membership(
        userbot,
        channels[0],
        message.from_user.id
    ) if channels else True

    # Check all channels.
    if status is True:
        for channel in channels[1:]:
            result = await check_user_membership(
                userbot,
                channel,
                message.from_user.id
            )
            if result is False:
                status = False
                break
            if result is None:
                status = True
                break

    if status is True:
        return

    if status is None:
        return

    buttons = []

    for index, channel in enumerate(channels, 1):
        link = channel_link(channel)
        if link:
            buttons.append([
                InlineKeyboardButton(
                    f"📢 Join Channel {index}",
                    url=link
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Check Subscription",
            callback_data="noop"
        )
    ])

    try:
        await message.reply_text(
            "🔒 **Force Subscription Required**\n\n"
            "Pehle required channels join karo, phir DM karo.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        log.warning(f"Userbot ForceSub reply failed: {e}")


# =========================================================
# USERBOT COMMAND PARSER
# =========================================================

def command_args(message):
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def resolve_target(value):
    value = str(value).strip()

    if not value:
        raise ValueError("Target missing")

    # Numeric Telegram IDs.
    if value.lstrip("-").isdigit():
        target = int(value)

        # Important: call get_chat so Pyrogram stores/resolves the peer.
        return await userbot.get_chat(target)

    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break

    value = value.rstrip("/")

    if value.startswith("+"):
        return await userbot.join_chat(value)

    if value.startswith("@"):
        return await userbot.get_chat(value)

    return await userbot.get_chat("@" + value)


async def ensure_peer(chat_id):
    try:
        return await userbot.get_chat(chat_id)
    except Exception as e:
        log.warning(f"Peer resolve failed {chat_id}: {e}")
        return None


# =========================================================
# USERBOT COMMANDS
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command("addchat", prefixes=".")
)
async def addchat_handler(client, message: Message):
    try:
        arg = command_args(message)

        if arg:
            chat = await resolve_target(arg)
        else:
            chat = message.chat

        if chat.type not in (
            enums.ChatType.GROUP,
            enums.ChatType.SUPERGROUP,
            enums.ChatType.CHANNEL,
        ):
            await edit_or_reply(message, "❌ Sirf group/supergroup/channel add kar sakte ho.")
            return

        await add_chat_db(
            chat.id,
            chat.title or "Unknown",
            getattr(chat, "username", "") or ""
        )

        await edit_or_reply(
            message,
            "✅ **Chat Added**\n\n"
            f"📌 `{chat.title or 'Unknown'}`\n"
            f"🆔 `{chat.id}`"
        )

    except Exception as e:
        await edit_or_reply(message, f"❌ **Add Chat Failed**\n\n`{e}`")


@userbot.on_message(
    filters.outgoing &
    filters.command("delchat", prefixes=".")
)
async def delchat_handler(client, message: Message):
    try:
        arg = command_args(message)

        if arg:
            chat = await resolve_target(arg)
            chat_id = chat.id
        else:
            chat_id = message.chat.id

        deleted = await delete_chat_db(chat_id)

        if deleted:
            await edit_or_reply(message, f"🗑 Removed: `{chat_id}`")
        else:
            await edit_or_reply(message, "ℹ️ Chat list me nahi hai.")

    except Exception as e:
        await edit_or_reply(message, f"❌ `{e}`")


@userbot.on_message(
    filters.outgoing &
    filters.command("listchats", prefixes=".")
)
async def listchats_handler(client, message: Message):
    try:
        chats = await get_all_chats()

        if not chats:
            await edit_or_reply(message, "ℹ️ **No target chats.**")
            return

        text = "📋 **Target Chats**\n\n"

        for index, chat in enumerate(chats, 1):
            line = (
                f"{index}. **{chat.get('title', 'Unknown')}**\n"
                f"   🆔 `{chat.get('chat_id')}`\n\n"
            )

            if len(text) + len(line) > 3900:
                await message.edit_text(text)
                text = ""

            text += line

        if text:
            await message.edit_text(text)

    except Exception as e:
        await edit_or_reply(message, f"❌ `{e}`")


@userbot.on_message(
    filters.outgoing &
    filters.command("setmsg", prefixes=".")
)
async def setmsg_handler(client, message: Message):
    text = command_args(message)

    if not text:
        await edit_or_reply(message, "❌ `.setmsg Your message`")
        return

    if len(text) > 4096:
        await edit_or_reply(message, "❌ Message too long.")
        return

    await set_config("msg", text)
    await edit_or_reply(message, "✅ **Message updated.**")


@userbot.on_message(
    filters.outgoing &
    filters.command("setdelay", prefixes=".")
)
async def setdelay_handler(client, message: Message):
    value = command_args(message)

    if not value.isdigit() or int(value) < 3:
        await edit_or_reply(message, "❌ `.setdelay 10` — minimum 3 seconds.")
        return

    value = int(value)

    await set_config("delay", value)
    await edit_or_reply(message, f"✅ Delay set to `{value}s`")


@userbot.on_message(
    filters.outgoing &
    filters.command("setcycle", prefixes=".")
)
async def setcycle_handler(client, message: Message):
    value = command_args(message)

    if not value.isdigit() or int(value) < 1:
        await edit_or_reply(message, "❌ `.setcycle 15` — minimum 1 minute.")
        return

    value = int(value)

    await set_config("cycle_delay", value)
    await edit_or_reply(message, f"✅ Cycle break set to `{value} minutes`")


@userbot.on_message(
    filters.outgoing &
    filters.command("forcesub", prefixes=".")
)
async def forcesub_handler(client, message: Message):
    args = (message.text or "").split()

    if len(args) < 2:
        await edit_or_reply(
            message,
            "❌ Example:\n"
            "`.forcesub @channel1 @channel2 @channel3`\n\n"
            "Private:\n"
            "`.forcesub -1001234567890 -1009876543210`"
        )
        return

    channels = [clean_channel(x) for x in args[1:4]]

    # Resolve first, then save. This fixes many peer-cache problems.
    for channel in channels:
        try:
            await userbot.get_chat(channel)
        except Exception as e:
            await edit_or_reply(
                message,
                f"❌ Channel access failed:\n`{channel}`\n\n`{e}`"
            )
            return

    await set_force_sub_channels(channels)

    await edit_or_reply(
        message,
        "🔒 **Force-Sub Updated**\n\n" +
        "\n".join(
            f"{i}. `{ch}`"
            for i, ch in enumerate(channels, 1)
        )
    )


@userbot.on_message(
    filters.outgoing &
    filters.command("forcesuboff", prefixes=".")
)
async def forcesuboff_handler(client, message: Message):
    await set_force_sub_channels([])
    await edit_or_reply(message, "🔓 **Force-Sub disabled.**")


@userbot.on_message(
    filters.outgoing &
    filters.command("automsg", prefixes=".")
)
async def automsg_handler(client, message: Message):
    args = (message.text or "").split()

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await edit_or_reply(
            message,
            "❌ `.automsg on`\n❌ `.automsg off`"
        )
        return

    state = args[1].upper()

    if state == "ON":
        force_status = await userbot_force_sub_ok()

        if force_status is False:
            await edit_or_reply(
                message,
                "❌ User account required ForceSub channel me joined nahi hai."
            )
            return

        if force_status is None:
            await edit_or_reply(
                message,
                "⚠️ ForceSub check failed. Try again."
            )
            return

    await set_config("status", state)
    await edit_or_reply(message, f"🤖 **Auto Messaging `{state}`**")


@userbot.on_message(
    filters.outgoing &
    filters.command("stats", prefixes=".")
)
async def stats_handler(client, message: Message):
    status = await get_config("status", "OFF")
    delay = await get_config("delay", 8)
    cycle = await get_config("cycle_delay", 15)
    channels = await get_force_sub_channels()
    chats = await chats_col.count_documents({})

    total = await get_stat("total_attempts")
    success = await get_stat("success_sent")
    failed = await get_stat("failed_sent")

    await edit_or_reply(
        message,
        "📊 **AutoForward Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Status: `{status}`\n"
        f"⏱ Delay: `{delay}s`\n"
        f"🔄 Cycle: `{cycle} min`\n"
        f"🔒 ForceSub: `{len(channels)}` channels\n"
        f"🎯 Targets: `{chats}`\n\n"
        f"📨 Attempts: `{total}`\n"
        f"✅ Success: `{success}`\n"
        f"❌ Failed: `{failed}`"
    )


@userbot.on_message(
    filters.outgoing &
    filters.command("help", prefixes=".")
)
async def userbot_help(client, message: Message):
    await edit_or_reply(
        message,
        "🤖 **Userbot Commands**\n\n"
        "`.addchat @group`\n"
        "`.addchat -100123456789`\n"
        "`.delchat @group`\n"
        "`.listchats`\n\n"
        "`.setmsg text`\n"
        "`.setdelay 10`\n"
        "`.setcycle 15`\n\n"
        "`.forcesub @channel1 @channel2 @channel3`\n"
        "`.forcesub -100123 -100456`\n"
        "`.forcesuboff`\n\n"
        "`.automsg on`\n"
        "`.automsg off`\n"
        "`.stats`"
    )


# =========================================================
# SAFE SEND
# =========================================================

async def safe_send(chat_id, text):
    try:
        await ensure_peer(chat_id)

        await userbot.send_message(chat_id, text)
        return "success"

    except FloodWait as e:
        wait = max(int(e.value), 1)
        log.warning(f"FloodWait {wait}s for {chat_id}")

        await asyncio.sleep(wait)

        try:
            await userbot.send_message(chat_id, text)
            return "success"
        except Exception as e2:
            log.error(f"Retry failed {chat_id}: {e2}")
            return "failed"

    except RPCError as e:
        log.error(f"Telegram error {chat_id}: {e}")
        return "failed"

    except Exception as e:
        log.error(f"Send error {chat_id}: {e}")
        return "failed"


# =========================================================
# BROADCAST
# =========================================================

async def broadcast_worker():
    while True:
        try:
            await asyncio.sleep(2)

            status = await get_config("status", "OFF")

            if status != "ON":
                continue

            force_status = await userbot_force_sub_ok()

            if force_status is False:
                await set_config("status", "OFF")

                try:
                    await userbot.send_message(
                        "me",
                        "❌ **Auto Msg stopped**\n"
                        "Force-Sub requirement not satisfied."
                    )
                except Exception:
                    pass

                continue

            if force_status is None:
                await asyncio.sleep(10)
                continue

            message_text = await get_config("msg", "")

            if not message_text:
                await asyncio.sleep(5)
                continue

            try:
                delay = max(
                    3,
                    int(await get_config("delay", 8))
                )
                cycle = max(
                    1,
                    int(await get_config("cycle_delay", 15))
                )
            except Exception:
                delay = 8
                cycle = 15

            chats = await get_all_chats()

            if not chats:
                await asyncio.sleep(10)
                continue

            log.info(f"Broadcast round started: {len(chats)} targets")

            for chat in chats:
                if await get_config("status", "OFF") != "ON":
                    break

                chat_id = chat.get("chat_id")

                if not chat_id:
                    continue

                await inc_stat("total_attempts")

                result = await safe_send(chat_id, message_text)

                if result == "success":
                    await inc_stat("success_sent")
                    log.info(
                        f"[SENT] {chat.get('title')} {chat_id}"
                    )
                else:
                    await inc_stat("failed_sent")
                    log.warning(
                        f"[FAILED] {chat.get('title')} {chat_id}"
                    )

                random_delay = random.randint(
                    max(3, delay - 2),
                    delay + 4
                )

                await asyncio.sleep(random_delay)

            if await get_config("status", "OFF") == "ON":
                log.info(
                    f"Round completed. Sleeping {cycle} minutes."
                )
                await asyncio.sleep(cycle * 60)

        except asyncio.CancelledError:
            raise

        except Exception as e:
            log.exception(f"Broadcast worker crashed: {e}")
            await asyncio.sleep(10)


async def broadcast_supervisor():
    while True:
        task = asyncio.create_task(broadcast_worker())

        try:
            await task

        except asyncio.CancelledError:
            task.cancel()
            raise

        except Exception as e:
            log.exception(f"Supervisor caught worker crash: {e}")
            await asyncio.sleep(5)


# =========================================================
# PEER WARMUP
# =========================================================

async def warmup_peers():
    chats = await get_all_chats()

    for item in chats:
        chat_id = item.get("chat_id")

        if not chat_id:
            continue

        try:
            await userbot.get_chat(chat_id)
            log.info(f"Target peer loaded: {chat_id}")
        except Exception as e:
            log.warning(
                f"Target peer warmup failed {chat_id}: {e}"
            )

    channels = await get_force_sub_channels()

    for channel in channels:
        try:
            await userbot.get_chat(channel)
            log.info(f"ForceSub peer loaded: {channel}")
        except Exception as e:
            log.warning(
                f"ForceSub peer load failed {channel}: {e}"
            )


# =========================================================
# MAIN
# =========================================================

async def main():
    await mongo_init()

    await userbot.start()

    me = await userbot.get_me()

    log.info(
        f"User account: {me.id} | {me.first_name or ''}"
    )
    log.info(f"Owner ID: {OWNER_ID}")

    await warmup_peers()

    force_channels = await get_force_sub_channels()

    log.info(
        "ForceSub: " +
        (
            ", ".join(map(str, force_channels))
            if force_channels
            else "disabled"
        )
    )

    await bot.delete_webhook(drop_pending_updates=True)

    broadcast_task = asyncio.create_task(
        broadcast_supervisor()
    )

    log.info("🚀 Userbot + Bot started")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:
        broadcast_task.cancel()

        try:
            await broadcast_task
        except asyncio.CancelledError:
            pass

        try:
            await userbot.stop()
        except Exception:
            pass

        try:
            await bot.session.close()
        except Exception:
            pass

        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(main())
