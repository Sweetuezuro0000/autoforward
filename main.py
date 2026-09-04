import asyncio

# Python 3.10+ / 3.14 asyncio event loop fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import random
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.types import Message


# =========================================================
# CREDENTIALS
# =========================================================

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

app = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


# =========================================================
# DUMMY WEB SERVER
# =========================================================

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


threading.Thread(
    target=start_dummy_server,
    daemon=True
).start()


# =========================================================
# DATABASE
# =========================================================

conn = sqlite3.connect(
    "userbot_data.db",
    check_same_thread=False
)

cursor = conn.cursor()
db_lock = threading.Lock()

with db_lock:
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS chats "
        "(chat_id TEXT PRIMARY KEY, title TEXT)"
    )

    cursor.execute(
        "CREATE TABLE IF NOT EXISTS config "
        "(key TEXT PRIMARY KEY, value TEXT)"
    )

    cursor.execute(
        "CREATE TABLE IF NOT EXISTS stats "
        "(key TEXT PRIMARY KEY, value INTEGER)"
    )

    conn.commit()


def get_config(key, default=""):
    with db_lock:
        cursor.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        )
        res = cursor.fetchone()

    return res[0] if res else default


def set_config(key, value):
    with db_lock:
        cursor.execute(
            "INSERT OR REPLACE INTO config (key, value) "
            "VALUES (?, ?)",
            (key, str(value))
        )
        conn.commit()


def get_stat(key):
    with db_lock:
        cursor.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )
        res = cursor.fetchone()

    return res[0] if res else 0


def inc_stat(key, amount=1):
    with db_lock:
        cursor.execute(
            "SELECT value FROM stats WHERE key=?",
            (key,)
        )

        res = cursor.fetchone()
        current = res[0] if res else 0

        cursor.execute(
            "INSERT OR REPLACE INTO stats (key, value) "
            "VALUES (?, ?)",
            (key, current + amount)
        )

        conn.commit()


def get_all_chats():
    with db_lock:
        cursor.execute(
            "SELECT chat_id, title FROM chats"
        )
        return cursor.fetchall()


def add_db_chat(chat_id, title):
    with db_lock:
        cursor.execute(
            "INSERT OR REPLACE INTO chats "
            "(chat_id, title) VALUES (?, ?)",
            (chat_id, title)
        )
        conn.commit()


def delete_db_chat(chat_id):
    with db_lock:
        cursor.execute(
            "DELETE FROM chats WHERE chat_id=?",
            (chat_id,)
        )

        deleted = cursor.rowcount
        conn.commit()

    return deleted


# =========================================================
# DEFAULTS
# =========================================================

if not get_config("status"):
    set_config("status", "OFF")

if not get_config("delay"):
    set_config("delay", "8")

if not get_config("cycle_delay"):
    set_config("cycle_delay", "15")

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
            channel = channel.split("t.me/")[1]

        channel = (
            channel
            .replace("/", "")
            .replace("@", "")
            .strip()
        )

        me = await app.get_me()

        member = await app.get_chat_member(
            channel,
            me.id
        )

        if member.status in [
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        ]:
            return True

        return False

    except Exception as e:
        # API error ko "not joined" mat samjho
        print(f"[ForceSub API Error] {e}")
        return None


# =========================================================
# AUTO BROADCASTER
# =========================================================

async def auto_broadcast_loop():

    while True:

        await asyncio.sleep(2)

        if get_config("status") != "ON":
            continue

        force_status = await is_force_sub_active()

        # Sirf confirmed non-member hone par OFF karo
        if force_status is False:

            set_config("status", "OFF")

            try:
                await app.send_message(
                    "me",
                    f"❌ **Auto Msg Stopped!**\n"
                    f"Force Sub Channel "
                    f"(@{get_config('force_sub')}) "
                    f"leave kar diya gaya hai."
                )
            except Exception as e:
                print(f"[Notify Error] {e}")

            continue

        # API error hua to auto msg band mat karo
        if force_status is None:
            await asyncio.sleep(10)
            continue

        chats = get_all_chats()

        msg_text = get_config(
            "msg",
            "Default Auto Message"
        )

        try:
            group_gap = max(
                1,
                int(get_config("delay", "8"))
            )

            cycle_minutes = max(
                0,
                int(get_config("cycle_delay", "15"))
            )

        except ValueError:
            group_gap = 8
            cycle_minutes = 15

        if not chats or not msg_text:
            continue

        # =================================================
        # ROUND START
        # =================================================

        for chat_id, title in chats:

            if get_config("status") != "ON":
                break

            try:

                target = (
                    int(chat_id)
                    if chat_id.lstrip("-").isdigit()
                    else chat_id
                )

                await app.send_message(
                    target,
                    msg_text
                )

                inc_stat("total_sent")
                inc_stat("success_sent")

                print(
                    f"[Sent] {title} ({chat_id})"
                )

            except FloodWait as e:

                print(
                    f"[FloodWait] Sleeping {e.value}s"
                )

                await asyncio.sleep(e.value)

            except Exception as e:

                inc_stat("total_sent")
                inc_stat("failed_sent")

                print(
                    f"[Send Error] "
                    f"{chat_id}: {e}"
                )

            # Human-like random delay
            randomized_gap = random.randint(
                max(3, group_gap - 2),
                group_gap + 4
            )

            await asyncio.sleep(
                randomized_gap
            )

        # =================================================
        # ROUND BREAK
        # =================================================

        if get_config("status") == "ON":

            print(
                f"[Cycle Done] "
                f"Resting for {cycle_minutes} minutes..."
            )

            await asyncio.sleep(
                cycle_minutes * 60
            )


# =========================================================
# COMMAND HANDLERS
# =========================================================

# .addchat
# No argument = current chat
# .addchat @username
# .addchat -1001234567890
# .addchat t.me/username

@app.on_message(
    filters.outgoing &
    filters.command("addchat", prefixes=".")
)
async def add_chat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        target = message.chat.id

    else:

        target_str = args[1].strip()

        if "t.me/" in target_str:

            target_str = (
                target_str
                .split("t.me/")[1]
                .replace("/", "")
                .replace("+", "")
            )

        if target_str.lstrip("-").isdigit():

            target = int(target_str)

        else:

            target = (
                target_str
                if target_str.startswith("@")
                else f"@{target_str}"
            )

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

        add_db_chat(
            chat_id,
            title
        )

        await message.edit_text(
            f"✅ **Chat Added!**\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat_id}`"
        )

    except Exception as e:

        await message.edit_text(
            f"❌ **Error:** `{e}`"
        )


# .delchat

@app.on_message(
    filters.outgoing &
    filters.command("delchat", prefixes=".")
)
async def del_chat_handler(
    client,
    message: Message
):

    args = message.text.split(
        maxsplit=1
    )

    chat_id = (
        str(message.chat.id)
        if len(args) < 2
        else args[1].strip()
    )

    deleted = delete_db_chat(
        chat_id
    )

    if deleted:

        await message.edit_text(
            f"🗑 **Chat `{chat_id}` Removed!**"
        )

    else:

        await message.edit_text(
            f"ℹ️ **Chat `{chat_id}` list mein nahi hai.**"
        )


# .listchats

@app.on_message(
    filters.outgoing &
    filters.command("listchats", prefixes=".")
)
async def list_chats_handler(
    client,
    message: Message
):

    chats = get_all_chats()

    if not chats:

        return await message.edit_text(
            "ℹ️ No chats added."
        )

    text = "📋 **Target Chats:**\n\n"

    for idx, (c_id, title) in enumerate(
        chats,
        1
    ):

        line = (
            f"{idx}. **{title}** | `{c_id}`\n"
        )

        if len(text) + len(line) > 4000:

            await message.edit_text(text)

            return

        text += line

    await message.edit_text(text)


# .setmsg

@app.on_message(
    filters.outgoing &
    filters.command("setmsg", prefixes=".")
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
            "❌ `.setmsg <text>`"
        )

    if len(args[1]) > 4096:

        return await message.edit_text(
            "❌ Message 4096 characters se chhota rakho."
        )

    set_config(
        "msg",
        args[1]
    )

    await message.edit_text(
        "✅ **Message Updated!**"
    )


# .setdelay

@app.on_message(
    filters.outgoing &
    filters.command("setdelay", prefixes=".")
)
async def set_delay_handler(
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

        return await message.edit_text(
            "❌ `.setdelay <seconds>`\n"
            "(Groups ke beech ka gap)"
        )

    delay = int(args[1])

    set_config(
        "delay",
        delay
    )

    await message.edit_text(
        f"⏱ **Group Gap set to `{delay}s`** "
        f"(with random variation)"
    )


# .setcycle

@app.on_message(
    filters.outgoing &
    filters.command("setcycle", prefixes=".")
)
async def set_cycle_handler(
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

        return await message.edit_text(
            "❌ `.setcycle <minutes>` "
            "(Round break time)"
        )

    minutes = int(args[1])

    set_config(
        "cycle_delay",
        minutes
    )

    await message.edit_text(
        f"🔄 **Round Break set to "
        f"`{minutes}` minutes.**"
    )


# .forcesub

@app.on_message(
    filters.outgoing &
    filters.command("forcesub", prefixes=".")
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
            "❌ `.forcesub <channel_username>`"
        )

    ch = (
        args[1]
        .replace("@", "")
        .replace("t.me/", "")
        .strip()
    )

    set_config(
        "force_sub",
        ch
    )

    await message.edit_text(
        f"🔒 **Force Sub set to:** `@{ch}`"
    )


# .automsg

@app.on_message(
    filters.outgoing &
    filters.command("automsg", prefixes=".")
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
        or args[1].lower()
        not in ["on", "off"]
    ):

        return await message.edit_text(
            "❌ `.automsg on` ya `.automsg off`"
        )

    state = args[1].upper()

    if state == "ON":

        force_status = (
            await is_force_sub_active()
        )

        if force_status is False:

            return await message.edit_text(
                f"❌ Join "
                f"@{get_config('force_sub')} "
                f"first!"
            )

        if force_status is None:

            return await message.edit_text(
                "⚠️ Force Sub check abhi fail ho rahi hai. "
                "Thodi der baad try karo."
            )

    set_config(
        "status",
        state
    )

    await message.edit_text(
        f"🤖 **Auto Messaging is `{state}`!**"
    )


# .stats

@app.on_message(
    filters.outgoing &
    filters.command("stats", prefixes=".")
)
async def stats_handler(
    client,
    message: Message
):

    stats_text = (
        "📊 **Auto-Bot Anti-Spam Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ **Status:** `{get_config('status')}`\n"
        f"⏱ **Group Gap:** ~`{get_config('delay')}s` "
        f"(Randomized)\n"
        f"🔄 **Round Break:** "
        f"`{get_config('cycle_delay')} mins`\n"
        f"🔒 **Force Sub:** "
        f"`@{get_config('force_sub') or 'None'}`\n"
        f"🎯 **Target Chats:** "
        f"`{len(get_all_chats())} Groups`\n\n"
        "📈 **Sending History:**\n"
        f"├ 🚀 **Total Attempts:** "
        f"`{get_stat('total_sent')}`\n"
        f"├ ✅ **Successfully Sent:** "
        f"`{get_stat('success_sent')}`\n"
        f"└ ❌ **Failed:** "
        f"`{get_stat('failed_sent')}`"
    )

    await message.edit_text(
        stats_text
    )


# =========================================================
# RUNNER
# =========================================================

async def main():

    await app.start()

    print(
        "Userbot started with "
        "Anti-Spam Protection!"
    )

    asyncio.create_task(
        auto_broadcast_loop()
    )

    await asyncio.Event().wait()


if __name__ == "__main__":

    loop = asyncio.get_event_loop()

    loop.run_until_complete(
        main()
    )
