import asyncio
import os
import logging
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

# ================= LOGGING & ENV =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("autoforward")

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "autoforward")
FORCE_SUB_ENV = os.environ.get("FORCE_SUB", "")
PORT = int(os.environ.get("PORT", "10000"))

# ================= MONGODB SETUP =================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[MONGO_DB]
config_col = db["config"]
chats_col = db["chats"]

async def mongo_init():
    await config_col.update_one({"_id": "status"}, {"$setOnInsert": {"value": "OFF"}}, upsert=True)
    await config_col.update_one({"_id": "delay"}, {"$setOnInsert": {"value": 8}}, upsert=True)
    await config_col.update_one({"_id": "msg"}, {"$setOnInsert": {"value": "Default Message"}}, upsert=True)
    await config_col.update_one({"_id": "force_sub"}, {"$setOnInsert": {"value": []}}, upsert=True)

async def get_config(key, default=None):
    doc = await config_col.find_one({"_id": key})
    return doc.get("value", default) if doc else default

async def set_config(key, value):
    await config_col.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)

# ================= HELPERS =================
def parse_id(val):
    s = str(val).strip()
    if s.startswith("100") and len(s) >= 12:
        s = "-" + s
    return int(s) if s.lstrip("-").isdigit() else s

userbot = Client("userbot_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= HEALTH SERVER =================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

Thread(target=lambda: ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever(), daemon=True).start()

# ================= FORCE-SUB ENGINE =================
async def is_subscribed(client_inst, chat_id, user_id):
    try:
        c_id = parse_id(chat_id)
        if isinstance(client_inst, Bot):
            m = await client_inst.get_chat_member(c_id, user_id)
        else:
            m = await client_inst.get_chat_member(c_id, user_id)
        return str(m.status).lower() in ["owner", "administrator", "creator", "member"]
    except Exception as e:
        log.error(f"Check sub error [{chat_id}]: {e}")
        return True

async def get_bot_keyboard():
    channels = await get_config("force_sub", [])
    btns = []
    for i, ch in enumerate(channels, 1):
        link = ch.get("link", "https://t.me") if isinstance(ch, dict) else "https://t.me"
        title = ch.get("title", f"Channel {i}") if isinstance(ch, dict) else f"Channel {i}"
        btns.append([AioInlineKeyboardButton(text=f"📢 Join {title}", url=link)])
    btns.append([AioInlineKeyboardButton(text="✅ Check Subscription", callback_data="check_sub")])
    return AioInlineKeyboardMarkup(inline_keyboard=btns)

# 1. BOT PM FORCE-SUB
@dp.message(F.chat.type == ChatType.PRIVATE)
async def bot_pm_guard(msg: AiogramMessage):
    channels = await get_config("force_sub", [])
    for ch in channels:
        ch_id = ch.get("id") if isinstance(ch, dict) else ch
        if not await is_subscribed(bot, ch_id, msg.from_user.id):
            await msg.answer("🔒 **Pehle niche diye gaye channels join karo:**", reply_markup=await get_bot_keyboard())
            return
    await msg.answer("🚀 Bot Active Hai!")

@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(cb: CallbackQuery):
    channels = await get_config("force_sub", [])
    for ch in channels:
        ch_id = ch.get("id") if isinstance(ch, dict) else ch
        if not await is_subscribed(bot, ch_id, cb.from_user.id):
            await cb.answer("❌ Pehle sabhi channels join karo!", show_alert=True)
            return
    await cb.message.edit_text("✅ **Subscription Verified!**")

# 2. SESSION USERBOT PM FORCE-SUB
@userbot.on_message(filters.private & ~filters.me & ~filters.bot)
async def userbot_pm_guard(cli, msg: Message):
    channels = await get_config("force_sub", [])
    if not channels:
        return
    
    for ch in channels:
        ch_id = ch.get("id") if isinstance(ch, dict) else ch
        if not await is_subscribed(userbot, ch_id, msg.from_user.id):
            txt = "🔒 **Mujhe PM me message karne ke liye pehle channels join karo:**\n\n"
            for i, c in enumerate(channels, 1):
                link = c.get("link", "https://t.me") if isinstance(c, dict) else "https://t.me"
                title = c.get("title", f"Channel {i}") if isinstance(c, dict) else f"Channel {i}"
                txt += f"{i}. 📢 [{title}]({link})\n"
            await msg.reply_text(txt, disable_web_page_preview=True)
            return

# 3. SESSION DOT COMMANDS
@userbot.on_message(filters.outgoing)
async def session_commands(cli, msg: Message):
    if not msg.text or not msg.text.startswith("."):
        return

    parts = msg.text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == ".help":
        await msg.edit_text(
            "🤖 **Session Commands Active:**\n\n"
            "• `.forcesub -100xxx -100yyy` : Set Channels\n"
            "• `.forcesuboff` : Clear ForceSub\n"
            "• `.addchat` : Add current chat to broadcast\n"
            "• `.delchat` : Remove current chat\n"
            "• `.listchats` : View target chats\n"
            "• `.setmsg text` : Set broadcast message\n"
            "• `.automsg on/off` : Toggle Auto Broadcast"
        )

    elif cmd == ".forcesub":
        if not args:
            await msg.edit_text("❌ Example: `.forcesub -10012345678 -10098765432`")
            return
        await msg.edit_text("⏳ Saving channels...")
        saved = []
        clean_args = " ".join(args).replace(",", " ").split()
        for item in clean_args[:3]:
            try:
                chat = await userbot.get_chat(parse_id(item))
                link = chat.username and f"https://t.me/{chat.username}" or chat.invite_link or "https://t.me"
                saved.append({"id": chat.id, "link": link, "title": chat.title or str(chat.id)})
            except Exception as e:
                log.error(f"Forcesub add error: {e}")
        
        await set_config("force_sub", saved)
        await msg.edit_text(f"✅ **ForceSub Set:** Saved {len(saved)} channel(s).")

    elif cmd == ".forcesuboff":
        await set_config("force_sub", [])
        await msg.edit_text("🔓 **Force-Sub Disabled.**")

    elif cmd == ".addchat":
        target = parse_id(args[0]) if args else msg.chat.id
        chat = await userbot.get_chat(target)
        await chats_col.update_one({"_id": str(chat.id)}, {"$set": {"chat_id": str(chat.id), "title": chat.title or "Chat"}}, upsert=True)
        await msg.edit_text(f"✅ **Chat Added:** `{chat.title or chat.id}`")

    elif cmd == ".delchat":
        target = parse_id(args[0]) if args else msg.chat.id
        await chats_col.delete_one({"_id": str(target)})
        await msg.edit_text("🗑 **Chat Removed.**")

    elif cmd == ".listchats":
        chats = await chats_col.find().to_list(1000)
        txt = "📋 **Target Chats:**\n\n" + "\n".join(f"{i}. {c.get('title')} (`{c.get('chat_id')}`)" for i, c in enumerate(chats, 1))
        await msg.edit_text(txt if chats else "ℹ️ No chats added.")

    elif cmd == ".setmsg":
        if not args:
            return await msg.edit_text("❌ Text missing.")
        new_text = msg.text.split(maxsplit=1)[1]
        await set_config("msg", new_text)
        await msg.edit_text(f"✅ **Message Saved:**\n\n{new_text}")

    elif cmd == ".automsg":
        if not args or args[0].lower() not in ["on", "off"]:
            return await msg.edit_text("❌ Usage: `.automsg on` or `.automsg off`")
        st = args[0].upper()
        await set_config("status", st)
        await msg.edit_text(f"🤖 **Auto Broadcast:** `{st}`")

# ================= BROADCAST WORKER =================
async def broadcast_worker():
    while True:
        try:
            await asyncio.sleep(2)
            if await get_config("status", "OFF") != "ON":
                continue
            msg_text = await get_config("msg", "")
            delay = int(await get_config("delay", 8))
            chats = await chats_col.find().to_list(1000)
            if msg_text and chats:
                for c in chats:
                    if await get_config("status", "OFF") != "ON": break
                    try:
                        await userbot.send_message(parse_id(c.get("chat_id")), msg_text)
                    except Exception: pass
                    await asyncio.sleep(delay)
        except Exception:
            await asyncio.sleep(5)

# ================= MAIN =================
async def main():
    await mongo_init()
    await userbot.start()
    
    # Auto load FORCE_SUB from Render ENV on restart
    if FORCE_SUB_ENV:
        raws = [x.strip() for x in FORCE_SUB_ENV.replace(" ", "").split(",") if x.strip()]
        saved = []
        for r in raws[:3]:
            try:
                ch = await userbot.get_chat(parse_id(r))
                lk = ch.username and f"https://t.me/{ch.username}" or ch.invite_link or "https://t.me"
                saved.append({"id": ch.id, "link": lk, "title": ch.title or str(ch.id)})
            except Exception: pass
        if saved:
            await set_config("force_sub", saved)

    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(broadcast_worker())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
