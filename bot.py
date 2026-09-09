```python
import asyncio
import time

from pyrogram import filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from aiogram.filters import Command, CommandStart

from main import (
    app,
    dp,
    get_config,
    set_config,
    chats_col,
    log,
)

import force

# =========================================================
# CHAT HELPERS
# =========================================================

async def get_chats():
    return await chats_col.find().to_list(1000)


# =========================================================
# .help
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("help", prefixes=".")
)
async def help_command(
    client,
    message: Message
):
    await message.edit_text(
        "🤖 **Session Commands**\n\n"
        "`.help`\n"
        "`.forcesub -100xxx -100yyy`\n"
        "`.forcesuboff`\n"
        "`.addchat`\n"
        "`.addchat 30`\n"
        "`.addchat @group 60`\n"
        "`.delchat`\n"
        "`.delchat -100xxx`\n"
        "`.listchats`\n"
        "`.setchatdelay -100xxx 30`\n"
        "`.setmsg text`\n"
        "`.automsg on`\n"
        "`.automsg off`\n"
        "`.status`"
    )


# =========================================================
# .addchat
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("addchat", prefixes=".")
)
async def addchat_command(
    client,
    message: Message
):
    args = (message.text or "").split()

    interval = 60

    if len(args) == 1:

        target = message.chat.id

    elif len(args) == 2:

        if args[1].isdigit():
            target = message.chat.id
            interval = int(args[1])

        else:
            target = force.normalize_target(
                args[1]
            )

    else:

        target = force.normalize_target(
            args[1]
        )

        if args[2].isdigit():
            interval = int(args[2])

    try:

        chat = await client.get_chat(target)

        title = (
            chat.title
            or chat.first_name
            or "Unknown"
        )

        await chats_col.update_one(
            {"_id": str(chat.id)},
            {
                "$set": {
                    "chat_id": str(chat.id),
                    "title": title,
                    "interval_sec": interval,
                    "last_sent": 0,
                }
            },
            upsert=True
        )

        await message.edit_text(
            "✅ **Chat Added!**\n\n"
            f"📌 **Title:** `{title}`\n"
            f"🆔 **ID:** `{chat.id}`\n"
            f"⏱ **Interval:** `{interval}` seconds"
        )

    except Exception as e:

        await message.edit_text(
            f"❌ **Error adding chat:**\n`{e}`"
        )


# =========================================================
# .delchat
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("delchat", prefixes=".")
)
async def delchat_command(
    client,
    message: Message
):
    args = (message.text or "").split(
        maxsplit=1
    )

    if len(args) < 2:
        chat_id = str(message.chat.id)
    else:
        chat_id = args[1].strip()

    result = await chats_col.delete_one(
        {"_id": chat_id}
    )

    if result.deleted_count:
        await message.edit_text(
            f"🗑 **Chat Removed:** `{chat_id}`"
        )
    else:
        await message.edit_text(
            f"ℹ️ Chat `{chat_id}` not found."
        )


# =========================================================
# .listchats
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("listchats", prefixes=".")
)
async def listchats_command(
    client,
    message: Message
):
    chats = await get_chats()

    if not chats:
        return await message.edit_text(
            "ℹ️ **No chats added.**"
        )

    text = "📋 **Target Chats**\n\n"

    for i, chat in enumerate(
        chats,
        1
    ):

        text += (
            f"{i}. **{chat.get('title', 'Chat')}**\n"
            f"🆔 `{chat.get('chat_id')}`\n"
            f"⏱ `{chat.get('interval_sec', 60)}s`\n\n"
        )

    await message.edit_text(text)


# =========================================================
# .setchatdelay
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("setchatdelay", prefixes=".")
)
async def setchatdelay_command(
    client,
    message: Message
):
    args = (message.text or "").split(
        maxsplit=2
    )

    if (
        len(args) < 3
        or not args[2].isdigit()
    ):
        return await message.edit_text(
            "❌ **Usage:**\n"
            "`.setchatdelay <chat_id> <seconds>`"
        )

    chat_id = args[1].strip()
    seconds = int(args[2])

    result = await chats_col.update_one(
        {"_id": chat_id},
        {
            "$set": {
                "interval_sec": seconds
            }
        }
    )

    if result.matched_count == 0:
        return await message.edit_text(
            "❌ **Chat not found.**"
        )

    await message.edit_text(
        f"✅ **Delay Updated**\n\n"
        f"🆔 `{chat_id}`\n"
        f"⏱ `{seconds}` seconds"
    )


# =========================================================
# .setmsg
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("setmsg", prefixes=".")
)
async def setmsg_command(
    client,
    message: Message
):
    args = (message.text or "").split(
        maxsplit=1
    )

    if len(args) < 2:
        return await message.edit_text(
            "❌ **Usage:** `.setmsg <text>`"
        )

    await set_config(
        "msg",
        args[1]
    )

    await message.edit_text(
        "✅ **Message Saved.**"
    )


# =========================================================
# .automsg
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("automsg", prefixes=".")
)
async def automsg_command(
    client,
    message: Message
):
    args = (message.text or "").split(
        maxsplit=1
    )

    if (
        len(args) < 2
        or args[1].lower() not in {
            "on",
            "off"
        }
    ):
        return await message.edit_text(
            "❌ **Usage:**\n"
            "`.automsg on`\n"
            "`.automsg off`"
        )

    state = args[1].upper()

    if state == "ON":

        channels = await force.get_force_channels()

        if channels:

            me = await client.get_me()

            for channel in channels:

                chat_id = (
                    channel.get("id")
                    if isinstance(channel, dict)
                    else channel
                )

                ok = await force.is_subscribed(
                    client,
                    chat_id,
                    me.id
                )

                if not ok:
                    return await message.edit_text(
                        "❌ **ForceSub check failed.**\n"
                        "Pehle required channel(s) join karo."
                    )

    await set_config(
        "status",
        state
    )

    await message.edit_text(
        f"🤖 **Auto Messaging `{state}`!**"
    )


# =========================================================
# .status
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("status", prefixes=".")
)
async def status_command(
    client,
    message: Message
):
    chats = await get_chats()
    channels = await force.get_force_channels()

    await message.edit_text(
        "📊 **Status**\n\n"
        f"⚡ **Auto:** `{await get_config('status', 'OFF')}`\n"
        f"🔒 **ForceSub:** `{len(channels)} channel(s)`\n"
        f"🎯 **Chats:** `{len(chats)}`\n"
        f"📝 **Message:** `{await get_config('msg', '')}`"
    )


# =========================================================
# AUTO BROADCAST WORKER
# =========================================================

async def broadcast_worker():

    while True:

        try:

            await asyncio.sleep(2)

            if (
                await get_config(
                    "status",
                    "OFF"
                ) != "ON"
            ):
                continue

            # -------------------------
            # FORCE SUB CHECK
            # -------------------------

            channels = (
                await force.get_force_channels()
            )

            if channels:

                me = await app.get_me()

                force_failed = False

                for channel in channels:

                    chat_id = (
                        channel.get("id")
                        if isinstance(channel, dict)
                        else channel
                    )

                    if not await force.is_subscribed(
                        app,
                        chat_id,
                        me.id
                    ):
                        force_failed = True
                        break

                if force_failed:

                    await set_config(
                        "status",
                        "OFF"
                    )

                    try:
                        await app.send_message(
                            "me",
                            "❌ **Auto Msg Stopped!**\n"
                            "ForceSub channel membership "
                            "check failed."
                        )
                    except Exception:
                        pass

                    continue

            # -------------------------
            # MESSAGE
            # -------------------------

            msg_text = await get_config(
                "msg",
                ""
            )

            if not msg_text:
                continue

            chats = await get_chats()

            now = time.time()

            # -------------------------
            # SEND LOOP
            # -------------------------

            for chat in chats:

                if (
                    await get_config(
                        "status",
                        "OFF"
                    ) != "ON"
                ):
                    break

                chat_id = chat.get(
                    "chat_id"
                )

                interval = int(
                    chat.get(
                        "interval_sec",
                        60
                    )
                )

                last_sent = float(
                    chat.get(
                        "last_sent",
                        0
                    ) or 0
                )

                if (
                    now - last_sent
                    < interval
                ):
                    continue

                try:

                    if str(chat_id).lstrip("-").isdigit():
                        target = int(chat_id)
                    else:
                        target = chat_id

                    await app.send_message(
                        target,
                        msg_text
                    )

                    await chats_col.update_one(
                        {"_id": chat["_id"]},
                        {
                            "$set": {
                                "last_sent": time.time()
                            }
                        }
                    )

                    await asyncio.sleep(2)

                except FloodWait as e:

                    log.warning(
                        f"FloodWait: {e.value}s"
                    )

                    await asyncio.sleep(
                        e.value
                    )

                except Exception as e:

                    log.error(
                        f"[Send Error] "
                        f"{chat_id}: {e}"
                    )

        except Exception as e:

            log.error(
                f"[Broadcast Error] {e}"
            )

            await asyncio.sleep(5)


# =========================================================
# TELEGRAM BOT API COMMANDS
# =========================================================

if True:

    @dp.message(CommandStart())
    async def bot_start(message):
        await message.answer(
            "🚀 **Bot Active Hai!**"
        )


    @dp.message(Command("help"))
    async def bot_help(message):
        await message.answer(
            "🤖 **Bot Commands**\n\n"
            "/start\n"
            "/help\n"
            "/status"
        )


    @dp.message(Command("status"))
    async def bot_status(message):

        chats = await get_chats()
        channels = await force.get_force_channels()

        await message.answer(
            f"⚡ **Status:** "
            f"`{await get_config('status', 'OFF')}`\n"
            f"🔒 **ForceSub:** "
            f"`{len(channels)}`\n"
            f"🎯 **Chats:** "
            f"`{len(chats)}`"
        )
```
