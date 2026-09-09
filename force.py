from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import Message

from aiogram import F
from aiogram.enums import ChatType
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from main import (
    app,
    dp,
    tg_bot,
    get_config,
    set_config,
    log,
)

# =========================================================
# HELPERS
# =========================================================

def parse_id(value):
    value = str(value).strip()

    if value.startswith("100") and len(value) >= 12:
        value = "-" + value

    if value.lstrip("-").isdigit():
        return int(value)

    return value


def normalize_target(value):
    value = str(value).strip()

    if "t.me/" in value:
        value = value.split("t.me/", 1)[1]

    value = value.split("/", 1)[0]
    value = value.replace("+", "").strip()

    return parse_id(value)


# =========================================================
# FORCE SUB DATA
# =========================================================

async def get_force_channels():
    return await get_config("force_sub", [])


async def set_force_channels(channels):
    await set_config("force_sub", channels)


# =========================================================
# SUBSCRIPTION CHECK
# =========================================================

async def is_subscribed(client, chat_id, user_id):
    try:
        target = parse_id(chat_id)

        member = await client.get_chat_member(
            target,
            user_id
        )

        status = str(member.status).lower()

        return status in {
            "owner",
            "administrator",
            "creator",
            "member",
        }

    except Exception as e:
        log.error(
            f"ForceSub check failed "
            f"| chat={chat_id} "
            f"| user={user_id} "
            f"| {e}"
        )

        return False


# =========================================================
# KEYBOARD
# =========================================================

async def get_force_keyboard():
    channels = await get_force_channels()

    rows = []

    for i, channel in enumerate(channels, 1):

        if isinstance(channel, dict):
            title = channel.get(
                "title",
                f"Channel {i}"
            )

            link = channel.get(
                "link",
                "https://t.me"
            )

        else:
            title = f"Channel {i}"
            link = "https://t.me"

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 Join {title}",
                    url=link
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Check Subscription",
                callback_data="check_sub"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# =========================================================
# ENV FORCE SUB
# =========================================================

async def load_force_sub_from_env(raw):
    saved = []

    values = [
        x.strip()
        for x in raw.replace(" ", "").split(",")
        if x.strip()
    ]

    for item in values[:3]:
        try:
            chat = await app.get_chat(
                normalize_target(item)
            )

            if chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                link = (
                    chat.invite_link
                    or "https://t.me"
                )

            saved.append(
                {
                    "id": chat.id,
                    "link": link,
                    "title": chat.title or str(chat.id),
                }
            )

        except Exception as e:
            log.error(
                f"FORCE_SUB load error [{item}]: {e}"
            )

    if saved:
        await set_force_channels(saved)


# =========================================================
# .forcesub
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("forcesub", prefixes=".")
)
async def forcesub_command(
    client,
    message: Message
):
    args = (message.text or "").split()

    if len(args) < 2:
        return await message.edit_text(
            "❌ **Usage:**\n"
            "`.forcesub -100123456789`\n\n"
            "Maximum 3 channels."
        )

    saved = []

    for item in args[1:4]:
        try:
            chat = await client.get_chat(
                normalize_target(item)
            )

            if chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                link = (
                    chat.invite_link
                    or "https://t.me"
                )

            saved.append(
                {
                    "id": chat.id,
                    "link": link,
                    "title": chat.title or str(chat.id),
                }
            )

        except Exception as e:
            log.error(
                f"Forcesub add error [{item}]: {e}"
            )

    await set_force_channels(saved)

    await message.edit_text(
        "✅ **ForceSub Set**\n\n"
        f"📢 Saved Channels: `{len(saved)}`"
    )


# =========================================================
# .forcesuboff
# =========================================================

@app.on_message(
    filters.outgoing &
    filters.command("forcesuboff", prefixes=".")
)
async def forcesuboff_command(
    client,
    message: Message
):
    await set_force_channels([])

    await message.edit_text(
        "🔓 **ForceSub Disabled.**"
    )


# =========================================================
# USERBOT PM FORCE SUB
# =========================================================

@app.on_message(
    filters.private &
    ~filters.me &
    ~filters.bot
)
async def userbot_pm_guard(
    client,
    message: Message
):
    channels = await get_force_channels()

    if not channels:
        return

    if not message.from_user:
        return

    for channel in channels:

        chat_id = (
            channel.get("id")
            if isinstance(channel, dict)
            else channel
        )

        ok = await is_subscribed(
            client,
            chat_id,
            message.from_user.id
        )

        if not ok:

            text = (
                "🔒 **Mujhe PM me message karne ke liye "
                "pehle channels join karo:**\n\n"
            )

            for i, ch in enumerate(
                channels,
                1
            ):

                if isinstance(ch, dict):
                    title = ch.get(
                        "title",
                        f"Channel {i}"
                    )

                    link = ch.get(
                        "link",
                        "https://t.me"
                    )

                else:
                    title = f"Channel {i}"
                    link = "https://t.me"

                text += (
                    f"{i}. [{title}]({link})\n"
                )

            await message.reply_text(
                text,
                disable_web_page_preview=True
            )

            return


# =========================================================
# BOT API FORCE SUB
# =========================================================

if tg_bot:

    @dp.message(
        F.chat.type == ChatType.PRIVATE
    )
    async def bot_pm_guard(message):

        if not message.from_user:
            return

        channels = await get_force_channels()

        if not channels:
            return

        for channel in channels:

            chat_id = (
                channel.get("id")
                if isinstance(channel, dict)
                else channel
            )

            ok = await is_subscribed(
                tg_bot,
                chat_id,
                message.from_user.id
            )

            if not ok:

                await message.answer(
                    "🔒 **Pehle niche diye gaye "
                    "channels join karo:**",
                    reply_markup=await get_force_keyboard()
                )

                return


    @dp.callback_query(
        F.data == "check_sub"
    )
    async def check_sub_callback(
        callback: CallbackQuery
    ):
        channels = await get_force_channels()

        for channel in channels:

            chat_id = (
                channel.get("id")
                if isinstance(channel, dict)
                else channel
            )

            ok = await is_subscribed(
                tg_bot,
                chat_id,
                callback.from_user.id
            )

            if not ok:

                await callback.answer(
                    "❌ Pehle sabhi channels join karo!",
                    show_alert=True
                )

                return

        await callback.answer(
            "✅ Subscription Verified!"
        )

        try:
            await callback.message.edit_text(
                "✅ **Subscription Verified!**"
            )
        except Exception:
            pass
