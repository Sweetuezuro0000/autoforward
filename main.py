import asyncio

# Python 3.10+ / 3.14 asyncio event loop fix (Pyrogram import crash prevention)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.types import Message

# Environment variables se credentials load honge
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
        return  # Render logs clean rakhne ke liye


def start_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# Background Thread me server start karein
threading.Thread(target=start_dummy_server, daemon=True).start()

# ================= DATABASE SETUP =================
conn = sqlite3.connect("userbot_data.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute(
    "CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY, title TEXT)"
)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)"
)
cursor.execute(
    "CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)"
)
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
    cursor.execute("SELECT chat_id, title FROM chats")
    return cursor.fetchall()


# Default Config Init
if not get_config("status"):
    set_config("status", "OFF")
if not get_config("delay"):
    set_config("delay", "30")
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
        # Link ya @ clean karna
        if "t.me/" in channel:
            channel = channel.split("t.me/")[1].replace("/", "")
        channel = channel.replace("@", "").strip()

        me = await app.get_me()
        member = await app.get_chat_member(channel, me.id)

        # Pyrogram v2 Enum Status Check
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


# ================= BACKGROUND AUTO BROADCASTER =================
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
        delay = int(get_config("delay", "30"))

        if not chats or not msg_text:
            continue

        for chat_id, title in chats:
            if get_config("status") != "ON":
                break

            try:
                target = (
                    int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
                )
                await app.send_message(target, msg_text)
                inc_stat("total_sent")
                inc_stat("success_sent")
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception as e:
                inc_stat("failed_sent")
                print(f"[Send Error] {chat_id}: {e}")

            await asyncio.sleep(delay)


# ================= COMMAND HANDLERS =================


@app.on_message(filters.me & filters.command("addchat", prefixes="."))
async def add_chat_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ **Usage:** `.addchat <chat_id/@username>`")

    target = args[1].strip()
    try:
        chat_obj = await client.get_chat(target)
        chat_id = str(chat_obj.id)
        title = chat_obj.title or chat_obj.first_name or "Unknown"

        cursor.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)",
            (chat_id, title),
        )
        conn.commit()

        await message.edit_text(
            f"✅ **Chat Added!**\n📌 **Title:** `{title}`\n🆔 **ID:** `{chat_id}`"
        )
    except Exception as e:
        await message.edit_text(f"❌ **Error:** `{e}`")


@app.on_message(filters.me & filters.command("delchat", prefixes="."))
async def del_chat_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ **Usage:** `.delchat <chat_id>`")

    chat_id = args[1].strip()
    cursor.execute("DELETE FROM chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    await message.edit_text(f"🗑 **Chat `{chat_id}` Removed!**")


@app.on_message(filters.me & filters.command("listchats", prefixes="."))
async def list_chats_handler(client, message: Message):
    chats = get_all_chats()
    if not chats:
        return await message.edit_text("ℹ️ Koi chat added nahi hai.")

    text = "📋 **Target Chats List:**\n\n"
    for idx, (c_id, title) in enumerate(chats, 1):
        text += f"{idx}. **{title}** | `{c_id}`\n"

    await message.edit_text(text)


@app.on_message(filters.me & filters.command("setmsg", prefixes="."))
async def set_msg_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text("❌ **Usage:** `.setmsg <your message>`")

    set_config("msg", args[1])
    await message.edit_text(
        f"✅ **Auto Message Updated!**\n\n📝 **New Msg:**\n{args[1]}"
    )


@app.on_message(filters.me & filters.command("setdelay", prefixes="."))
async def set_delay_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].isdigit():
        return await message.edit_text("❌ **Usage:** `.setdelay <seconds>`")

    set_config("delay", args[1])
    await message.edit_text(f"⏱ **Delay set to `{args[1]}` seconds.**")


@app.on_message(filters.me & filters.command("forcesub", prefixes="."))
async def set_forcesub_handler(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.edit_text(
            "❌ **Usage:** `.forcesub <channel_username>`"
        )

    ch = args[1].strip()
    if "t.me/" in ch:
        ch = ch.split("t.me/")[1].replace("/", "")
    ch = ch.replace("@", "").strip()

    set_config("force_sub", ch)
    await message.edit_text(f"🔒 **Force Sub Channel set to:** `@{ch}`")


@app.on_message(filters.me & filters.command("automsg", prefixes="."))
async def toggle_automsg(client, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        return await message.edit_text("❌ **Usage:** `.automsg on` ya `.automsg off`")

    state = args[1].upper()

    if state == "ON":
        if not await is_force_sub_active():
            return await message.edit_text(
                f"❌ **Cannot Start!** Pehle `@{get_config('force_sub')}` channel join karein."
            )

    set_config("status", state)
    await message.edit_text(f"🤖 **Auto Messaging is now switched `{state}`!**")


@app.on_message(filters.me & filters.command("stats", prefixes="."))
async def stats_handler(client, message: Message):
    stats_text = (
        "📊 **Auto-Bot Dashboard & Stats**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status:** `{get_config('status')}`\n"
        f"⏱ **Delay:** `{get_config('delay')}s`\n"
        f"🔒 **Force Sub:** `@{get_config('force_sub') or 'None'}`\n"
        f"🎯 **Target Chats:** `{len(get_all_chats())} Groups`\n\n"
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
    print("Userbot started successfully on Render!")
    asyncio.create_task(auto_broadcast_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
