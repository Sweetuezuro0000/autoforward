import logging
from hydrogram import Client, filters
from hydrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorDatabase
from force import ForceSubManager

logger = logging.getLogger(__name__)

def register_handlers(userbot: Client, bot: Client, db: AsyncIOMotorDatabase, fs_mgr: ForceSubManager):
    config_col = db["config"]
    chats_col = db["chats"]

    # ================= USERBOT COMMANDS (filters.outgoing) =================

    @userbot.on_message(filters.outgoing & filters.command("help", prefixes="."))
    async def cmd_userbot_help(client: Client, message: Message):
        text = (
            "🤖 **Userbot Command Panel**\n\n"
            "• `.help` - Show this menu\n"
            "• `.forcesub <channel_id/username>` - Set ForceSub channel\n"
            "• `.forcesuboff` - Disable ForceSub\n"
            "• `.addchat <chat_id> [delay]` - Add chat to auto broadcast\n"
            "• `.delchat <chat_id>` - Remove chat from auto broadcast\n"
            "• `.listchats` - List all broadcast chats\n"
            "• `.setchatdelay <chat_id> <seconds>` - Update per-chat delay\n"
            "• `.setmsg <message>` - Set auto broadcast message\n"
            "• `.automsg <on/off>` - Enable or Disable auto broadcast\n"
            "• `.status` - View current configuration & status"
        )
        await message.edit_text(text)

    @userbot.on_message(filters.outgoing & filters.command("forcesub", prefixes="."))
    async def cmd_set_forcesub(client: Client, message: Message):
        if len(message.command) < 2:
            await message.edit_text("❌ **Usage:** `.forcesub <channel_id or @username>`")
            return
        target = message.command[1]
        try:
            target = int(target)
        except ValueError:
            pass

        try:
            chat = await client.get_chat(target)
            target_id = chat.id
        except Exception as e:
            await message.edit_text(f"❌ **Invalid Channel/Peer ID:** `{e}`")
            return

        await fs_mgr.set_forcesub(target_id)
        await message.edit_text(f"✅ **ForceSub set to:** `{target_id}`")

    @userbot.on_message(filters.outgoing & filters.command("forcesuboff", prefixes="."))
    async def cmd_forcesuboff(client: Client, message: Message):
        await fs_mgr.remove_forcesub()
        await message.edit_text("✅ **ForceSub has been disabled.**")

    @userbot.on_message(filters.outgoing & filters.command("addchat", prefixes="."))
    async def cmd_addchat(client: Client, message: Message):
        if len(message.command) < 2:
            await message.edit_text("❌ **Usage:** `.addchat <chat_id> [delay_in_seconds]`")
            return

        try:
            chat_id = int(message.command[1])
        except ValueError:
            await message.edit_text("❌ **Chat ID must be an integer!**")
            return

        delay = 60
        if len(message.command) >= 3:
            try:
                delay = int(message.command[2])
            except ValueError:
                pass

        await chats_col.update_one(
            {"_id": chat_id},
            {"$set": {"delay": delay, "last_sent": 0}},
            upsert=True
        )
        await message.edit_text(f"✅ **Chat added:** `{chat_id}` with delay `{delay}s`")

    @userbot.on_message(filters.outgoing & filters.command("delchat", prefixes="."))
    async def cmd_delchat(client: Client, message: Message):
        if len(message.command) < 2:
            await message.edit_text("❌ **Usage:** `.delchat <chat_id>`")
            return

        try:
            chat_id = int(message.command[1])
        except ValueError:
            await message.edit_text("❌ **Chat ID must be an integer!**")
            return

        res = await chats_col.delete_one({"_id": chat_id})
        if res.deleted_count > 0:
            await message.edit_text(f"✅ **Chat removed:** `{chat_id}`")
        else:
            await message.edit_text(f"⚠️ **Chat not found:** `{chat_id}`")

    @userbot.on_message(filters.outgoing & filters.command("listchats", prefixes="."))
    async def cmd_listchats(client: Client, message: Message):
        cursor = chats_col.find({})
        chats = await cursor.to_list(length=500)
        if not chats:
            await message.edit_text("ℹ️ **No target chats configured.**")
            return

        text = "📋 **Target Broadcast Chats:**\n\n"
        for idx, item in enumerate(chats, 1):
            text += f"{idx}. `{item['_id']}` | Delay: `{item.get('delay', 60)}s`\n"
        await message.edit_text(text)

    @userbot.on_message(filters.outgoing & filters.command("setchatdelay", prefixes="."))
    async def cmd_setchatdelay(client: Client, message: Message):
        if len(message.command) < 3:
            await message.edit_text("❌ **Usage:** `.setchatdelay <chat_id> <seconds>`")
            return

        try:
            chat_id = int(message.command[1])
            delay = int(message.command[2])
        except ValueError:
            await message.edit_text("❌ **Chat ID and delay must be integers!**")
            return

        res = await chats_col.update_one(
            {"_id": chat_id},
            {"$set": {"delay": delay}}
        )
        if res.matched_count > 0:
            await message.edit_text(f"✅ **Updated delay for** `{chat_id}` **to** `{delay}s`")
        else:
            await message.edit_text(f"⚠️ **Chat** `{chat_id}` **not found in database.**")

    @userbot.on_message(filters.outgoing & filters.command("setmsg", prefixes="."))
    async def cmd_setmsg(client: Client, message: Message):
        if len(message.command) < 2:
            await message.edit_text("❌ **Usage:** `.setmsg <your_broadcast_text>`")
            return

        new_msg = message.text.split(maxsplit=1)[1]
        await config_col.update_one(
            {"_id": "global"},
            {"$set": {"automsg_text": new_msg}},
            upsert=True
        )
        await message.edit_text(f"✅ **Auto Broadcast Message updated:**\n\n{new_msg}")

    @userbot.on_message(filters.outgoing & filters.command("automsg", prefixes="."))
    async def cmd_automsg(client: Client, message: Message):
        if len(message.command) < 2:
            await message.edit_text("❌ **Usage:** `.automsg <on/off>`")
            return

        state = message.command[1].lower()
        if state not in ["on", "off"]:
            await message.edit_text("❌ **Choose either `on` or `off`.**")
            return

        is_on = (state == "on")
        await config_col.update_one(
            {"_id": "global"},
            {"$set": {"automsg_enabled": is_on}},
            upsert=True
        )
        status_text = "ENABLED 🚀" if is_on else "DISABLED 🛑"
        await message.edit_text(f"🤖 **Auto Broadcast status:** `{status_text}`")

    @userbot.on_message(filters.outgoing & filters.command("status", prefixes="."))
    async def cmd_status(client: Client, message: Message):
        cfg = await config_col.find_one({"_id": "global"}) or {}
        fs = await fs_mgr.get_forcesub()
        chat_count = await chats_col.count_documents({})

        enabled = cfg.get("automsg_enabled", False)
        automsg_text = cfg.get("automsg_text", "Not Set")

        text = (
            "📊 **System Status & Configuration**\n\n"
            f"• **Auto Broadcast:** `{'ON' if enabled else 'OFF'}`\n"
            f"• **ForceSub Channel:** `{fs if fs else 'Disabled'}`\n"
            f"• **Target Chats Count:** `{chat_count}`\n"
            f"• **Broadcast Text:**\n`{automsg_text}`"
        )
        await message.edit_text(text)

    # ================= PM GUARD FOR USERBOT =================

    @userbot.on_message(filters.private & ~filters.me & ~filters.bot)
    async def userbot_pm_guard(client: Client, message: Message):
        await fs_mgr.handle_pm_guard(client, message)

    # ================= BOT API HANDLERS =================

    @bot.on_message(filters.command("start"))
    async def bot_start(client: Client, message: Message):
        user_id = message.from_user.id
        fs = await fs_mgr.get_forcesub()
        if fs:
            is_sub = await fs_mgr.check_user_subscribed(client, fs, user_id)
            if not is_sub:
                await fs_mgr.handle_pm_guard(client, message)
                return

        await message.reply_text(
            "👋 **Hello!**\n"
            "This Telegram Bot API instance is active and synced with the Userbot.\n\n"
            "Use `/help` to view available commands."
        )

    @bot.on_message(filters.command("help"))
    async def bot_help(client: Client, message: Message):
        await message.reply_text(
            "ℹ️ **Bot API Help Menu**\n\n"
            "• `/start` - Start the bot & verify subscription\n"
            "• `/help` - Show this message\n"
            "• `/status` - Check current broadcast status"
        )

    @bot.on_message(filters.command("status"))
    async def bot_status(client: Client, message: Message):
        cfg = await config_col.find_one({"_id": "global"}) or {}
        chat_count = await chats_col.count_documents({})
        enabled = cfg.get("automsg_enabled", False)
        fs = await fs_mgr.get_forcesub()

        await message.reply_text(
            "📊 **Bot Status**\n\n"
            f"• **Broadcast Active:** `{'YES' if enabled else 'NO'}`\n"
            f"• **Target Chats:** `{chat_count}`\n"
            f"• **ForceSub:** `{fs if fs else 'Disabled'}`"
        )

    # Catch-all PM handler that DOES NOT block commands
    @bot.on_message(filters.private & ~filters.command(["start", "help", "status"]))
    async def bot_pm_catchall(client: Client, message: Message):
        is_ok = await fs_mgr.handle_pm_guard(client, message)
        if is_ok:
            await message.reply_text("📥 Message received. Please use commands to interact.")
