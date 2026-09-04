import asyncio
import os
import sqlite3
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message


# =========================================================
# ASYNCIO FIX
# =========================================================

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


# =========================================================
# ENVIRONMENT
# =========================================================

API_ID_RAW = os.environ.get("API_ID", "")
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

print("========================================", flush=True)
print("        USERBOT STARTUP CHECK", flush=True)
print("========================================", flush=True)

print(
    f"[ENV] API_ID present: {bool(API_ID_RAW)}",
    flush=True
)

print(
    f"[ENV] API_HASH present: {bool(API_HASH)}",
    flush=True
)

print(
    f"[ENV] SESSION_STRING present: {bool(SESSION_STRING)}",
    flush=True
)

try:
    API_ID = int(API_ID_RAW)
except Exception:
    print(
        "[FATAL] API_ID is missing or invalid!",
        flush=True
    )
    raise


if not API_HASH:
    raise RuntimeError(
        "API_HASH environment variable is missing."
    )

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING environment variable is missing."
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

print(
    "[OK] Pyrogram Client object created.",
    flush=True
)


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Userbot is running!"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    try:

        port = int(
            os.environ.get(
                "PORT",
                "8080"
            )
        )

        server = HTTPServer(
            ("0.0.0.0", port),
            HealthCheckHandler
        )

        print(
            f"[OK] Health server listening on port {port}",
            flush=True
        )

        server.serve_forever()

    except Exception:

        print(
            "[HEALTH SERVER ERROR]",
            flush=True
        )

        traceback.print_exc()


threading.Thread(
    target=start_health_server,
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

db_lock = threading.RLock()


with db_lock:

    cursor.execute(
        "PRAGMA journal_mode=WAL"
    )

    cursor.execute(
        "PRAGMA synchronous=NORMAL"
    )

    cursor.execute(
        "PRAGMA busy_timeout=30000"
    )

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


print(
    "[OK] SQLite database initialized.",
    flush=True
)


# =========================================================
# DATABASE FUNCTIONS
# =========================================================

def get_config(key, default=""):

    with db_lock:

        cursor.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        )

        result = cursor.fetchone()

        if result:
            return result[0]

        return default


def set_config(key, value):

    with db_lock:

        cursor.execute(
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

        cursor.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )

        result = cursor.fetchone()

        if result:
            return int(result[0])

        return 0


def inc_stat(key, amount=1):

    with db_lock:

        current = get_stat(key)

        cursor.execute(
            """
            INSERT OR REPLACE INTO stats
            (key, value)
            VALUES (?, ?)
            """,
            (
                key,
                current + amount
            )
        )

        conn.commit()


def get_all_chats():

    with db_lock:

        cursor.execute(
            """
            SELECT chat_id, title
            FROM chats
            ORDER BY rowid
            """
        )

        return cursor.fetchall()


# =========================================================
# DEFAULT SETTINGS
# =========================================================

if get_config("status", "") == "":
    set_config("status", "OFF")

if get_config("delay", "") == "":
    set_config("delay", "30")

if get_config("msg", "") == "":
    set_config(
        "msg",
        "Default Auto Message"
    )

if get_config("force_sub", "") == "":
    set_config("force_sub", "")


# =========================================================
# FORCE SUB CLEANER
# =========================================================

def clean_channel(channel):

    channel = channel.strip()

    if "t.me/" in channel:

        channel = channel.split(
            "t.me/",
            1
        )[1]

        channel = channel.split(
            "?",
            1
        )[0]

        channel = channel.rstrip("/")

    channel = channel.replace(
        "@",
        ""
    ).strip()

    return channel


# =========================================================
# FORCE SUB CHECK
# =========================================================

async def is_force_sub_active():

    channel = get_config(
        "force_sub",
        ""
    )

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

        return member.status in active_statuses

    except Exception as e:

        print(
            f"[FORCE SUB ERROR] {e}",
            flush=True
        )

        traceback.print_exc()

        # None = Telegram/API error
        # False = actual membership failure
        return None


# =========================================================
# SEND MESSAGE
# =========================================================

async def send_to_chat(
    target,
    text
):

    while True:

        try:

            await app.send_message(
                target,
                text
            )

            return True

        except FloodWait as e:

            wait_time = int(e.value)

            print(
                f"[FLOODWAIT] "
                f"{target} -> waiting {wait_time}s",
                flush=True
            )

            await asyncio.sleep(
                wait_time
            )

        except Exception as e:

            print(
                f"[SEND ERROR] {target}: {e}",
                flush=True
            )

            return False


# =========================================================
# AUTO BROADCAST LOOP
# =========================================================

async def auto_broadcast_loop():

    print(
        "[OK] Auto broadcaster started.",
        flush=True
    )

    while True:

        try:

            await asyncio.sleep(2)

            if get_config("status") != "ON":
                continue

            # ---------------------------------------------
            # FORCE SUB
            # ---------------------------------------------

            force_status = (
                await is_force_sub_active()
            )

            # Telegram/API temporary error
            if force_status is None:
                continue

            # Actually not member
            if force_status is False:

                set_config(
                    "status",
                    "OFF"
                )

                channel = get_config(
                    "force_sub",
                    ""
                )

                try:

                    await app.send_message(
                        "me",
                        (
                            "❌ **Auto Msg Stopped!**\n\n"
                            f"Force Sub Channel `@{channel}` "
                            "membership nahi mili."
                        )
                    )

                except Exception:
                    traceback.print_exc()

                continue

            # ---------------------------------------------
            # CONFIG
            # ---------------------------------------------

            chats = get_all_chats()

            msg_text = get_config(
                "msg",
                ""
            )

            try:

                delay = int(
                    get_config(
                        "delay",
                        "30"
                    )
                )

            except Exception:

                delay = 30

                set_config(
                    "delay",
                    "30"
                )

            delay = max(
                1,
                delay
            )

            if not chats:
                continue

            if not msg_text:
                continue

            # ---------------------------------------------
            # SEND
            # ---------------------------------------------

            for chat_id, title in chats:

                if get_config("status") != "ON":
                    break

                try:

                    if str(chat_id).lstrip("-").isdigit():

                        target = int(chat_id)

                    else:

                        target = chat_id

                    inc_stat(
                        "total_sent"
                    )

                    success = await send_to_chat(
                        target,
                        msg_text
                    )

                    if success:

                        inc_stat(
                            "success_sent"
                        )

                    else:

                        inc_stat(
                            "failed_sent"
                        )

                except Exception as e:

                    inc_stat(
                        "failed_sent"
                    )

                    print(
                        f"[BROADCAST ERROR] "
                        f"{chat_id}: {e}",
                        flush=True
                    )

                await asyncio.sleep(
                    delay
                )

        except asyncio.CancelledError:

            print(
                "[OK] Auto broadcaster stopped.",
                flush=True
            )

            raise

        except Exception as e:

            print(
                f"[BROADCAST LOOP ERROR] {e}",
                flush=True
            )

            traceback.print_exc()

            await asyncio.sleep(5)


# =========================================================
# COMMAND: ADD CHAT
# =========================================================

async def add_chat_handler(
    client,
    message: Message
):

    try:

        args = message.text.split(
            maxsplit=1
        )

        if len(args) < 2:

            return await message.edit_text(
                "❌ **Usage:** "
                "`.addchat <chat_id/@username>`"
            )

        target = args[1].strip()

        chat_obj = await client.get_chat(
            target
        )

        chat_id = str(
            chat_obj.id
        )

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
                (
                    chat_id,
                    title
                )
            )

            conn.commit()

        await message.edit_text(
            "✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`"
        )

    except Exception as e:

        print(
            f"[ADDCHAT ERROR] {e}",
            flush=True
        )

        traceback.print_exc()

        await message.edit_text(
            f"❌ **Error:** `{str(e)[:3000]}`"
        )


# =========================================================
# COMMAND: DELETE CHAT
# =========================================================

async def del_chat_handler(
    client,
    message: Message
):

    try:

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

    except Exception as e:

        print(
            f"[DELCHAT ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: LIST CHATS
# =========================================================

async def list_chats_handler(
    client,
    message: Message
):

    try:

        chats = get_all_chats()

        if not chats:

            return await message.edit_text(
                "ℹ️ Koi chat added nahi hai."
            )

        lines = [
            "📋 **Target Chats List:**",
            ""
        ]

        for index, (
            chat_id,
            title
        ) in enumerate(
            chats,
            1
        ):

            lines.append(
                f"{index}. **{title}** | `{chat_id}`"
            )

        text = "\n".join(
            lines
        )

        if len(text) > 4000:

            text = (
                text[:3900]
                + "\n\n⚠️ List shortened."
            )

        await message.edit_text(
            text
        )

    except Exception as e:

        print(
            f"[LISTCHATS ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: SET MESSAGE
# =========================================================

async def set_msg_handler(
    client,
    message: Message
):

    try:

        args = message.text.split(
            maxsplit=1
        )

        if len(args) < 2:

            return await message.edit_text(
                "❌ **Usage:** `.setmsg <your message>`"
            )

        new_message = args[1]

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

    except Exception as e:

        print(
            f"[SETMSG ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: SET DELAY
# =========================================================

async def set_delay_handler(
    client,
    message: Message
):

    try:

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

        if delay < 1:

            return await message.edit_text(
                "❌ Minimum delay 1 second hai."
            )

        if delay > 86400:

            return await message.edit_text(
                "❌ Maximum delay 86400 seconds hai."
            )

        set_config(
            "delay",
            str(delay)
        )

        await message.edit_text(
            f"⏱ **Delay set to `{delay}` seconds.**"
        )

    except Exception as e:

        print(
            f"[SETDELAY ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: FORCE SUB
# =========================================================

async def set_forcesub_handler(
    client,
    message: Message
):

    try:

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
            f"🔒 **Force Sub Channel set to:** "
            f"`@{channel}`"
        )

    except Exception as e:

        print(
            f"[FORCESUB ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: AUTO MSG
# =========================================================

async def toggle_automsg(
    client,
    message: Message
):

    try:

        args = message.text.split(
            maxsplit=1
        )

        if (
            len(args) < 2
            or args[1].lower()
            not in ["on", "off"]
        ):

            return await message.edit_text(
                "❌ **Usage:** "
                "`.automsg on` ya `.automsg off`"
            )

        state = args[1].upper()

        if state == "ON":

            force_status = (
                await is_force_sub_active()
            )

            if force_status is False:

                channel = get_config(
                    "force_sub",
                    ""
                )

                return await message.edit_text(
                    "❌ **Cannot Start!**\n\n"
                    f"Pehle `@{channel}` "
                    "channel join karein."
                )

            if force_status is None:

                return await message.edit_text(
                    "⚠️ Force Sub check nahi ho paaya.\n"
                    "Thodi der baad dobara try karein."
                )

        set_config(
            "status",
            state
        )

        await message.edit_text(
            f"🤖 **Auto Messaging is now switched "
            f"`{state}`!**"
        )

    except Exception as e:

        print(
            f"[AUTOMSG ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# COMMAND: STATS
# =========================================================

async def stats_handler(
    client,
    message: Message
):

    try:

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

        if len(current_message) > 1000:

            current_message = (
                current_message[:1000]
                + "\n..."
            )

        text = (
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

        if len(text) > 4096:

            text = (
                text[:4000]
                + "\n\n⚠️ Stats shortened."
            )

        await message.edit_text(
            text
        )

    except Exception as e:

        print(
            f"[STATS ERROR] {e}",
            flush=True
        )

        traceback.print_exc()


# =========================================================
# EXPLICIT COMMAND REGISTRATION
# =========================================================
#
# Decorators ki jagah yahan explicitly handlers register
# kiye ja rahe hain. Isse startup log me confirm ho jayega
# ki saare handlers add hue hain.
# =========================================================

COMMANDS = [
    (
        "addchat",
        add_chat_handler
    ),
    (
        "delchat",
        del_chat_handler
    ),
    (
        "listchats",
        list_chats_handler
    ),
    (
        "setmsg",
        set_msg_handler
    ),
    (
        "setdelay",
        set_delay_handler
    ),
    (
        "forcesub",
        set_forcesub_handler
    ),
    (
        "automsg",
        toggle_automsg
    ),
    (
        "stats",
        stats_handler
    ),
]


for command_name, handler_function in COMMANDS:

    app.add_handler(
        MessageHandler(
            handler_function,
            filters.me
            & filters.command(
                command_name,
                prefixes="."
            )
        )
    )

    print(
        f"[HANDLER OK] .{command_name}",
        flush=True
    )


print(
    f"[OK] Total command handlers registered: "
    f"{len(COMMANDS)}",
    flush=True
)


# =========================================================
# MAIN
# =========================================================

async def main():

    broadcast_task = None

    print(
        "========================================",
        flush=True
    )

    print(
        "        STARTING PYROGRAM",
        flush=True
    )

    print(
        "========================================",
        flush=True
    )

    try:

        print(
            "[START] Calling app.start()...",
            flush=True
        )

        # 60 sec timeout:
        # agar Telegram connection/authentication par
        # atak gaya to Render log me exact error milega.
        await asyncio.wait_for(
            app.start(),
            timeout=60
        )

        print(
            "[OK] app.start() completed!",
            flush=True
        )

        # ---------------------------------------------
        # GET ACCOUNT INFO
        # ---------------------------------------------

        try:

            me = await app.get_me()

            print(
                "========================================",
                flush=True
            )

            print(
                "[LOGIN SUCCESS]",
                flush=True
            )

            print(
                f"Name: {me.first_name or ''}",
                flush=True
            )

            print(
                f"Username: "
                f"@{me.username}"
                if me.username
                else "Username: None",
                flush=True
            )

            print(
                f"User ID: {me.id}",
                flush=True
            )

            print(
                "========================================",
                flush=True
            )

        except Exception as e:

            print(
                "[GET ME ERROR]",
                flush=True
            )

            print(
                repr(e),
                flush=True
            )

            traceback.print_exc()

            raise

        # ---------------------------------------------
        # START BROADCASTER
        # ---------------------------------------------

        broadcast_task = asyncio.create_task(
            auto_broadcast_loop()
        )

        print(
            "[OK] Background broadcaster task created.",
            flush=True
        )

        print(
            "========================================",
            flush=True
        )

        print(
            "       USERBOT IS FULLY RUNNING",
            flush=True
        )

        print(
            "========================================",
            flush=True
        )

        print(
            "Commands:",
            flush=True
        )

        for command_name, _ in COMMANDS:

            print(
                f"  .{command_name}",
                flush=True
            )

        print(
            "========================================",
            flush=True
        )

        # ---------------------------------------------
        # KEEP ALIVE
        # ---------------------------------------------

        await asyncio.Event().wait()

    except asyncio.TimeoutError:

        print(
            "========================================",
            flush=True
        )

        print(
            "[FATAL] Pyrogram app.start() "
            "timed out after 60 seconds!",
            flush=True
        )

        print(
            "Possible causes:",
            flush=True
        )

        print(
            "1. Invalid/expired SESSION_STRING",
            flush=True
        )

        print(
            "2. Invalid API_ID/API_HASH",
            flush=True
        )

        print(
            "3. Telegram connection problem",
            flush=True
        )

        print(
            "========================================",
            flush=True
        )

        raise

    except Exception as e:

        print(
            "========================================",
            flush=True
        )

        print(
            "[FATAL STARTUP ERROR]",
            flush=True
        )

        print(
            f"Error type: {type(e).__name__}",
            flush=True
        )

        print(
            f"Error: {e}",
            flush=True
        )

        print(
            "FULL TRACEBACK:",
            flush=True
        )

        traceback.print_exc()

        print(
            "========================================",
            flush=True
        )

        raise

    finally:

        if broadcast_task:

            broadcast_task.cancel()

            try:
                await broadcast_task
            except asyncio.CancelledError:
                pass

        try:

            if app.is_connected:

                print(
                    "[STOP] Stopping Pyrogram...",
                    flush=True
                )

                await app.stop()

        except Exception:

            traceback.print_exc()

        try:

            conn.close()

        except Exception:

            pass


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    print(
        "[BOOT] main.py loaded successfully.",
        flush=True
    )

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "[STOP] Userbot stopped manually.",
            flush=True
        )

    except Exception as e:

        print(
            "========================================",
            flush=True
        )

        print(
            "[PROCESS EXIT]",
            flush=True
        )

        print(
            f"{type(e).__name__}: {e}",
            flush=True
        )

        traceback.print_exc()

        print(
            "========================================",
            flush=True
        )

        raise
