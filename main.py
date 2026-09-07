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

    await config_col.update_one(
        {"_id": "status"},
        {"$setOnInsert": {"value": "OFF"}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "delay"},
        {"$setOnInsert": {"value": 8}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "cycle_delay"},
        {"$setOnInsert": {"value": 15}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "msg"},
        {"$setOnInsert": {"value": "Default Auto Message"}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "force_sub"},
        {"$setOnInsert": {"value": []}},
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
# PYROGRAM
# =========================================================

userbot = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


# =========================================================
# AIOGRAM BOT
# =========================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(
            b"AutoForward Userbot + Bot is running!"
        )

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    log.info(f"Health server running on port {PORT}")

    server.serve_forever()


Thread(
    target=start_health_server,
    daemon=True
).start()


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(user_id):
    return int(user_id) == OWNER_ID


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
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True
    )


async def delete_chat_db(chat_id):
    result = await chats_col.delete_one(
        {"_id": str(chat_id)}
    )

    return result.deleted_count


async def get_all_chats():
    return await chats_col.find().to_list(length=10000)


# =========================================================
# FORCE SUB CONFIG
# =========================================================

async def get_force_sub_channels():
    channels = await get_config(
        "force_sub",
        []
    )

    if not isinstance(channels, list):
        return []

    return channels[:3]


async def set_force_sub_channels(channels):
    await set_config(
        "force_sub",
        channels[:3]
    )


def clean_channel(value):
    value = value.strip()

    if value.startswith("https://t.me/"):
        value = value.replace(
            "https://t.me/",
            "",
            1
        )

    elif value.startswith("http://t.me/"):
        value = value.replace(
            "http://t.me/",
            "",
            1
        )

    value = value.rstrip("/")

    return value


# =========================================================
# FORCE SUB CHECK
# =========================================================

async def check_user_membership(client, channel, user_id):

    try:
        member = await client.get_chat_member(
            channel,
            user_id
        )

        if member.status in (
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.MEMBER,
        ):
            return True

        if member.status == enums.ChatMemberStatus.RESTRICTED:
            return bool(
                getattr(
                    member,
                    "is_member",
                    False
                )
            )

        return False

    except Exception as e:
        log.warning(
            f"ForceSub check failed "
            f"{channel}: {e}"
        )

        return None


# =========================================================
# BOT FORCE SUB
# =========================================================

async def bot_force_sub_keyboard():
    channels = await get_force_sub_channels()

    buttons = []

    for index, channel in enumerate(channels, 1):

        link = channel

        if not (
            link.startswith("https://t.me/")
            or link.startswith("http://t.me/")
        ):
            if str(link).startswith("-100"):
                link = "https://t.me/"
            else:
                link = f"https://t.me/{str(link).lstrip('@')}"

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

    return AioInlineKeyboardMarkup(
        inline_keyboard=buttons
    )


async def check_bot_force_sub(user_id):

    channels = await get_force_sub_channels()

    if not channels:
        return True

    for channel in channels:

        result = await check_user_membership(
            bot,
            channel,
            user_id
        )

        if result is False:
            return False

        if result is None:
            return None

    return True


# =========================================================
# BOT START
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

        status = await check_bot_force_sub(
            user_id
        )

        if status is False:

            await message.answer(
                "🔒 **Force Subscription Required**\n\n"
                "Pehle required channels join karo.",
                reply_markup=await bot_force_sub_keyboard()
            )

            return

        if status is None:

            await message.answer(
                "⚠️ Force-Sub check temporarily unavailable."
            )

            return

    await message.answer(
        "🚀 **AutoForward Manager**\n\n"
        "Bot successfully working.\n\n"
        "Use /help for commands."
    )


# =========================================================
# BOT HELP
# =========================================================

@dp.message(Command("help"))
async def bot_help(message: AiogramMessage):

    if message.chat.type == ChatType.PRIVATE:

        status = await check_bot_force_sub(
            message.from_user.id
        )

        if status is not True:
            await message.answer(
                "🔒 Please complete Force-Sub first.",
                reply_markup=await bot_force_sub_keyboard()
            )
            return

    text = (
        "🤖 **Commands**\n\n"
        "/start - Start bot\n"
        "/help - Help\n"
        "/status - System status\n"
        "/groups - Added groups\n\n"
        "👥 **Group Commands**\n"
        "/groupstatus\n"
        "/groupenable\n"
        "/groupdisable\n"
        "/groupinfo"
    )

    await message.answer(text)


# =========================================================
# BOT STATUS
# =========================================================

@dp.message(Command("status"))
async def bot_status(message: AiogramMessage):

    if not is_owner(message.from_user.id):
        return

    status = await get_config(
        "status",
        "OFF"
    )

    delay = await get_config(
        "delay",
        8
    )

    cycle = await get_config(
        "cycle_delay",
        15
    )

    chats = await chats_col.count_documents({})

    await message.answer(
        "📊 **System Status**\n\n"
        f"🤖 Auto Msg: `{status}`\n"
        f"⏱ Delay: `{delay}s`\n"
        f"🔄 Cycle: `{cycle} min`\n"
        f"🎯 Targets: `{chats}`"
    )


# =========================================================
# BOT GROUP ADD / REMOVE
# =========================================================

@dp.my_chat_member()
async def bot_group_update(update):

    chat = update.chat

    if chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP
    ):
        return

    new_status = update.new_chat_member.status

    if new_status in (
        "member",
        "administrator"
    ):

        await groups_col.update_one(
            {"_id": chat.id},
            {
                "$set": {
                    "chat_id": chat.id,
                    "title": chat.title or "Unknown",
                    "enabled": True,
                    "updated_at": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

        log.info(
            f"Bot added to group {chat.id}"
        )

    elif new_status in (
        "left",
        "kicked"
    ):

        await groups_col.delete_one(
            {"_id": chat.id}
        )


# =========================================================
# GROUP ADMIN CHECK
# =========================================================

async def group_admin(message):

    if message.chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP
    ):
        return False

    member = await bot.get_chat_member(
        message.chat.id,
        message.from_user.id
    )

    return member.status in (
        "administrator",
        "creator"
    )


# =========================================================
# GROUP STATUS
# =========================================================

@dp.message(Command("groupstatus"))
async def group_status(message: AiogramMessage):

    if not await group_admin(message):
        return

    group = await groups_col.find_one(
        {"_id": message.chat.id}
    )

    if not group:
        await message.answer(
            "❌ Group database me registered nahi hai."
        )
        return

    state = group.get(
        "enabled",
        True
    )

    await message.answer(
        "👥 **Group Status**\n\n"
        f"📌 Group: `{message.chat.title}`\n"
        f"⚡ Status: `{ 'ON' if state else 'OFF' }`"
    )


# =========================================================
# GROUP ENABLE
# =========================================================

@dp.message(Command("groupenable"))
async def group_enable(message: AiogramMessage):

    if not await group_admin(message):
        return

    await groups_col.update_one(
        {"_id": message.chat.id},
        {
            "$set": {
                "enabled": True
            }
        },
        upsert=True
    )

    await message.answer(
        "✅ Group system **enabled**."
    )


# =========================================================
# GROUP DISABLE
# =========================================================

@dp.message(Command("groupdisable"))
async def group_disable(message: AiogramMessage):

    if not await group_admin(message):
        return

    await groups_col.update_one(
        {"_id": message.chat.id},
        {
            "$set": {
                "enabled": False
            }
        },
        upsert=True
    )

    await message.answer(
        "🛑 Group system **disabled**."
    )


# =========================================================
# GROUP INFO
# =========================================================

@dp.message(Command("groupinfo"))
async def group_info(message: AiogramMessage):

    if not await group_admin(message):
        return

    group = await groups_col.find_one(
        {"_id": message.chat.id}
    )

    if not group:
        await message.answer(
            "❌ Group registered nahi hai."
        )
        return

    await message.answer(
        "👥 **Group Information**\n\n"
        f"📌 `{group.get('title', 'Unknown')}`\n"
        f"🆔 `{group.get('chat_id')}`\n"
        f"⚡ Enabled: `{group.get('enabled', True)}`"
    )


# =========================================================
# FORCE SUB CALLBACK
# =========================================================

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(
    callback: CallbackQuery
):

    status = await check_bot_force_sub(
        callback.from_user.id
    )

    if status is True:

        await callback.message.edit_text(
            "✅ **Subscription verified!**\n\n"
            "Ab bot use kar sakte ho."
        )

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
# USERBOT FORCE SUB
# =========================================================

async def userbot_force_sub_ok():

    channels = await get_force_sub_channels()

    if not channels:
        return True

    me = await userbot.get_me()

    for channel in channels:

        result = await check_user_membership(
            userbot,
            channel,
            me.id
        )

        if result is False:
            return False

        if result is None:
            return None

    return True


# =========================================================
# PEER RESOLVER
# =========================================================

async def resolve_target(value):

    value = str(value).strip()

    if value.lstrip("-").isdigit():

        target = int(value)

        try:
            chat = await userbot.get_chat(target)
            return chat
        except Exception:
            pass

    if value.startswith("https://t.me/"):
        value = value.replace(
            "https://t.me/",
            "",
            1
        )

    if value.startswith("http://t.me/"):
        value = value.replace(
            "http://t.me/",
            "",
            1
        )

    value = value.rstrip("/")

    if value.startswith("+"):
        return await userbot.join_chat(value)

    if not value.startswith("@"):
        value = "@" + value

    return await userbot.get_chat(value)


# =========================================================
# ADD CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "addchat",
        prefixes="."
    )
)
async def addchat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    try:

        if len(args) == 1:

            chat = message.chat

        else:

            chat = await resolve_target(
                args[1]
            )

        await add_chat_db(
            chat.id,
            chat.title or chat.first_name or "Unknown",
            getattr(chat, "username", "")
        )

        await message.edit_text(
            "✅ **Chat Added**\n\n"
            f"📌 `{chat.title or chat.first_name or 'Unknown'}`\n"
            f"🆔 `{chat.id}`"
        )

    except Exception as e:

        await message.edit_text(
            f"❌ **Add Chat Failed**\n\n`{e}`"
        )


# =========================================================
# DELETE CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "delchat",
        prefixes="."
    )
)
async def delchat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    try:

        if len(args) == 1:

            chat_id = message.chat.id

        else:

            chat = await resolve_target(
                args[1]
            )

            chat_id = chat.id

        deleted = await delete_chat_db(
            chat_id
        )

        if deleted:

            await message.edit_text(
                f"🗑 **Removed:** `{chat_id}`"
            )

        else:

            await message.edit_text(
                "ℹ️ Chat list me nahi hai."
            )

    except Exception as e:

        await message.edit_text(
            f"❌ `{e}`"
        )


# =========================================================
# LIST CHATS
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "listchats",
        prefixes="."
    )
)
async def listchats_handler(
    client,
    message: Message
):

    chats = await get_all_chats()

    if not chats:

        await message.edit_text(
            "ℹ️ **No target chats.**"
        )

        return

    text = "📋 **Target Chats**\n\n"

    for index, chat in enumerate(
        chats,
        1
    ):

        line = (
            f"{index}. "
            f"**{chat.get('title', 'Unknown')}**\n"
            f"   `{chat.get('chat_id')}`\n\n"
        )

        if len(text) + len(line) > 3900:

            await message.edit_text(
                text
            )

            return

        text += line

    await message.edit_text(
        text
    )


# =========================================================
# SET MESSAGE
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "setmsg",
        prefixes="."
    )
)
async def setmsg_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        await message.edit_text(
            "❌ `.setmsg Your message`"
        )

        return

    text = args[1]

    if len(text) > 4096:

        await message.edit_text(
            "❌ Message too long."
        )

        return

    await set_config(
        "msg",
        text
    )

    await message.edit_text(
        "✅ **Message updated.**"
    )


# =========================================================
# SET DELAY
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "setdelay",
        prefixes="."
    )
)
async def setdelay_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if (
        len(args) < 2
        or not args[1].isdigit()
        or int(args[1]) < 1
    ):

        await message.edit_text(
            "❌ `.setdelay 10`"
        )

        return

    value = int(args[1])

    await set_config(
        "delay",
        value
    )

    await message.edit_text(
        f"✅ Delay set to `{value}s`"
    )


# =========================================================
# SET CYCLE
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "setcycle",
        prefixes="."
    )
)
async def setcycle_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if (
        len(args) < 2
        or not args[1].isdigit()
    ):

        await message.edit_text(
            "❌ `.setcycle 15`"
        )

        return

    value = int(args[1])

    if value < 1:

        await message.edit_text(
            "❌ Minimum cycle `1` minute."
        )

        return

    await set_config(
        "cycle_delay",
        value
    )

    await message.edit_text(
        f"✅ Cycle break set to `{value} minutes`"
    )


# =========================================================
# FORCE SUB COMMAND
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "forcesub",
        prefixes="."
    )
)
async def forcesub_handler(
    client,
    message: Message
):

    args = message.text.split()

    if len(args) < 2:

        await message.edit_text(
            "❌ Example:\n"
            "`.forcesub @channel1 @channel2 @channel3`\n\n"
            "Private:\n"
            "`.forcesub -1001234567890 -1009876543210`"
        )

        return

    channels = []

    for value in args[1:4]:

        value = clean_channel(
            value
        )

        channels.append(
            value
        )

    await set_force_sub_channels(
        channels
    )

    await message.edit_text(
        "🔒 **Force-Sub Updated**\n\n"
        + "\n".join(
            f"{i}. `{ch}`"
            for i, ch in enumerate(
                channels,
                1
            )
        )
    )


# =========================================================
# FORCE SUB OFF
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "forcesuboff",
        prefixes="."
    )
)
async def forcesuboff_handler(
    client,
    message: Message
):

    await set_force_sub_channels(
        []
    )

    await message.edit_text(
        "🔓 **Force-Sub disabled.**"
    )


# =========================================================
# AUTO MSG
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "automsg",
        prefixes="."
    )
)
async def automsg_handler(
    client,
    message: Message
):

    args = message.text.split()

    if (
        len(args) < 2
        or args[1].lower()
        not in ("on", "off")
    ):

        await message.edit_text(
            "❌ `.automsg on`\n"
            "❌ `.automsg off`"
        )

        return

    state = args[1].upper()

    if state == "ON":

        status = await userbot_force_sub_ok()

        if status is False:

            await message.edit_text(
                "❌ User account required Force-Sub channel me joined nahi hai."
            )

            return

        if status is None:

            await message.edit_text(
                "⚠️ Force-Sub check failed. Try again."
            )

            return

    await set_config(
        "status",
        state
    )

    await message.edit_text(
        f"🤖 **Auto Messaging `{state}`**"
    )


# =========================================================
# STATS
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "stats",
        prefixes="."
    )
)
async def stats_handler(
    client,
    message: Message
):

    status = await get_config(
        "status",
        "OFF"
    )

    delay = await get_config(
        "delay",
        8
    )

    cycle = await get_config(
        "cycle_delay",
        15
    )

    channels = await get_force_sub_channels()

    chats = await chats_col.count_documents({})

    total = await get_stat(
        "total_attempts"
    )

    success = await get_stat(
        "success_sent"
    )

    failed = await get_stat(
        "failed_sent"
    )

    await message.edit_text(
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


# =========================================================
# USERBOT HELP
# =========================================================

@userbot.on_message(
    filters.outgoing &
    filters.command(
        "help",
        prefixes="."
    )
)
async def userbot_help(
    client,
    message: Message
):

    await message.edit_text(
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

        await userbot.send_message(
            chat_id,
            text
        )

        return "success"

    except FloodWait as e:

        wait = max(
            int(e.value),
            1
        )

        log.warning(
            f"FloodWait {wait}s"
        )

        await asyncio.sleep(
            wait
        )

        try:

            await userbot.send_message(
                chat_id,
                text
            )

            return "success"

        except Exception as e2:

            log.error(
                f"Retry failed {chat_id}: {e2}"
            )

            return "failed"

    except RPCError as e:

        log.error(
            f"Telegram error {chat_id}: {e}"
        )

        return "failed"

    except Exception as e:

        log.error(
            f"Send error {chat_id}: {e}"
        )

        return "failed"


# =========================================================
# BROADCAST LOOP
# =========================================================

async def broadcast_worker():

    while True:

        try:

            await asyncio.sleep(2)

            status = await get_config(
                "status",
                "OFF"
            )

            if status != "ON":
                continue

            force_status = await userbot_force_sub_ok()

            if force_status is False:

                await set_config(
                    "status",
                    "OFF"
                )

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

            message_text = await get_config(
                "msg",
                ""
            )

            if not message_text:
                continue

            try:

                delay = max(
                    3,
                    int(
                        await get_config(
                            "delay",
                            8
                        )
                    )
                )

                cycle = max(
                    1,
                    int(
                        await get_config(
                            "cycle_delay",
                            15
                        )
                    )
                )

            except Exception:

                delay = 8
                cycle = 15

            chats = await get_all_chats()

            if not chats:
                continue

            log.info(
                f"Broadcast round started: "
                f"{len(chats)} targets"
            )

            for chat in chats:

                status = await get_config(
                    "status",
                    "OFF"
                )

                if status != "ON":
                    break

                chat_id = chat.get(
                    "chat_id"
                )

                await inc_stat(
                    "total_attempts"
                )

                result = await safe_send(
                    chat_id,
                    message_text
                )

                if result == "success":

                    await inc_stat(
                        "success_sent"
                    )

                    log.info(
                        f"[SENT] "
                        f"{chat.get('title')} "
                        f"{chat_id}"
                    )

                else:

                    await inc_stat(
                        "failed_sent"
                    )

                    log.warning(
                        f"[FAILED] "
                        f"{chat.get('title')} "
                        f"{chat_id}"
                    )

                random_delay = random.randint(
                    max(3, delay - 2),
                    delay + 4
                )

                await asyncio.sleep(
                    random_delay
                )

            if await get_config(
                "status",
                "OFF"
            ) == "ON":

                log.info(
                    f"Round completed. "
                    f"Sleeping {cycle} minutes."
                )

                await asyncio.sleep(
                    cycle * 60
                )

        except asyncio.CancelledError:

            raise

        except Exception as e:

            log.exception(
                f"Broadcast worker crashed: {e}"
            )

            await asyncio.sleep(10)


# =========================================================
# BROADCAST SUPERVISOR
# =========================================================

async def broadcast_supervisor():

    while True:

        task = asyncio.create_task(
            broadcast_worker()
        )

        try:

            await task

        except asyncio.CancelledError:

            task.cancel()

            raise

        except Exception as e:

            log.exception(
                f"Supervisor caught worker crash: {e}"
            )

            await asyncio.sleep(5)


# =========================================================
# STARTUP PEER CACHE
# =========================================================

async def warmup_peers():

    chats = await get_all_chats()

    for item in chats:

        try:

            await userbot.get_chat(
                item.get("chat_id")
            )

        except Exception as e:

            log.warning(
                f"Peer warmup failed "
                f"{item.get('chat_id')}: {e}"
            )

    channels = await get_force_sub_channels()

    for channel in channels:

        try:

            await userbot.get_chat(
                channel
            )

            log.info(
                f"ForceSub peer loaded: {channel}"
            )

        except Exception as e:

            log.warning(
                f"ForceSub peer load failed "
                f"{channel}: {e}"
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    await mongo_init()

    await userbot.start()

    me = await userbot.get_me()

    log.info(
        f"User account: "
        f"{me.id} | "
        f"{me.first_name or ''}"
    )

    log.info(
        f"Owner ID: {OWNER_ID}"
    )

    await warmup_peers()

    force_channels = (
        await get_force_sub_channels()
    )

    log.info(
        "ForceSub: "
        + (
            ", ".join(
                map(str, force_channels)
            )
            if force_channels
            else "disabled"
        )
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    broadcast_task = asyncio.create_task(
        broadcast_supervisor()
    )

    log.info(
        "🚀 Userbot + Bot started"
    )

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

        await userbot.stop()

        await bot.session.close()

        mongo_client.close()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
