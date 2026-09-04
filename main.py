import asyncio
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.types import Message


# =========================================================
# PYTHON / ASYNCIO EVENT LOOP FIX
# =========================================================

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


# =========================================================
# ENVIRONMENT
# =========================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

if not API_ID or not API_HASH or not SESSION_STRING:
    raise RuntimeError(
        "API_ID, API_HASH and SESSION_STRING environment variables are required."
    )


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
# DUMMY WEB SERVER - RENDER HEALTH CHECK
# =========================================================

class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Userbot is running perfectly!")

    def log_message(self, format, *args):
        return


def start_dummy_server():
    port = int(os.environ.get("PORT", "8080"))

    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        print(f"Health server running on port {port}")
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
    timeout=30,
    check_same_thread=False
)

cursor = conn.cursor()

# SQLite ko concurrent reads/writes ke liye better banana
cursor.execute("PRAGMA journal_mode=WAL")
cursor.execute("PRAGMA synchronous=NORMAL")
cursor.execute("PRAGMA busy_timeout=30000")

db_lock = threading.RLock()


# =========================================================
# DATABASE TABLES
# =========================================================

with db_lock:

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            title TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """
    )

    conn.commit()


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_config(key, default=""):
    with db_lock:
        cursor.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        )

        result = cursor.fetchone()

        if result is None:
            return default

        return result[0]


def set_config(key, value):
    with db_lock:
        cursor.execute(
            """
            INSERT OR REPLACE INTO config (key, value)
            VALUES (?, ?)
            """,
            (key, str(value))
        )

        conn.commit()


def get_stat(key):
    with db_lock:
        cursor.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )

        result = cursor.fetchone()

        if result is None:
            return 0

        return int(result[0])


def inc_stat(key, amount=1):
    with db_lock:
        cursor.execute(
            """
            INSERT INTO stats (key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = value + excluded.value
            """,
            (key, amount)
        )

        conn.commit()


def get_all_chats():
    with db_lock:
        cursor.execute(
            "SELECT chat_id, title FROM chats ORDER BY rowid"
        )

        return cursor.fetchall()


# =========================================================
# DEFAULT CONFIG
# =========================================================

if get_config("status") == "":
    set_config("status", "OFF")

if get_config("delay") == "":
    set_config("delay", "30")

if get_config("msg") == "":
    set_config("msg", "Default Auto Message")

if get_config("force_sub") == "":
    set_config("force_sub", "")


# =========================================================
# FORCE SUB
# =========================================================

def clean_channel(value):
    value = value.strip()

    if "t.me/" in value:
        value = value.split("t.me/", 1)[1]
        value = value.split("?", 1)[0]
        value = value.rstrip("/")

    value = value.replace("@", "").strip()

    return value


async def is_force_sub_active():
    channel = get_config("force_sub", "")

    # Force sub configured nahi hai
    if not channel:
        return True

    channel = clean_channel(channel)

    if not channel:
        return True

    try:
        me = await app.get_me()

        member = await app.get_chat_member(
            channel,
            me.id
        )

        active_statuses = {
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        }

        if member.status in active_statuses:
            return True

        # LEFT / BANNED etc.
        return False

    except Exception as e:
        # IMPORTANT:
        # Temporary Telegram/API error ko "user left" nahi maana jayega.
        print(f"[ForceSub Check Error] {e}")

        return None


# =========================================================
# AUTO BROADCAST
# =========================================================

broadcast_task = None


async def send_to_chat(target, msg_text):
    """
    Message send karega.
    FloodWait aane par wait karke retry karega.
    """

    while True:

        try:
            await app.send_message(
                target,
                msg_text
            )

            return True

        except FloodWait as e:

            wait_time = int(e.value)

            print(
                f"[FloodWait] Waiting {wait_time} seconds..."
            )

            await asyncio.sleep(wait_time)

        except Exception as e:

            print(
                f"[Send Error] {target}: {e}"
            )

            return False


async def auto_broadcast_loop():

    print("Auto broadcaster started.")

    while True:

        try:
            await asyncio.sleep(2)

            if get_config("status") != "ON":
                continue

            # -------------------------------------------------
            # FORCE SUB CHECK
            # -------------------------------------------------

            force_status = await is_force_sub_active()

            # Telegram/API error:
            # bot ko OFF nahi karna
            if force_status is None:
                continue

            # Actually channel leave kiya
            if force_status is False:

                set_config("status", "OFF")

                force_channel = get_config(
                    "force_sub",
                    ""
                )

                try:
                    await app.send_message(
                        "me",
                        (
                            "❌ **Auto Msg Stopped!**\n\n"
                            f"Force Sub Channel `@{force_channel}` "
                            "me membership nahi mili."
                        )
                    )
                except Exception as e:
                    print(
                        f"[Notification Error] {e}"
                    )

                continue

            # -------------------------------------------------
            # LOAD CONFIG
            # -------------------------------------------------

            chats = get_all_chats()
            msg_text = get_config("msg", "")

            try:
                delay = int(
                    get_config("delay", "30")
                )
            except ValueError:
                delay = 30
                set_config("delay", "30")

            # Safety
            delay = max(1, delay)

            if not chats:
                continue

            if not msg_text:
                continue

            # -------------------------------------------------
            # SEND TO ALL CHATS
            # -------------------------------------------------

            for chat_id, title in chats:

                # Beech me automsg off kar diya gaya
                if get_config("status") != "ON":
                    break

                try:

                    if str(chat_id).lstrip("-").isdigit():
                        target = int(chat_id)
                    else:
                        target = chat_id

                    # Har actual send attempt count
                    inc_stat("total_sent")

                    success = await send_to_chat(
                        target,
                        msg_text
                    )

                    if success:
                        inc_stat("success_sent")
                    else:
                        inc_stat("failed_sent")

                except Exception as e:

                    inc_stat("failed_sent")

                    print(
                        f"[Broadcast Error] "
                        f"{chat_id}: {e}"
                    )

                # Next chat se pehle delay
                await asyncio.sleep(delay)

        except asyncio.CancelledError:

            print("Auto broadcaster stopped.")

            raise

        except Exception as e:

            # Ek unexpected error se complete loop band na ho
            print(
                f"[Broadcast Loop Error] {e}"
            )

            await asyncio.sleep(5)


# =========================================================
# ADD CHAT
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "addchat",
        prefixes="."
    )
)
async def add_chat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.addchat <chat_id/@username>`"
        )

    target = args[1].strip()

    try:

        chat_obj = await client.get_chat(
            target
        )

        chat_id = str(chat_obj.id)

        title = (
            chat_obj.title
            or chat_obj.first_name
            or "Unknown"
        )

        with db_lock:

            cursor.execute(
                """
                INSERT OR REPLACE INTO chats
                (chat_id, title)
                VALUES (?, ?)
                """,
                (chat_id, title)
            )

            conn.commit()

        await message.edit_text(
            f"✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`"
        )

    except Exception as e:

        await message.edit_text(
            f"❌ **Error:** `{str(e)[:3000]}`"
        )


# =========================================================
# DELETE CHAT
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "delchat",
        prefixes="."
    )
)
async def del_chat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.delchat <chat_id>`"
        )

    chat_id = args[1].strip()

    with db_lock:

        cursor.execute(
            "DELETE FROM chats WHERE chat_id=?",
            (chat_id,)
        )

        deleted = cursor.rowcount

        conn.commit()

    if deleted == 0:

        return await message.edit_text(
            f"ℹ️ Chat `{chat_id}` database me nahi mili."
        )

    await message.edit_text(
        f"🗑 **Chat `{chat_id}` Removed!**"
    )


# =========================================================
# LIST CHATS
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "listchats",
        prefixes="."
    )
)
async def list_chats_handler(
    client,
    message: Message
):

    chats = get_all_chats()

    if not chats:

        return await message.edit_text(
            "ℹ️ Koi chat added nahi hai."
        )

    lines = [
        "📋 **Target Chats List:**",
        ""
    ]

    for idx, (chat_id, title) in enumerate(
        chats,
        1
    ):

        lines.append(
            f"{idx}. **{title}** | `{chat_id}`"
        )

    text = "\n".join(lines)

    # Telegram message limit protection
    if len(text) > 4000:

        text = (
            text[:3900]
            + "\n\n⚠️ List bahut badi hai."
        )

    await message.edit_text(text)


# =========================================================
# SET MESSAGE
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "setmsg",
        prefixes="."
    )
)
async def set_msg_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.setmsg <your message>`"
        )

    new_message = args[1]

    # Telegram message size safety
    if len(new_message) > 4096:

        return await message.edit_text(
            "❌ Message 4096 characters se zyada nahi ho sakta."
        )

    set_config(
        "msg",
        new_message
    )

    await message.edit_text(
        "✅ **Auto Message Updated!**\n\n"
        "📝 **New Msg:**\n"
        f"{new_message}"
    )


# =========================================================
# SET DELAY
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "setdelay",
        prefixes="."
    )
)
async def set_delay_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.setdelay <seconds>`"
        )

    value = args[1].strip()

    if not value.isdigit():

        return await message.edit_text(
            "❌ Delay sirf number me dein."
        )

    delay = int(value)

    # 0 seconds allow nahi
    if delay < 1:

        return await message.edit_text(
            "❌ Minimum delay **1 second** hai."
        )

    # Accidental huge value prevent
    if delay > 86400:

        return await message.edit_text(
            "❌ Maximum delay **86400 seconds (24 hours)** hai."
        )

    set_config(
        "delay",
        str(delay)
    )

    await message.edit_text(
        f"⏱ **Delay set to `{delay}` seconds.**"
    )


# =========================================================
# FORCE SUB
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "forcesub",
        prefixes="."
    )
)
async def set_forcesub_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return await message.edit_text(
            "❌ **Usage:** `.forcesub <channel_username>`"
        )

    channel = clean_channel(
        args[1]
    )

    if not channel:

        return await message.edit_text(
            "❌ Valid channel username dein."
        )

    set_config(
        "force_sub",
        channel
    )

    await message.edit_text(
        f"🔒 **Force Sub Channel set to:** `@{channel}`"
    )


# =========================================================
# AUTO MESSAGE ON / OFF
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "automsg",
        prefixes="."
    )
)
async def toggle_automsg(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if (
        len(args) < 2
        or args[1].lower() not in ["on", "off"]
    ):

        return await message.edit_text(
            "❌ **Usage:** `.automsg on` ya `.automsg off`"
        )

    state = args[1].upper()

    # -------------------------------------------------
    # ON
    # -------------------------------------------------

    if state == "ON":

        force_status = await is_force_sub_active()

        # Actual membership problem
        if force_status is False:

            channel = get_config(
                "force_sub",
                ""
            )

            return await message.edit_text(
                f"❌ **Cannot Start!**\n\n"
                f"Pehle `@{channel}` channel join karein."
            )

        # Telegram/API temporary error
        if force_status is None:

            return await message.edit_text(
                "⚠️ Force Sub check nahi ho paaya.\n"
                "Thodi der baad dobara try karein."
            )

    # -------------------------------------------------
    # SAVE STATE
    # -------------------------------------------------

    set_config(
        "status",
        state
    )

    await message.edit_text(
        f"🤖 **Auto Messaging is now switched `{state}`!**"
    )


# =========================================================
# STATS
# =========================================================

@app.on_message(
    filters.me & filters.command(
        "stats",
        prefixes="."
    )
)
async def stats_handler(
    client,
    message: Message
):

    status = get_config(
        "status",
        "OFF"
    )

    delay = get_config(
        "delay",
        "30"
    )

    force_sub = get_config(
        "force_sub",
        ""
    )

    chats = get_all_chats()

    total = get_stat(
        "total_sent"
    )

    success = get_stat(
        "success_sent"
    )

    failed = get_stat(
        "failed_sent"
    )

    current_message = get_config(
        "msg",
        ""
    )

    # Stats me message ko safe length tak rakho
    if len(current_message) > 1000:
        current_message = (
            current_message[:1000]
            + "\n..."
        )

    stats_text = (
        "📊 **Auto-Bot Dashboard & Stats**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status:** `{status}`\n"
        f"⏱ **Delay:** `{delay}s`\n"
        f"🔒 **Force Sub:** "
        f"`@{force_sub or 'None'}`\n"
        f"🎯 **Target Chats:** "
        f"`{len(chats)} Groups`\n\n"
        "📈 **Sending History:**\n"
        f"├ 🚀 **Total Attempts:** `{total}`\n"
        f"├ ✅ **Successfully Sent:** `{success}`\n"
        f"└ ❌ **Failed:** `{failed}`\n\n"
        "📝 **Current Active Message:**\n"
        f"{current_message}"
    )

    if len(stats_text) > 4096:

        stats_text = (
            stats_text[:4000]
            + "\n\n⚠️ Message shortened."
        )

    await message.edit_text(
        stats_text
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    global broadcast_task

    print("Starting userbot...")

    await app.start()

    print(
        "Userbot started successfully on Render!"
    )

    # Duplicate broadcaster prevent
    if (
        broadcast_task is None
        or broadcast_task.done()
    ):
        broadcast_task = asyncio.create_task(
            auto_broadcast_loop()
        )

    # Bot ko alive rakho
    await asyncio.Event().wait()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    loop = asyncio.get_event_loop()

    try:

        loop.run_until_complete(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Userbot stopped manually."
        )

    finally:

        if loop.is_running():
            loop.stop()
