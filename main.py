import asyncio

# Python 3.10+ / 3.14 asyncio event loop fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.types import Message

# Credentials
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

app = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


# ================= DUMMY WEB SERVER (FOR RENDER HEALTH CHECK) =================
class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Userbot is running perfectly!")

    def log_message(self, format, *args):
        return


def start_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


threading.Thread(target=start_dummy_server, daemon=True).start()

# ================= DATABASE SETUP =================
conn = sqlite3.connect("userbot_data.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute(
    "CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, title TEXT, interval_sec INTEGER DEFAULT 60, last_sent REAL DEFAULT 0)"
)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)"
)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)"
)

# Migration support for existing DB
try:
    cursor.execute(
        "ALTER TABLE chats ADD COLUMN interval_sec INTEGER DEFAULT 60"
    )
except Exception:
    pass
try:
    cursor.execute("ALTER TABLE chats ADD COLUMN last_sent REAL DEFAULT 0")
except Exception:
    pass

conn.commit()


def get_config(key, default=""):
    cursor.execute("SELECT value FROM config WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else default


def set_config(key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()


def get_stat(key):
    cursor.execute("SELECT value FROM stats WHERE key=?", (key,))
    res = cursor.fetchone()
    return res[0] if res else 0


def inc_stat(key, amount=1):
    current = get_stat(key)
    cursor.execute(
        "INSERT OR REPLACE INTO stats (key, value) VALUES (?, ?)",
        (key, current + amount),
    )
    conn.commit()


def get_all_chats():
    cursor.execute("SELECT chat_id, title, interval_sec, last_sent FROM chats")
    return cursor.fetchall()


def update_last_sent(chat_id, timestamp):
    cursor.execute(
        "UPDATE chats SET last_sent=? WHERE chat_id=?", (timestamp, chat_id)
    )
    conn.commit()


# Defaults
if not get_config("status"):
    set_config("status", "OFF")
if not get_config("msg"):
    set_config("msg", "Default Auto Message")
if not get_config("force_sub"):
    set_config("force_sub", "")


# ================= FORCE SUB CHECK =================
async def is_force_sub_active():
    channel = get_config("force_sub")
    if not channel:
        return True
    try:
        if "t.me/" in channel:
            channel = channel.split("t.me/")[1].replace("/", "")
        channel = channel.replace("@", "").strip()

        me = await app.get_me()
        member = await app.get_chat_member(channel, me.id)

        if member.status in [
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        ]:
            return True
    except Exception as e:
        print(f"[ForceSub Error] {e}")
        return False
    return False


# ================= INDIVIDUAL CHAT TIMER BROADCASTER =================
async def auto_broadcast_loop():
    while True:
        await asyncio.sleep(2)
        if get_config("status") != "ON":
            continue

        if not await is_force_sub_active():
            set_config("status", "OFF")
            await app.send_message(
                "me",
                f"❌ **Auto Msg Stopped!** Force Sub Channel (@{get_config('force_sub')}) leave kar diya gaya hai.",
            )
            continue

        chats = get_all_chats()
        msg_text = get_config("msg")

        if not chats or not msg_text:
            continue

        current_time = time.time()

        for chat_id, title, interval_sec, last_sent in chats:
            if get_config("status") != "ON":
                break

            # Check if this chat's specific interval time has passed
            if current_time - last_sent >= interval_sec:
                try:
                    target = (
                        int(chat_id)
                        if chat_id.lstrip("-").isdigit()
                        else chat_id
                    )
                    await app.send_message(target, msg_text)

                    update_last_sent(chat_id, time.time())
                    inc_stat("total_sent")
                    inc_stat("success_sent")

                    # Flood preventer gap between sending to multiple chats in same tick
                    await asyncio.sleep(2)

                except FloodWait as e:
                    print(f"FloodWait hit: Sleeping for {e.value}s")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    inc_stat("failed_sent")
                    print(f"[Send Error] {chat_id}: {e}")


# ================= COMMAND HANDLERS =================


@app.on_message(filters.me & filters.command("addchat", prefixes="."))
async def add_chat_handler(client, message: Message):
    """
    Usage:
    .addchat (current chat me 60s delay)
    .addchat 30 (current chat me 30s delay)
    .addchat @groupname 45 (target group me 45s delay)
    """
    args = message.text.split()
    interval = 60  # Default 60 seconds

    if len(args) == 1:
        target = message.chat.id
    elif len(args) == 2:
        if args[1].isdigit():
            target = message.chat.id
            interval = int(args[1])
        else:
            target = args[1]
    else:
        target = args[1]
        if args[2].isdigit():
            interval = int(args[2])

    if isinstance(target, str):
        if "t.me/" in target:
            target = target.split("t.me/")[1].replace("/", "").replace("+", "")
        if target.lstrip("-").isdigit():
            target = int(target)
        elif not target.startswith("@"):
            target = f"@{target}"

    try:
        chat_obj = await client.get_chat(target)
        chat_id = str(chat_obj.id)
        title = chat_obj.title or chat_obj.first_name or "Unknown"

        cursor.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title, interval_sec, last_sent) VALUES (?, ?, ?, ?)",
            (chat_id, title, interval, 0),
        )
        conn.commit()

        await message.edit_text(
            f"✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`\n"
            f"⏱ **Interval:** Har `{interval}` seconds"
        )
    except Exception as e:
        await message.edit_text(f"❌ **Error adding chat:** `{e}`")


@app.on_message(filters.me & filters.command("setchatdelay", prefixes="."))
async def set_chat_delay_handler(client, message: Message):
    """Usage: .setchatdelay -100123456789 30"""
    args = message.text.split(maxsplit=2)
    if len(args) < 3 or not args[2].isdigit():
        return await message.edit_text(
            "❌ **Usage:** `.setchatdelay <chat_id> <seconds>`"
        )

    chat_id = args[1].strip()
    interval = int(args[2])

    cursor.execute(
        "UPDATE chats SET interval_sec=? WHERE chat_id=?", (interval, chat_id)
    )
    conn.commit()
    await message.edit_text(
        f"⏱ **Chat `{chat_id}` ka delay `{interval}` seconds set kar diya gaya hai!**"
    )


@app.on_message(filters.me & filters.command("delchat", prefixes="."))
async def del_chat_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    chat_id = str(message.chat.id) if len(args) < 2 else args[1].strip()
    cursor.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    await message.edit_text(f"🗑 **Chat `{chat_id}` Removed!**")


@app.on_message(filters.me & filters.command("listchats", prefixes="."))
async def list_chats_handler(client, message: Message):
    chats = get_all_chats()
    if not chats:
        return await message.edit_text("ℹ️ No chats added.")

    text = "📋 **Target Chats & Custom Timing:**\n\n"
    for idx, (c_id, title, interval, _) in enumerate(chats, 1):
        text += f"{idx}. **{title}** | `{c_id}`\n   ⏱ **Interval:** `{interval}s`\n"

    await message.edit_text(text)


@app.on_message(filters.me & filters.command("setmsg", prefixes="."))
async def set_msg_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ `.setmsg <text>`")
    set_config("msg", args[1])
    await message.edit_text(f"✅ **Message Updated!**")


@app.on_message(filters.me & filters.command("forcesub", prefixes="."))
async def set_forcesub_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ `.forcesub <channel_username>`")
    ch = args[1].replace("@", "").replace("t.me/", "").strip()
    set_config("force_sub", ch)
    await message.edit_text(f"🔒 **Force Sub set to:** `@{ch}`")


@app.on_message(filters.me & filters.command("automsg", prefixes="."))
async def toggle_automsg(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        return await message.edit_text("❌ `.automsg on` ya `.automsg off`")
    state = args[1].upper()

    if state == "ON" and not await is_force_sub_active():
        return await message.edit_text(
            f"❌ Join @{get_config('force_sub')} first!"
        )

    set_config("status", state)
    await message.edit_text(f"🤖 **Auto Messaging is `{state}`!**")


@app.on_message(filters.me & filters.command("stats", prefixes="."))
async def stats_handler(client, message: Message):
    chats = get_all_chats()
    stats_text = (
        "📊 **Auto-Bot Per-Chat Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status:** `{get_config('status')}`\n"
        f"🔒 **Force Sub:** `@{get_config('force_sub') or 'None'}`\n"
        f"🎯 **Target Chats:** `{len(chats)} Groups`\n\n"
        "📈 **Sending History:**\n"
        f"├ 🚀 **Total Attempts:** `{get_stat('total_sent')}`\n"
        f"├ ✅ **Successfully Sent:** `{get_stat('success_sent')}`\n"
        f"└ ❌ **Failed:** `{get_stat('failed_sent')}`\n\n"
        f"📝 **Current Active Message:**\n`{get_config('msg')}`"
    )
    await message.edit_text(stats_text)


# ================= RUNNER =================
async def main():
    await app.start()
    print("Userbot started with Per-Chat Timers!")
    asyncio.create_task(auto_broadcast_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
