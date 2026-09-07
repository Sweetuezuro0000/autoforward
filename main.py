import asyncio
import os
import random
import logging
from datetime import datetime, timezone
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from motor.motor_asyncio import AsyncIOMotorClient

from pyrogram import Client, filters
from pyrogram.types import Message

from aiogram import Bot, Dispatcher, F
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
# PRIVATE CHANNEL & FORCE-SUB HELPERS
# =========================================================

async def check_user_membership(client_instance, channel_info, user_id):
    chat_id = channel_info.get("id") if isinstance(channel_info, dict) else channel_info
    try:
        if isinstance(client_instance, Bot):
            member = await client_instance.get_chat_member(chat_id=chat_id, user_id=user_id)
        else:
            member = await client_instance.get_chat_member(chat_id, user_id)

        status_str = str(getattr(member, "status", "")).lower()
        if any(s in status_str for s in ["owner", "administrator", "creator", "member"]):
            return True
        if "restricted" in status_str:
            return bool(getattr(member, "is_member", False))
        return False
    except Exception as e:
        log.warning(f"ForceSub check error for {chat_id} (User: {user_id}): {e}")
        return False


async def bot_force_sub_keyboard():
    channels = await get_config("force_sub", [])
    buttons = []
    for index, ch in enumerate(channels, 1):
        if isinstance(ch, dict):
            link = ch.get("link", "https://t.me")
            title = ch.get("title", f"Channel {index}")
        else:
            link = f"https://t.me/{str(ch).lstrip('@')}" if str(ch).startswith("@") else "https://t.me"
            title = f"Channel {index}"

        buttons.append([AioInlineKeyboardButton(text=f"📢 Join {title}", url=link)])

    buttons.append([AioInlineKeyboardButton(text="✅ Check Subscription", callback_data="check_sub")])
    return AioInlineKeyboardMarkup(inline_keyboard=buttons)


async def check_bot_force_sub(user_id):
    channels = await get_config("force_sub", [])
    if not channels:
        return True
    for ch in channels:
        joined = await check_user_membership(bot, ch, user_id)
        if not joined:
            return False
    return True


# =========================================================
# AIOGRAM BOT HANDLERS (GROUPS & BOT PM)
# =========================================================

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group_message_guard(message: AiogramMessage):
    if not message.from_user or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    status = await check_bot_force_sub(user_id)

    if status is False:
        try:
            await message.reply(
                f"⚠️ **{message.from_user.first_name}**, aapne humare required channels join nahi kiye hain!\n\n"
                f"Group me chat karne ke liye pehle niche diye gaye channels join karein:",
                reply_markup=await bot_force_sub_keyboard()
            )
        except Exception as e:
            log.warning(f"Failed to send group warning: {e}")


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

    await message.answer("🚀 Bot Active Hai!")


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    status = await check_bot_force_sub(callback.from_user.id)
    if status is True:
        await callback.message.edit_text("✅ **Subscription Verified!** Ab aap chat kar sakte ho.")
    else:
        await callback.answer("❌ Pehle sabhi channels join karo!", show_alert=True)


# =========================================================
# USERBOT PM GUARD (SESSION PM FORCE-SUB)
# =========================================================

@userbot.on_message(filters.private & ~filters.me & ~filters.bot)
async def userbot_pm_forcesub(client, message: Message):
    try:
        channels = await get_config("force_sub", [])
        if not channels:
            return

        user_id = message.from_user.id
        not_joined = False

        for ch in channels:
            res = await check_user_membership(userbot, ch, user_id)
            if not res:
                not_joined = True
                break

        if not_joined:
            links_text = ""
            for index, ch in enumerate(channels, 1):
                if isinstance(ch, dict):
                    link = ch.get("link", "https://t.me")
                    title = ch.get("title", f"Channel {index}")
                else:
                    link = f"https://t.me/{str(ch).lstrip('@')}" if str(ch).startswith("@") else "https://t.me"
                    title = f"Channel {index}"
                links_text += f"{index}. 📢 [{title}]({link})\n"

            await message.reply_text(
                f"🔒 **Force Subscription Required**\n\n"
                f"Mujhe PM me message karne ke liye pehle niche diye gaye channels join karo:\n\n{links_text}",
                disable_web_page_preview=True
            )
    except Exception as e:
        log.error(f"Error in userbot PM forcesub: {e}")


# =========================================================
# USERBOT COMMAND DISPATCHER (RELIABLE SESSION CONTROL)
# =========================================================

@userbot.on_message(filters.outgoing)
async def userbot_cmd_dispatcher(client, message: Message):
    if not message.text:
        return

    text = message.text.strip()
    if not text.startswith("."):
        return

    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    log.info(f"Session Command Detected: {cmd} | Args: {args}")

    # 1. HELP COMMAND
    if cmd == ".help":
        await message.edit_text(
            "🤖 **Session Controls Active!**\n\n"
            "• `.forcesub -100xxx -100yyy` : Set ForceSub Channels\n"
            "• `.forcesuboff` : Disable ForceSub\n"
            "• `.addchat` : Add current chat to broadcast\n"
            "• `.delchat` : Remove chat from broadcast\n"
            "• `.listchats` : View broadcast chats list\n"
            "• `.setmsg text` : Set broadcast message\n"
            "• `.automsg on/off` : Enable/Disable Auto Broadcast"
        )

    # 2. FORCESUB COMMAND
    elif cmd == ".forcesub":
        if not args:
            await message.edit_text("❌ **Usage:** `.forcesub -10012345678 -10098765432` or `.forcesub @ch1 @ch2`")
            return

        await message.edit_text("⏳ Processing channels & links...")
        saved_channels = []

        for item in args[:3]:
            try:
                if item.startswith("http"):
                    chat = await userbot.get_chat(item)
                    link = item
                else:
                    target = int(item) if item.lstrip("-").isdigit() else item
                    chat = await userbot.get_chat(target)
                    if chat.username:
                        link = f"https://t.me/{chat.username}"
                    elif chat.invite_link:
                        link = chat.invite_link
                    else:
                        try:
                            link = await userbot.export_chat_invite_link(chat.id)
                        except Exception:
                            link = "https://t.me"

                saved_channels.append({
                    "id": chat.id,
                    "link": link,
                    "title": chat.title or str(chat.id)
                })
            except Exception as e:
                log.error(f"Failed to add channel {item}: {e}")

        if not saved_channels:
            await message.edit_text("❌ Channels process nahi ho paye. Userbot ko pehle channels me add karo!")
            return

        await set_config("force_sub", saved_channels)
        txt = "🔒 **Force-Sub Channels Set:**\n\n"
        for i, ch in enumerate(saved_channels, 1):
            txt += f"{i}. [{ch['title']}]({ch['link']}) (`{ch['id']}`)\n"
        await message.edit_text(txt, disable_web_page_preview=True)

    # 3. FORCESUBOFF COMMAND
    elif cmd == ".forcesuboff":
        await set_config("force_sub", [])
        await message.edit_text("🔓 **Force-Sub disabled.**")

    # 4. ADDCHAT COMMAND
    elif cmd == ".addchat":
        try:
            target = message.chat.id if not args else args[0]
            target = int(target) if str(target).lstrip("-").isdigit() else target
            chat = await userbot.get_chat(target)
            await chats_col.update_one(
                {"_id": str(chat.id)},
                {"$set": {"chat_id": str(chat.id), "title": chat.title or "Unknown"}},
                upsert=True
            )
            await message.edit_text(f"✅ **Chat Added:** `{chat.title or chat.id}`")
        except Exception as e:
            await message.edit_text(f"❌ **Error:** `{e}`")

    # 5. DELCHAT COMMAND
    elif cmd == ".delchat":
        try:
            target = message.chat.id if not args else args[0]
            target = int(target) if str(target).lstrip("-").isdigit() else target
            chat = await userbot.get_chat(target)
            res = await chats_col.delete_one({"_id": str(chat.id)})
            await message.edit_text("🗑 **Removed from database.**" if res.deleted_count else "ℹ️ Chat not found in database.")
        except Exception as e:
            await message.edit_text(f"❌ `{e}`")

    # 6. LISTCHATS COMMAND
    elif cmd == ".listchats":
        chats = await chats_col.find().to_list(length=1000)
        if not chats:
            await message.edit_text("ℹ️ **No target chats added.**")
            return
        text = "📋 **Target Chats List:**\n\n" + "\n".join(f"{i}. **{c.get('title')}** (`{c.get('chat_id')}`)" for i, c in enumerate(chats, 1))
        await message.edit_text(text)

    # 7. SETMSG COMMAND
    elif cmd == ".setmsg":
        if not args:
            await message.edit_text("❌ Example: `.setmsg Your message text here`")
            return
        new_msg = text.split(maxsplit=1)[1]
        await set_config("msg", new_msg)
        await message.edit_text(f"✅ **Auto Message set to:**\n\n{new_msg}")

    # 8. AUTOMSG COMMAND
    elif cmd == ".automsg":
        if not args or args[0].lower() not in ("on", "off"):
            await message.edit_text("❌ Example: `.automsg on` or `.automsg off`")
            return
        state = args[0].upper()
        await set_config("status", state)
        await message.edit_text(f"🤖 **Auto Msg State:** `{state}`")


# =========================================================
# BROADCAST WORKER
# =========================================================

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
                    try:
                        c_id = int(chat.get("chat_id")) if chat.get("chat_id").lstrip("-").isdigit() else chat.get("chat_id")
                        await userbot.send_message(c_id, msg_text)
                    except Exception as e:
                        log.warning(f"Broadcast send failed for {chat.get('chat_id')}: {e}")
                    await asyncio.sleep(delay)

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
