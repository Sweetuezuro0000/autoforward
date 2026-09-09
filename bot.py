import os
import sqlite3
import time

from pyrogram import filters
from pyrogram.types import Message

from aiogram import Bot as AioBot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message as AioMessage

from main import app
from force import set_force_sub, get_force_sub, force_sub_ok, normalize_target

# ================= DATABASE =================
conn = sqlite3.connect("userbot_data.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    title TEXT,
    interval_sec INTEGER DEFAULT 60,
    last_sent REAL DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
)
""")
conn.commit()

def get_config(key, default=""):
    cursor.execute("SELECT value FROM config WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def set_config(key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",
        (key, str(value))
    )
    conn.commit()

def get_stat(key):
    cursor.execute("SELECT value FROM stats WHERE key=?", (key,))
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def inc_stat(key, amount=1):
    current = get_stat(key)
    cursor.execute(
        "INSERT OR REPLACE INTO stats(key,value) VALUES(?,?)",
        (key, current + amount)
    )
    conn.commit()

def get_chats():
    cursor.execute(
        "SELECT chat_id,title,interval_sec,last_sent FROM chats ORDER BY title"
    )
    return cursor.fetchall()

def init_db():
    if not get_config("status"):
        set_config("status", "OFF")
    if not get_config("msg"):
        set_config("msg", "Default Auto Message")

# ================= SESSION COMMANDS =================
# Important: using filters.outgoing because this matches the old working setup.

@app.on_message(filters.outgoing & filters.command("help", prefixes="."))
async def help_cmd(client, message: Message):
    await message.edit_text(
        "🤖 **Commands**\n\n"
        "`.addchat`\n"
        "`.addchat 30`\n"
        "`.addchat @group 60`\n"
        "`.setchatdelay <chat_id> <seconds>`\n"
        "`.delchat <chat_id>`\n"
        "`.listchats`\n"
        "`.setmsg <text>`\n"
        "`.forcesub <channel>`\n"
        "`.forcesuboff`\n"
        "`.automsg on/off`\n"
        "`.stats`"
    )

@app.on_message(filters.outgoing & filters.command("addchat", prefixes="."))
async def addchat_cmd(client, message: Message):
    args = (message.text or "").split()
    interval = 60

    if len(args) == 1:
        target = message.chat.id
    elif len(args) == 2:
        if args[1].isdigit():
            target = message.chat.id
            interval = int(args[1])
        else:
            target = normalize_target(args[1])
    else:
        target = normalize_target(args[1])
        if args[2].isdigit():
            interval = int(args[2])

    try:
        chat = await client.get_chat(target)
        chat_id = str(chat.id)
        title = chat.title or chat.first_name or "Unknown"

        cursor.execute(
            "INSERT OR REPLACE INTO chats(chat_id,title,interval_sec,last_sent)"
            " VALUES(?,?,?,?)",
            (chat_id, title, interval, 0)
        )
        conn.commit()

        await message.edit_text(
            f"✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`\n"
            f"⏱ **Interval:** `{interval}` seconds"
        )
    except Exception as e:
        await message.edit_text(f"❌ **Error:** `{e}`")

@app.on_message(filters.outgoing & filters.command("delchat", prefixes="."))
async def delchat_cmd(client, message: Message):
    args = (message.text or "").split(maxsplit=1)
    chat_id = str(message.chat.id) if len(args) < 2 else args[1].strip()

    cursor.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    await message.edit_text(f"🗑 **Chat `{chat_id}` removed.**")

@app.on_message(filters.outgoing & filters.command("listchats", prefixes="."))
async def listchats_cmd(client, message: Message):
    rows = get_chats()
    if not rows:
        return await message.edit_text("ℹ️ **No chats added.**")

    text = "📋 **Target Chats**\n\n"
    for i, (chat_id, title, interval, _) in enumerate(rows, 1):
        text += f"{i}. **{title}**\n🆔 `{chat_id}`\n⏱ `{interval}s`\n\n"

    await message.edit_text(text)

@app.on_message(filters.outgoing & filters.command("setchatdelay", prefixes="."))
async def setchatdelay_cmd(client, message: Message):
    args = (message.text or "").split(maxsplit=2)

    if len(args) < 3 or not args[2].isdigit():
        return await message.edit_text(
            "❌ `.setchatdelay <chat_id> <seconds>`"
        )

    chat_id = args[1]
    seconds = int(args[2])

    cursor.execute(
        "UPDATE chats SET interval_sec=? WHERE chat_id=?",
        (seconds, chat_id)
    )
    conn.commit()

    if cursor.rowcount == 0:
        return await message.edit_text("❌ Chat not found.")

    await message.edit_text(
        f"✅ Delay for `{chat_id}` set to `{seconds}` seconds."
    )

@app.on_message(filters.outgoing & filters.command("setmsg", prefixes="."))
async def setmsg_cmd(client, message: Message):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ `.setmsg <text>`")

    set_config("msg", args[1])
    await message.edit_text("✅ **Message updated.**")

@app.on_message(filters.outgoing & filters.command("forcesub", prefixes="."))
async def forcesub_cmd(client, message: Message):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text(
            "❌ `.forcesub <channel_username/link/id>`"
        )

    raw = args[1].strip()
    target = normalize_target(raw)

    try:
        chat = await client.get_chat(target)
        value = str(chat.username or chat.id)
        set_force_sub(value)
        set_config("force_sub", value)

        shown = f"@{chat.username}" if chat.username else str(chat.id)
        await message.edit_text(f"🔒 **ForceSub set:** `{shown}`")
    except Exception as e:
        await message.edit_text(f"❌ **ForceSub Error:** `{e}`")

@app.on_message(filters.outgoing & filters.command("forcesuboff", prefixes="."))
async def forcesuboff_cmd(client, message: Message):
    set_force_sub("")
    set_config("force_sub", "")
    await message.edit_text("🔓 **ForceSub disabled.**")

@app.on_message(filters.outgoing & filters.command("automsg", prefixes="."))
async def automsg_cmd(client, message: Message):
    args = (message.text or "").split(maxsplit=1)

    if len(args) < 2 or args[1].lower() not in {"on", "off"}:
        return await message.edit_text("❌ `.automsg on` / `.automsg off`")

    state = args[1].upper()

    if state == "ON" and not await force_sub_ok():
        return await message.edit_text(
            f"❌ **ForceSub check failed:** `@{get_force_sub() or 'None'}`"
        )

    set_config("status", state)
    await message.edit_text(f"🤖 **Auto Messaging `{state}`**")

@app.on_message(filters.outgoing & filters.command("stats", prefixes="."))
async def stats_cmd(client, message: Message):
    rows = get_chats()

    await message.edit_text(
        "📊 **Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Status: `{get_config('status','OFF')}`\n"
        f"🔒 ForceSub: `@{get_force_sub() or 'None'}`\n"
        f"🎯 Chats: `{len(rows)}`\n"
        f"🚀 Attempts: `{get_stat('total_sent')}`\n"
        f"✅ Success: `{get_stat('success_sent')}`\n"
        f"❌ Failed: `{get_stat('failed_sent')}`\n\n"
        f"📝 Message:\n`{get_config('msg','')}`"
    )

# ================= AUTO BROADCAST =================
async def broadcast_loop():
    import asyncio
    while True:
        try:
            await asyncio.sleep(2)

            if get_config("status", "OFF") != "ON":
                continue

            if not await force_sub_ok():
                set_config("status", "OFF")
                try:
                    await app.send_message(
                        "me",
                        f"❌ **Auto Msg stopped.** ForceSub `@{get_force_sub()}` active nahi hai."
                    )
                except Exception:
                    pass
                continue

            rows = get_chats()
            msg_text = get_config("msg", "")
            now = time.time()

            for chat_id, title, interval, last_sent in rows:
                if get_config("status", "OFF") != "ON":
                    break

                if now - float(last_sent or 0) < int(interval):
                    continue

                try:
                    target = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
                    await app.send_message(target, msg_text)

                    cursor.execute(
                        "UPDATE chats SET last_sent=? WHERE chat_id=?",
                        (time.time(), chat_id)
                    )
                    conn.commit()

                    inc_stat("total_sent")
                    inc_stat("success_sent")
                    await asyncio.sleep(2)

                except Exception as e:
                    inc_stat("failed_sent")
                    print(f"[Send Error] {chat_id}: {e}")

        except Exception as e:
            print(f"[Broadcast Error] {e}")
            await asyncio.sleep(5)

# ================= TELEGRAM BOT =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
tg_bot = AioBot(BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_handler(message: AioMessage):
    await message.answer("🚀 Bot Active Hai!")

@dp.message(Command("help"))
async def bot_help_handler(message: AioMessage):
    await message.answer(
        "🤖 **Bot Help**\n\n"
        "/start - Start bot\n"
        "/help - Help\n"
        "/status - Status"
    )

@dp.message(Command("status"))
async def bot_status_handler(message: AioMessage):
    await message.answer(
        f"⚡ Status: `{get_config('status','OFF')}`\n"
        f"🔒 ForceSub: `@{get_force_sub() or 'None'}`\n"
        f"🎯 Chats: `{len(get_chats())}`"
    )
