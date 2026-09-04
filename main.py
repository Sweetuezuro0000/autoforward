import asyncio
import os
import sqlite3
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message


# =========================================================
# ENVIRONMENT
# =========================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

print(f"API_ID present: {bool(API_ID)}")
print(f"API_HASH present: {bool(API_HASH)}")
print(f"SESSION_STRING present: {bool(SESSION_STRING)}")


# =========================================================
# PYROGRAM CLIENT
# =========================================================

app = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Userbot is running perfectly!")

    def log_message(self, format, *args):
        return


def start_dummy_server():
    try:
        port = int(os.environ.get("PORT", "10000"))

        server = HTTPServer(
            ("0.0.0.0", port),
            HealthCheckHandler
        )

        print(f"Health server started on port {port}")

        server.serve_forever()

    except Exception as e:
        print(f"[Health Server Error] {e}")


threading.Thread(
    target=start_dummy_server,
    daemon=True
).start()


# =========================================================
# DATABASE
# =========================================================

DB_FILE = "userbot_data.db"

conn = sqlite3.connect(
    DB_FILE,
    check_same_thread=False,
    timeout=30
)

db_lock = threading.RLock()

with db_lock:

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            title TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)

    conn.commit()

print("SQLite database initialized")


# =========================================================
# DATABASE FUNCTIONS
# =========================================================

def get_config(key, default=""):

    with db_lock:

        cur = conn.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        )

        row = cur.fetchone()

        return row[0] if row else default


def set_config(key, value):

    with db_lock:

        conn.execute(
            """
            INSERT OR REPLACE INTO config
            (key, value)
            VALUES (?, ?)
            """,
            (key, str(value))
        )

        conn.commit()


def get_stat(key):

    with db_lock:

        cur = conn.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )

        row = cur.fetchone()

        return int(row[0]) if row else 0


def inc_stat(key, amount=1):

    with db_lock:

        cur = conn.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )

        row = cur.fetchone()

        current = int(row[0]) if row else 0

        conn.execute(
            """
            INSERT OR REPLACE INTO stats
            (key, value)
            VALUES (?, ?)
            """,
            (key, current + amount)
        )

        conn.commit()


def get_all_chats():

    with db_lock:

        cur = conn.execute(
            "SELECT chat_id, title FROM chats ORDER BY rowid"
        )

        return cur.fetchall()


def add_chat_db(chat_id, title):

    with db_lock:

        conn.execute(
            """
            INSERT OR REPLACE INTO chats
            (chat_id, title)
            VALUES (?, ?)
            """,
            (chat_id, title)
        )

        conn.commit()


def delete_chat_db(chat_id):

    with db_lock:

        cur = conn.execute(
            "DELETE FROM chats WHERE chat_id=?",
            (chat_id,)
        )

        conn.commit()

        return cur.rowcount


# =========================================================
# DEFAULT CONFIG
# =========================================================

if not get_config("status"):
    set_config("status", "OFF")

if not get_config("delay"):
    set_config("delay", "30")

if not get_config("msg"):
    set_config("msg", "Default Auto Message")

if not get_config("force_sub"):
    set_config("force_sub", "")


# =========================================================
# FORCE SUB
# =========================================================

async def is_force_sub_active():

    channel = get_config("force_sub")

    if not channel:
        return True

    try:

        if "t.me/" in channel:
            channel = channel.split("t.me/", 1)[1]

        channel = channel.replace("/", "")
        channel = channel.replace("@", "").strip()

        me = await app.get_me()

        member = await app.get_chat_member(
            channel,
            me.id
        )

        if member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER
        ):
            return True

        return False

    except Exception as e:

        print(f"[ForceSub API Error] {e}")

        # API error ko "not joined" mat samjho
        return None


# =========================================================
# AUTO BROADCAST
# =========================================================

broadcast_task = None


async def auto_broadcast_loop():

    print("Auto broadcaster started")

    while True:

        try:

            await asyncio.sleep(2)

            if get_config("status") != "ON":
                continue

            force_status = await is_force_sub_active()

            # Actual membership failure
            if force_status is False:

                set_config("status", "OFF")

                try:
                    await app.send_message(
                        "me",
                        "❌ **Auto Msg Stopped!**\n"
                        "Force Sub channel se aap member nahi ho."
                    )
                except Exception:
                    pass

                continue

            # Telegram/API temporary error
            if force_status is None:
                print("[ForceSub] Check failed, auto messaging kept ON")
                continue

            chats = get_all_chats()
            msg_text = get_config("msg")
            delay_text = get_config("delay", "30")

            try:
                delay = max(1, int(delay_text))
            except ValueError:
                delay = 30

            if not chats:
                continue

            if not msg_text:
                continue

            for chat_id, title in chats:

                if get_config("status") != "ON":
                    break

                try:

                    if chat_id.lstrip("-").isdigit():
                        target = int(chat_id)
                    else:
                        target = chat_id

                    await app.send_message(
                        target,
                        msg_text
                    )

                    inc_stat("total_sent")
                    inc_stat("success_sent")

                    print(
                        f"[SENT] {title} ({chat_id})"
                    )

                    await asyncio.sleep(delay)

                except FloodWait as e:

                    print(
                        f"[FloodWait] {chat_id}: "
                        f"waiting {e.value}s"
                    )

                    await asyncio.sleep(e.value)

                    # FloodWait ke baad isi chat ko retry
                    try:

                        await app.send_message(
                            target,
                            msg_text
                        )

                        inc_stat("total_sent")
                        inc_stat("success_sent")

                        print(
                            f"[RETRY SENT] {title} ({chat_id})"
                        )

                    except Exception as retry_error:

                        inc_stat("failed_sent")

                        print(
                            f"[Retry Error] "
                            f"{chat_id}: {retry_error}"
                        )

                    await asyncio.sleep(delay)

                except Exception as e:

                    inc_stat("total_sent")
                    inc_stat("failed_sent")

                    print(
                        f"[Send Error] "
                        f"{chat_id}: {e}"
                    )

        except asyncio.CancelledError:

            print("Auto broadcaster stopped")

            raise

        except Exception as e:

            print(f"[Broadcast Loop Error] {e}")

            traceback.print_exc()

            await asyncio.sleep(5)


# =========================================================
# COMMAND DEBUG
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.regex(r"^\.[A-Za-z0-9_]+")
)
async def command_debug(client, message: Message):

    print(
        f"[COMMAND RECEIVED] "
        f"text={message.text!r} "
        f"chat_id={message.chat.id if message.chat else None} "
        f"outgoing={message.outgoing}"
    )


# =========================================================
# .addchat
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("addchat", prefixes=".")
)
async def add_chat_handler(client, message: Message):

    print(f"[CMD] addchat -> {message.text}")

    args = message.text.split(maxsplit=1)

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.addchat <chat_id/@username>`"
        )

    target = args[1].strip()

    try:

        chat_obj = await client.get_chat(target)

        chat_id = str(chat_obj.id)

        title = (
            chat_obj.title
            or chat_obj.first_name
            or chat_obj.username
            or "Unknown"
        )

        add_chat_db(
            chat_id,
            title
        )

        await message.edit_text(
            f"✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`"
        )

    except Exception as e:

        print(f"[addchat Error] {e}")

        await message.edit_text(
            f"❌ **Error:** `{str(e)[:3000]}`"
        )


# =========================================================
# .delchat
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("delchat", prefixes=".")
)
async def del_chat_handler(client, message: Message):

    print(f"[CMD] delchat -> {message.text}")

    args = message.text.split(maxsplit=1)

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.delchat <chat_id>`"
        )

    chat_id = args[1].strip()

    deleted = delete_chat_db(chat_id)

    if deleted:

        await message.edit_text(
            f"🗑 **Chat `{chat_id}` Removed!**"
        )

    else:

        await message.edit_text(
            f"ℹ️ Chat `{chat_id}` list me nahi mila."
        )


# =========================================================
# .listchats
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("listchats", prefixes=".")
)
async def list_chats_handler(client, message: Message):

    print(f"[CMD] listchats -> {message.text}")

    chats = get_all_chats()

    if not chats:

        return await message.edit_text(
            "ℹ️ Koi chat added nahi hai."
        )

    text = "📋 **Target Chats List:**\n\n"

    for idx, (chat_id, title) in enumerate(chats, 1):

        line = (
            f"{idx}. **{title}**\n"
            f"   `{chat_id}`\n\n"
        )

        if len(text) + len(line) > 3800:

            await message.edit_text(text)

            # Agar bahut zyada chats hain
            # remaining data Saved Messages me bhej do
            remaining = (
                "📋 **More Target Chats:**\n\n"
                + line
            )

            for idx2, (cid, ttl) in enumerate(
                chats[idx:],
                idx + 1
            ):

                remaining += (
                    f"{idx2}. **{ttl}**\n"
                    f"   `{cid}`\n\n"
                )

                if len(remaining) > 3800:

                    await client.send_message(
                        "me",
                        remaining
                    )

                    remaining = ""

            if remaining:

                await client.send_message(
                    "me",
                    remaining
                )

            return

        text += line

    await message.edit_text(text)


# =========================================================
# .setmsg
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("setmsg", prefixes=".")
)
async def set_msg_handler(client, message: Message):

    print(f"[CMD] setmsg -> {message.text}")

    args = message.text.split(maxsplit=1)

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.setmsg <your message>`"
        )

    new_msg = args[1]

    if len(new_msg) > 4096:

        return await message.edit_text(
            "❌ Message 4096 characters se zyada nahi ho sakta."
        )

    set_config(
        "msg",
        new_msg
    )

    preview = new_msg[:3000]

    await message.edit_text(
        f"✅ **Auto Message Updated!**\n\n"
        f"📝 **New Msg:**\n{preview}"
    )


# =========================================================
# .setdelay
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("setdelay", prefixes=".")
)
async def set_delay_handler(client, message: Message):

    print(f"[CMD] setdelay -> {message.text}")

    args = message.text.split(maxsplit=1)

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.setdelay <seconds>`"
        )

    value = args[1].strip()

    if not value.isdigit():

        return await message.edit_text(
            "❌ Delay sirf number hona chahiye."
        )

    seconds = int(value)

    if seconds < 1:

        return await message.edit_text(
            "❌ Minimum delay **1 second** hai."
        )

    set_config(
        "delay",
        str(seconds)
    )

    await message.edit_text(
        f"⏱ **Delay set to `{seconds}` seconds.**"
    )


# =========================================================
# .forcesub
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("forcesub", prefixes=".")
)
async def set_forcesub_handler(client, message: Message):

    print(f"[CMD] forcesub -> {message.text}")

    args = message.text.split(maxsplit=1)

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.forcesub <channel_username>`"
        )

    ch = args[1].strip()

    if "t.me/" in ch:

        ch = ch.split("t.me/", 1)[1]

    ch = ch.replace("/", "")
    ch = ch.replace("@", "").strip()

    if not ch:

        return await message.edit_text(
            "❌ Invalid channel."
        )

    set_config(
        "force_sub",
        ch
    )

    await message.edit_text(
        f"🔒 **Force Sub Channel set to:** `@{ch}`"
    )


# =========================================================
# .automsg
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("automsg", prefixes=".")
)
async def toggle_automsg(client, message: Message):

    print(f"[CMD] automsg -> {message.text}")

    args = message.text.split(maxsplit=1)

    if (
        len(args) < 2
        or args[1].lower() not in ("on", "off")
    ):

        return await message.edit_text(
            "❌ **Usage:** `.automsg on` ya `.automsg off`"
        )

    state = args[1].upper()

    if state == "ON":

        force_status = await is_force_sub_active()

        if force_status is False:

            return await message.edit_text(
                "❌ **Cannot Start!**\n"
                f"Pehle `@{get_config('force_sub')}` "
                "channel join karein."
            )

        if force_status is None:

            return await message.edit_text(
                "⚠️ Force Sub check nahi ho paya.\n"
                "Thodi der baad dobara try karo."
            )

    set_config(
        "status",
        state
    )

    await message.edit_text(
        f"🤖 **Auto Messaging is now switched `{state}`!**"
    )


# =========================================================
# .stats
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("stats", prefixes=".")
)
async def stats_handler(client, message: Message):

    print(f"[CMD] stats -> {message.text}")

    status = get_config("status")
    delay = get_config("delay")
    force_sub = get_config("force_sub") or "None"

    chats = get_all_chats()

    total = get_stat("total_sent")
    success = get_stat("success_sent")
    failed = get_stat("failed_sent")

    msg = get_config("msg")

    if len(msg) > 1000:
        msg = msg[:1000] + "..."

    stats_text = (
        "📊 **Auto-Bot Dashboard & Stats**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status:** `{status}`\n"
        f"⏱ **Delay:** `{delay}s`\n"
        f"🔒 **Force Sub:** `@{force_sub}`\n"
        f"🎯 **Target Chats:** `{len(chats)}`\n\n"
        "📈 **Sending History:**\n"
        f"├ 🚀 **Total Attempts:** `{total}`\n"
        f"├ ✅ **Successfully Sent:** `{success}`\n"
        f"└ ❌ **Failed:** `{failed}`\n\n"
        "📝 **Current Active Message:**\n"
        f"`{msg}`"
    )

    await message.edit_text(stats_text)


# =========================================================
# RUNNER
# =========================================================

async def main():

    global broadcast_task

    try:

        print("Starting Pyrogram client...")

        await app.start()

        me = await app.get_me()

        print(
            f"Login successful: "
            f"user_id={me.id}, "
            f"username=@{me.username or 'none'}"
        )

        if broadcast_task is None or broadcast_task.done():

            broadcast_task = asyncio.create_task(
                auto_broadcast_loop()
            )

            print("Background broadcaster created")

        print("====================================")
        print("USERBOT IS FULLY RUNNING")
        print("Commands use filters.outgoing")
        print("====================================")

        await asyncio.Event().wait()

    except Exception as e:

        print(f"[MAIN ERROR] {e}")

        traceback.print_exc()

        raise

    finally:

        if broadcast_task:

            broadcast_task.cancel()

            try:
                await broadcast_task
            except asyncio.CancelledError:
                pass

        try:
            await app.stop()
        except Exception:
            pass


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("Userbot stopped")

    except Exception as e:

        print(f"Fatal error: {e}")
        traceback.print_exc()
