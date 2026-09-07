import asyncio
import os
import random
import logging
from datetime import datetime, timezone
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from motor.motor_asyncio import AsyncIOMotorClient

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
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

log = logging.getLogger("AUTO")


# =========================================================
# ENVIRONMENT
# =========================================================

def required_env(name: str):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


API_ID = int(required_env("API_ID"))
API_HASH = required_env("API_HASH")
SESSION_STRING = required_env("SESSION_STRING")

BOT_TOKEN = required_env("BOT_TOKEN")

MONGO_URI = required_env("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "autoforward")

OWNER_ID_ENV = os.getenv("OWNER_ID", "").strip()

FORCE_SUB_CHANNELS_ENV = os.getenv(
    "FORCE_SUB_CHANNELS",
    ""
).strip()

FORCE_SUB_LINKS_ENV = os.getenv(
    "FORCE_SUB_LINKS",
    ""
).strip()

PORT = int(os.getenv("PORT", "8080"))


# =========================================================
# CLIENTS
# =========================================================

userbot = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

mongo = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000,
)

db = mongo[MONGO_DB]

config_col = db["config"]
chats_col = db["chats"]
stats_col = db["stats"]
users_col = db["users"]
groups_col = db["groups"]


OWNER_ID = int(OWNER_ID_ENV) if OWNER_ID_ENV else 0


# =========================================================
# DEFAULT CONFIG
# =========================================================

DEFAULT_CONFIG = {
    "status": False,
    "message": "Default Auto Message",
    "delay": 15,
    "cycle_delay": 30,
    "force_sub_channels": [],
    "force_sub_links": [],
}


# =========================================================
# TIME
# =========================================================

def now():
    return datetime.now(timezone.utc)


# =========================================================
# MONGO HELPERS
# =========================================================

async def init_db():
    await mongo.admin.command("ping")

    for key, value in DEFAULT_CONFIG.items():
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
        {
            "$set": {
                "value": value,
                "updated_at": now(),
            }
        },
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
    return int(doc.get("value", 0)) if doc else 0


# =========================================================
# PARSERS
# =========================================================

def parse_csv(value):
    if not value:
        return []

    return [
        x.strip()
        for x in value.split(",")
        if x.strip()
    ]


def normalize_channel(value):
    value = value.strip()

    if value.startswith("https://t.me/"):
        value = "@" + value.split("https://t.me/", 1)[1].split("/", 1)[0]

    if value.startswith("t.me/"):
        value = "@" + value.split("t.me/", 1)[1].split("/", 1)[0]

    return value


def channel_link(channel, index):
    links = GLOBAL_FORCE_LINKS

    if index < len(links):
        link = links[index]

        if link:
            return link

    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"

    if channel.startswith("https://t.me/"):
        return channel

    return None


GLOBAL_FORCE_CHANNELS = []
GLOBAL_FORCE_LINKS = []


async def load_force_sub_config():
    global GLOBAL_FORCE_CHANNELS
    global GLOBAL_FORCE_LINKS

    channels = await get_config(
        "force_sub_channels",
        parse_csv(FORCE_SUB_CHANNELS_ENV)
    )

    links = await get_config(
        "force_sub_links",
        parse_csv(FORCE_SUB_LINKS_ENV)
    )

    GLOBAL_FORCE_CHANNELS = [
        normalize_channel(x)
        for x in channels
        if x
    ]

    GLOBAL_FORCE_LINKS = links


# =========================================================
# FORCE SUB BUTTONS - USERBOT
# =========================================================

def user_force_buttons():
    rows = []

    for index, channel in enumerate(GLOBAL_FORCE_CHANNELS):
        link = channel_link(channel, index)

        if link:
            rows.append([
                InlineKeyboardButton(
                    f"📢 Join Channel {index + 1}",
                    url=link
                )
            ])

    rows.append([
        InlineKeyboardButton(
            "✅ Check Subscription",
            callback_data="fs_check"
        )
    ])

    return InlineKeyboardMarkup(rows)


# =========================================================
# FORCE SUB BUTTONS - BOT
# =========================================================

def bot_force_buttons():
    rows = []

    for index, channel in enumerate(GLOBAL_FORCE_CHANNELS):
        link = channel_link(channel, index)

        if link:
            rows.append([
                AioInlineKeyboardButton(
                    text=f"📢 Join Channel {index + 1}",
                    url=link
                )
            ])

    rows.append([
        AioInlineKeyboardButton(
            text="✅ Check Subscription",
            callback_data="fs_check"
        )
    ])

    return AioInlineKeyboardMarkup(inline_keyboard=rows)


# =========================================================
# USERBOT FORCE SUB CHECK
# =========================================================

async def userbot_check_subscription(user_id):
    if not GLOBAL_FORCE_CHANNELS:
        return True

    unknown = False

    for channel in GLOBAL_FORCE_CHANNELS:
        try:
            member = await userbot.get_chat_member(
                channel,
                user_id
            )

            status = str(member.status).lower()

            if any(x in status for x in [
                "left",
                "kicked",
                "banned"
            ]):
                return False

        except Exception as e:
            log.warning(
                "Userbot ForceSub check failed %s: %s",
                channel,
                e
            )
            unknown = True

    if unknown:
        return None

    return True


# =========================================================
# BOT FORCE SUB CHECK
# =========================================================

async def bot_check_subscription(user_id):
    if not GLOBAL_FORCE_CHANNELS:
        return True

    unknown = False

    for channel in GLOBAL_FORCE_CHANNELS:
        try:
            member = await bot.get_chat_member(
                channel,
                user_id
            )

            status = str(member.status).lower()

            if any(x in status for x in [
                "left",
                "kicked"
            ]):
                return False

        except Exception as e:
            log.warning(
                "Bot ForceSub check failed %s: %s",
                channel,
                e
            )
            unknown = True

    if unknown:
        return None

    return True


# =========================================================
# FORCE SUB MESSAGE - USER ACCOUNT
# =========================================================

async def send_user_force_sub(message):
    await message.reply_text(
        "🔒 **Force Subscription Required**\n\n"
        "Pehle neeche diye gaye channels join karo.\n"
        "Join karne ke baad **Check Subscription** dabao.",
        reply_markup=user_force_buttons(),
        disable_web_page_preview=True
    )


# =========================================================
# USERBOT INCOMING DM FORCE SUB
# =========================================================

@userbot.on_message(
    filters.private & ~filters.me
)
async def userbot_private_message(_, message):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if user_id == OWNER_ID:
        return

    result = await userbot_check_subscription(user_id)

    if result is True:
        return

    if result is None:
        await message.reply_text(
            "⚠️ Subscription check temporarily unavailable.\n"
            "Please try again after a few seconds."
        )
        return

    # Avoid repeatedly sending ForceSub messages
    user = await users_col.find_one({"_id": user_id})

    if user:
        last_prompt = user.get("last_force_prompt")

        if last_prompt:
            seconds = (
                now() - last_prompt
            ).total_seconds()

            if seconds < 60:
                return

    await users_col.update_one(
        {"_id": user_id},
        {
            "$set": {
                "last_force_prompt": now(),
                "updated_at": now(),
            }
        },
        upsert=True
    )

    await send_user_force_sub(message)


# =========================================================
# USERBOT FORCE SUB CALLBACK
# =========================================================

@userbot.on_callback_query(
    filters.regex("^fs_check$")
)
async def userbot_force_callback(_, query):

    user_id = query.from_user.id

    result = await userbot_check_subscription(user_id)

    if result is True:
        await query.answer(
            "✅ Subscription verified!",
            show_alert=True
        )

        try:
            await query.message.edit_text(
                "✅ **Verified!**\n\n"
                "Aap subscribed ho. Ab DM normally use kar sakte ho."
            )
        except Exception:
            pass

        return

    if result is None:
        await query.answer(
            "⚠️ Check temporarily unavailable.",
            show_alert=True
        )
        return

    await query.answer(
        "❌ Pehle sabhi channels join karo.",
        show_alert=True
    )


# =========================================================
# BOT /START
# =========================================================

@dp.message(CommandStart())
async def bot_start(message: Message):

    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id

    if user_id == OWNER_ID:
        await message.answer(
            "👑 **Owner Panel**\n\n"
            "Bot is online.\n"
            "Use /panel for controls."
        )
        return

    result = await bot_check_subscription(user_id)

    if result is True:
        await message.answer(
            "✅ Welcome!\n\n"
            "Aap verified ho."
        )
        return

    if result is None:
        await message.answer(
            "⚠️ Subscription check temporarily unavailable."
        )
        return

    await message.answer(
        "🔒 **Subscription Required**\n\n"
        "Pehle sabhi required channels join karo.",
        reply_markup=bot_force_buttons()
    )


# =========================================================
# BOT FORCE SUB CALLBACK
# =========================================================

@dp.callback_query(F.data == "fs_check")
async def bot_force_callback(query: CallbackQuery):

    user_id = query.from_user.id

    result = await bot_check_subscription(user_id)

    if result is True:
        await query.answer(
            "✅ Subscription verified!",
            show_alert=True
        )

        try:
            await query.message.edit_text(
                "✅ **Verified!**\n\n"
                "Ab bot use kar sakte ho."
            )
        except Exception:
            pass

        return

    if result is None:
        await query.answer(
            "⚠️ Check temporarily unavailable.",
            show_alert=True
        )
        return

    await query.answer(
        "❌ Sabhi required channels join karo.",
        show_alert=True
    )


# =========================================================
# BOT ALL PRIVATE MESSAGES
# =========================================================

@dp.message(F.chat.type == ChatType.PRIVATE)
async def bot_private_message(message: Message):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if user_id == OWNER_ID:
        return

    # Commands handled separately
    if message.text and message.text.startswith("/"):
        return

    result = await bot_check_subscription(user_id)

    if result is True:
        return

    if result is None:
        await message.answer(
            "⚠️ Subscription check temporarily unavailable."
        )
        return

    user = await users_col.find_one({"_id": user_id})

    if user:
        last_prompt = user.get("last_force_prompt")

        if last_prompt:
            seconds = (
                now() - last_prompt
            ).total_seconds()

            if seconds < 60:
                return

    await users_col.update_one(
        {"_id": user_id},
        {
            "$set": {
                "last_force_prompt": now(),
                "updated_at": now(),
            }
        },
        upsert=True
    )

    await message.answer(
        "🔒 **Force Subscription Required**\n\n"
        "Pehle channels join karo.",
        reply_markup=bot_force_buttons()
    )


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(user_id):
    return user_id == OWNER_ID


async def owner_only(message):
    if not message.from_user:
        return False

    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Owner only.")
        return False

    return True


# =========================================================
# BOT PANEL
# =========================================================

@dp.message(Command("panel"))
async def bot_panel(message: Message):

    if not await owner_only(message):
        return

    active = await chats_col.count_documents({
        "enabled": True
    })

    status = await get_config("status", False)

    await message.answer(
        "👑 **AUTO SYSTEM PANEL**\n\n"
        f"📡 Broadcast: {'ON 🟢' if status else 'OFF 🔴'}\n"
        f"💬 Active Chats: `{active}`\n"
        f"📢 ForceSub Channels: `{len(GLOBAL_FORCE_CHANNELS)}`\n\n"
        "Commands:\n"
        "/status\n"
        "/help"
    )


# =========================================================
# BOT STATUS
# =========================================================

@dp.message(Command("status"))
async def bot_status(message: Message):

    if not await owner_only(message):
        return

    status = await get_config("status", False)
    delay = await get_config("delay", 15)
    cycle = await get_config("cycle_delay", 30)

    total = await chats_col.count_documents({})
    active = await chats_col.count_documents({
        "enabled": True
    })

    sent = await get_stat("success_sent")
    failed = await get_stat("failed_sent")
    floods = await get_stat("flood_waits")

    await message.answer(
        "📊 **System Status**\n\n"
        f"Broadcast: {'🟢 ON' if status else '🔴 OFF'}\n"
        f"Total Chats: `{total}`\n"
        f"Active Chats: `{active}`\n"
        f"Delay: `{delay}s`\n"
        f"Cycle: `{cycle} min`\n\n"
        f"✅ Sent: `{sent}`\n"
        f"❌ Failed: `{failed}`\n"
        f"⏳ FloodWait: `{floods}`"
    )


# =========================================================
# BOT HELP
# =========================================================

@dp.message(Command("help"))
async def bot_help(message: Message):

    if not await owner_only(message):
        return

    await message.answer(
        "🛠 **Commands**\n\n"
        "/panel - Control panel\n"
        "/status - System status\n\n"
        "User Account Commands:\n"
        "`.addchat`\n"
        "`.delchat`\n"
        "`.listchats`\n"
        "`.setmsg`\n"
        "`.setdelay`\n"
        "`.setcycle`\n"
        "`.automsg on/off`\n"
        "`.forcesub`\n"
        "`.forcesublinks`\n"
        "`.enablechat`\n"
        "`.disablechat`\n"
        "`.failedchats`\n"
        "`.stats`"
    )


# =========================================================
# ADD CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "addchat",
        prefixes="."
    )
)
async def add_chat(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n`.addchat @username`"
        )
        return

    target = message.command[1]

    try:
        chat = await userbot.get_chat(target)

        chat_id = str(chat.id)

        await chats_col.update_one(
            {"_id": chat_id},
            {
                "$set": {
                    "title": chat.title or chat.first_name or "Unknown",
                    "username": chat.username,
                    "enabled": True,
                    "updated_at": now(),
                },
                "$setOnInsert": {
                    "created_at": now(),
                    "fail_count": 0,
                }
            },
            upsert=True
        )

        await message.edit(
            f"✅ Added:\n"
            f"`{chat.title or chat.first_name or chat.id}`\n"
            f"ID: `{chat.id}`"
        )

    except Exception as e:
        await message.edit(
            f"❌ Add failed:\n`{e}`"
        )


# =========================================================
# DELETE CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "delchat",
        prefixes="."
    )
)
async def delete_chat(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n`.delchat @username`"
        )
        return

    target = message.command[1]

    deleted = False

    # Direct ID
    result = await chats_col.delete_one({
        "_id": str(target)
    })

    if result.deleted_count:
        deleted = True

    # Username
    if not deleted:
        username = target.lstrip("@")

        result = await chats_col.delete_one({
            "username": username
        })

        if result.deleted_count:
            deleted = True

    if deleted:
        await message.edit("✅ Chat deleted.")
    else:
        await message.edit("❌ Chat not found.")


# =========================================================
# LIST CHATS
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "listchats",
        prefixes="."
    )
)
async def list_chats(_, message):

    if not is_owner(message.from_user.id):
        return

    chats = []

    async for chat in chats_col.find({}).sort(
        "created_at",
        1
    ):
        status = "🟢" if chat.get("enabled", True) else "🔴"

        title = chat.get("title", "Unknown")
        chat_id = chat.get("_id")

        chats.append(
            f"{status} {title} | `{chat_id}`"
        )

    if not chats:
        await message.edit(
            "📭 No chats added."
        )
        return

    text = "📋 **Chats**\n\n" + "\n".join(chats)

    await message.edit(text[:4000])


# =========================================================
# SET MESSAGE
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "setmsg",
        prefixes="."
    )
)
async def set_message(_, message):

    if not is_owner(message.from_user.id):
        return

    text = message.text.split(
        maxsplit=1
    )

    if len(text) < 2:
        await message.edit(
            "❌ Usage:\n`.setmsg Your message`"
        )
        return

    await set_config(
        "message",
        text[1]
    )

    await message.edit(
        "✅ Broadcast message updated."
    )


# =========================================================
# SET DELAY
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "setdelay",
        prefixes="."
    )
)
async def set_delay(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n`.setdelay 15`"
        )
        return

    try:
        value = int(message.command[1])
    except ValueError:
        await message.edit(
            "❌ Number required."
        )
        return

    if value < 5:
        await message.edit(
            "❌ Minimum delay is 5 seconds."
        )
        return

    await set_config(
        "delay",
        value
    )

    await message.edit(
        f"✅ Delay set to `{value}s`."
    )


# =========================================================
# SET CYCLE
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "setcycle",
        prefixes="."
    )
)
async def set_cycle(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n`.setcycle 30`"
        )
        return

    try:
        value = int(message.command[1])
    except ValueError:
        await message.edit(
            "❌ Number required."
        )
        return

    if value < 1:
        await message.edit(
            "❌ Minimum cycle is 1 minute."
        )
        return

    await set_config(
        "cycle_delay",
        value
    )

    await message.edit(
        f"✅ Cycle delay set to `{value} minutes`."
    )


# =========================================================
# AUTOMSG
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "automsg",
        prefixes="."
    )
)
async def auto_msg_command(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n`.automsg on`\n`.automsg off`"
        )
        return

    value = message.command[1].lower()

    if value == "on":
        await set_config("status", True)

        await message.edit(
            "🟢 Auto broadcast **ON**."
        )

    elif value == "off":
        await set_config("status", False)

        await message.edit(
            "🔴 Auto broadcast **OFF**."
        )

    else:
        await message.edit(
            "❌ Use `on` or `off`."
        )


# =========================================================
# FORCE SUB COMMAND
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "forcesub",
        prefixes="."
    )
)
async def force_sub_command(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n"
            "`.forcesub @channel1 @channel2 @channel3`\n\n"
            "Disable:\n"
            "`.forcesub off`"
        )
        return

    args = message.command[1:]

    if len(args) == 1 and args[0].lower() == "off":
        await set_config(
            "force_sub_channels",
            []
        )

        await load_force_sub_config()

        await message.edit(
            "🔴 Force-Sub disabled."
        )
        return

    channels = [
        normalize_channel(x)
        for x in args
    ]

    if len(channels) > 3:
        await message.edit(
            "❌ Maximum 3 Force-Sub channels."
        )
        return

    await set_config(
        "force_sub_channels",
        channels
    )

    await load_force_sub_config()

    await message.edit(
        "✅ Force-Sub updated:\n\n" +
        "\n".join(
            f"{i + 1}. `{x}`"
            for i, x in enumerate(channels)
        )
    )


# =========================================================
# FORCE SUB LINKS
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "forcesublinks",
        prefixes="."
    )
)
async def force_sub_links_command(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ Usage:\n"
            "`.forcesublinks https://t.me/a https://t.me/b`"
        )
        return

    links = message.command[1:]

    if len(links) > 3:
        await message.edit(
            "❌ Maximum 3 links."
        )
        return

    await set_config(
        "force_sub_links",
        links
    )

    await load_force_sub_config()

    await message.edit(
        "✅ Force-Sub links updated."
    )


# =========================================================
# ENABLE CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "enablechat",
        prefixes="."
    )
)
async def enable_chat(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ `.enablechat CHAT_ID`"
        )
        return

    target = message.command[1]

    result = await chats_col.update_one(
        {"_id": str(target)},
        {
            "$set": {
                "enabled": True,
                "updated_at": now(),
            }
        }
    )

    if result.matched_count:
        await message.edit("🟢 Chat enabled.")
    else:
        await message.edit("❌ Chat not found.")


# =========================================================
# DISABLE CHAT
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "disablechat",
        prefixes="."
    )
)
async def disable_chat(_, message):

    if not is_owner(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.edit(
            "❌ `.disablechat CHAT_ID`"
        )
        return

    target = message.command[1]

    result = await chats_col.update_one(
        {"_id": str(target)},
        {
            "$set": {
                "enabled": False,
                "updated_at": now(),
            }
        }
    )

    if result.matched_count:
        await message.edit("🔴 Chat disabled.")
    else:
        await message.edit("❌ Chat not found.")


# =========================================================
# FAILED CHATS
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "failedchats",
        prefixes="."
    )
)
async def failed_chats(_, message):

    if not is_owner(message.from_user.id):
        return

    cursor = chats_col.find({
        "enabled": False,
        "fail_count": {
            "$gt": 0
        }
    })

    items = []

    async for chat in cursor:
        items.append(
            f"🔴 {chat.get('title', 'Unknown')}\n"
            f"ID: `{chat.get('_id')}`\n"
            f"Fails: `{chat.get('fail_count', 0)}`\n"
            f"Error: `{str(chat.get('last_error', ''))[:100]}`"
        )

    if not items:
        await message.edit(
            "✅ No failed chats."
        )
        return

    await message.edit(
        "⚠️ **Failed Chats**\n\n" +
        "\n\n".join(items)[:4000]
    )


# =========================================================
# STATS
# =========================================================

@userbot.on_message(
    filters.outgoing & filters.command(
        "stats",
        prefixes="."
    )
)
async def stats_command(_, message):

    if not is_owner(message.from_user.id):
        return

    total = await chats_col.count_documents({})
    active = await chats_col.count_documents({
        "enabled": True
    })

    sent = await get_stat("success_sent")
    failed = await get_stat("failed_sent")
    floods = await get_stat("flood_waits")

    await message.edit(
        "📊 **Statistics**\n\n"
        f"Total Chats: `{total}`\n"
        f"Active Chats: `{active}`\n\n"
        f"✅ Sent: `{sent}`\n"
        f"❌ Failed: `{failed}`\n"
        f"⏳ FloodWait: `{floods}`"
    )


# =========================================================
# PERMANENT TELEGRAM ERRORS
# =========================================================

PERMANENT_ERRORS = {
    "ChannelPrivate",
    "ChatWriteForbidden",
    "UserBannedInChannel",
    "PeerIdInvalid",
    "ChatIdInvalid",
    "ChannelInvalid",
    "UsernameNotOccupied",
    "UsernameInvalid",
    "Forbidden",
}


# =========================================================
# SEND TO CHAT
# =========================================================

async def send_to_chat(chat):

    chat_id = chat["_id"]
    text = await get_config(
        "message",
        "Default Auto Message"
    )

    try:
        await userbot.send_message(
            int(chat_id),
            text
        )

        await chats_col.update_one(
            {"_id": chat_id},
            {
                "$set": {
                    "last_sent": now(),
                    "last_error": None,
                    "updated_at": now(),
                },
                "$setOnInsert": {
                    "fail_count": 0
                }
            }
        )

        await chats_col.update_one(
            {"_id": chat_id},
            {
                "$set": {
                    "fail_count": 0
                }
            }
        )

        await inc_stat(
            "success_sent"
        )

        return "success"

    except Exception as e:

        error_name = type(e).__name__

        await inc_stat(
            "failed_sent"
        )

        await chats_col.update_one(
            {"_id": chat_id},
            {
                "$inc": {
                    "fail_count": 1
                },
                "$set": {
                    "last_error": str(e)[:500],
                    "updated_at": now(),
                }
            }
        )

        if error_name in PERMANENT_ERRORS:
            await chats_col.update_one(
                {"_id": chat_id},
                {
                    "$set": {
                        "enabled": False,
                        "disabled_reason": error_name,
                        "disabled_at": now(),
                    }
                }
            )

            log.warning(
                "Disabled chat %s: %s",
                chat_id,
                error_name
            )

        raise


# =========================================================
# AUTO BROADCAST
# =========================================================

async def auto_broadcast_loop():

    while True:

        try:

            enabled = await get_config(
                "status",
                False
            )

            if not enabled:
                await asyncio.sleep(5)
                continue

            # Refresh ForceSub configuration
            await load_force_sub_config()

            # Owner account must be subscribed to own required channels
            if GLOBAL_FORCE_CHANNELS:

                me = await userbot.get_me()

                owner_sub = await userbot_check_subscription(
                    me.id
                )

                if owner_sub is False:
                    log.warning(
                        "Owner account is not subscribed to ForceSub channels."
                    )

                    await asyncio.sleep(30)
                    continue

            chats = []

            async for chat in chats_col.find({
                "enabled": True
            }).sort(
                "created_at",
                1
            ):
                chats.append(chat)

            if not chats:
                await asyncio.sleep(10)
                continue

            delay = int(
                await get_config(
                    "delay",
                    15
                )
            )

            cycle_delay = int(
                await get_config(
                    "cycle_delay",
                    30
                )
            )

            log.info(
                "Starting broadcast cycle: %s chats",
                len(chats)
            )

            flood_happened = False

            for chat in chats:

                # Recheck status
                enabled = await get_config(
                    "status",
                    False
                )

                if not enabled:
                    break

                try:

                    result = await send_to_chat(
                        chat
                    )

                    if result == "success":
                        log.info(
                            "Sent -> %s",
                            chat.get("title", chat["_id"])
                        )

                except Exception as e:

                    error_name = type(e).__name__

                    # FloodWait
                    if error_name == "FloodWait":

                        wait_time = int(
                            getattr(e, "value", 30)
                        )

                        await inc_stat(
                            "flood_waits"
                        )

                        log.warning(
                            "FloodWait: sleeping %ss",
                            wait_time
                        )

                        await asyncio.sleep(
                            wait_time
                        )

                        flood_happened = True

                        # Stop current cycle after FloodWait.
                        break

                    log.warning(
                        "Failed -> %s | %s",
                        chat.get("title", chat["_id"]),
                        e
                    )

                # Delay between chats
                await asyncio.sleep(
                    max(5, delay)
                )

            # Full cycle delay
            if flood_happened:
                await asyncio.sleep(
                    max(30, cycle_delay * 60)
                )
            else:
                await asyncio.sleep(
                    max(60, cycle_delay * 60)
                )

        except asyncio.CancelledError:
            raise

        except Exception as e:

            log.exception(
                "Broadcast loop crashed: %s",
                e
            )

            # Automatic recovery
            await asyncio.sleep(15)


# =========================================================
# SUPERVISOR
# =========================================================

async def broadcaster_supervisor():

    while True:

        try:
            await auto_broadcast_loop()

        except asyncio.CancelledError:
            raise

        except Exception as e:

            log.exception(
                "Broadcaster supervisor error: %s",
                e
            )

            await asyncio.sleep(15)


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"OK - AutoForward is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    log.info(
        "Health server running on port %s",
        PORT
    )

    server.serve_forever()


# =========================================================
# VALIDATE FORCE SUB CHANNELS
# =========================================================

async def validate_force_sub():

    if not GLOBAL_FORCE_CHANNELS:
        log.info(
            "ForceSub: disabled"
        )
        return

    log.info(
        "ForceSub channels: %s",
        GLOBAL_FORCE_CHANNELS
    )

    for channel in GLOBAL_FORCE_CHANNELS:

        # User account check
        try:
            chat = await userbot.get_chat(
                channel
            )

            log.info(
                "User account can access ForceSub: %s",
                chat.title or chat.id
            )

        except Exception as e:

            log.warning(
                "User account cannot access %s: %s",
                channel,
                e
            )

        # Bot check
        try:
            chat = await bot.get_chat(
                channel
            )

            log.info(
                "Bot can access ForceSub: %s",
                chat.title or chat.id
            )

        except Exception as e:

            log.warning(
                "Bot cannot access %s: %s",
                channel,
                e
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    global OWNER_ID

    # MongoDB
    await init_db()

    # Userbot
    await userbot.start()

    me = await userbot.get_me()

    if OWNER_ID == 0:
        OWNER_ID = me.id

    log.info(
        "User account: %s | %s",
        me.id,
        me.first_name
    )

    log.info(
        "Owner ID: %s",
        OWNER_ID
    )

    # Load ForceSub
    await load_force_sub_config()

    await validate_force_sub()

    # Start health server
    Thread(
        target=start_health_server,
        daemon=True
    ).start()

    # Start broadcaster
    broadcaster_task = asyncio.create_task(
        broadcaster_supervisor()
    )

    log.info(
        "🚀 Userbot + Bot started"
    )

    try:

        # Bot polling
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        await dp.start_polling(
            bot
        )

    finally:

        broadcaster_task.cancel()

        try:
            await broadcaster_task
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

        mongo.close()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        pass
