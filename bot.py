import asyncio
import logging
from hydrogram import filters, Client
from hydrogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Map to store forwarded messages for replying: {forwarded_msg_id: original_sender_id}
FORWARD_MAP = {}

def register_handlers(userbot: Client, bot: Client, db, fs_mgr):

    # ==========================================
    # 1. USERBOT COMMANDS (Admin Controls)
    # ==========================================

    # --- Channel Management Commands ---
    @userbot.on_message(filters.command("forcesub", prefixes=".") & filters.outgoing)
    async def add_fs_cmd(client: Client, message: Message):
        if len(message.command) < 2:
            return await message.reply_text("❌ **Usage:** `.forcesub <channel_id or @username>`")

        ch = message.command[1]
        try:
            chat = await client.get_chat(ch)
            ch_identifier = str(chat.id)
            updated = await fs_mgr.add_forcesub(ch_identifier)
            await message.reply_text(f"✅ **Added to ForceSub:** {chat.title} (`{ch_identifier}`)\n\n**Total Channels:** {len(updated)}")
        except Exception as e:
            await message.reply_text(f"❌ **Error:** {e}\nMake sure Userbot & Bot are Admins in the channel.")

    @userbot.on_message(filters.command("delforcesub", prefixes=".") & filters.outgoing)
    async def del_fs_cmd(client: Client, message: Message):
        if len(message.command) < 2:
            return await message.reply_text("❌ **Usage:** `.delforcesub <channel_id or @username>`")

        ch = message.command[1]
        updated = await fs_mgr.remove_forcesub(ch)
        await message.reply_text(f"🗑️ **Removed from ForceSub.**\n\n**Remaining Channels:** {len(updated)}")

    @userbot.on_message(filters.command("forcesublist", prefixes=".") & filters.outgoing)
    async def list_fs_cmd(client: Client, message: Message):
        channels = await fs_mgr.get_forcesubs()
        if not channels:
            return await message.reply_text("ℹ️ No active ForceSub channels.")

        text = "📋 **Active ForceSub Channels:**\n\n"
        for idx, ch in enumerate(channels, start=1):
            text += f"{idx}. `{ch}`\n"
        await message.reply_text(text)

    @userbot.on_message(filters.command("forcesuboff", prefixes=".") & filters.outgoing)
    async def clear_fs_cmd(client: Client, message: Message):
        await fs_mgr.clear_forcesubs()
        await message.reply_text("🚫 **All ForceSub channels disabled!**")

    # --- Custom Text & Buttons Commands ---
    @userbot.on_message(filters.command("setfsubmsg", prefixes=".") & filters.outgoing)
    async def set_fsub_msg_cmd(client: Client, message: Message):
        if len(message.command) < 2:
            return await message.reply_text("❌ **Usage:** `.setfsubmsg <New Message Text>`")

        new_text = message.text.split(maxsplit=1)[1]
        await fs_mgr.set_forcesub_text(new_text)
        await message.reply_text("✅ **ForceSub Message Updated!**")

    @userbot.on_message(filters.command("addbutton", prefixes=".") & filters.outgoing)
    async def add_btn_cmd(client: Client, message: Message):
        # Format: .addbutton Label | https://link.com
        if "|" not in message.text:
            return await message.reply_text("❌ **Usage:** `.addbutton Button Label | https://example.com`")

        try:
            raw_data = message.text.split(maxsplit=1)[1]
            label, url = map(str.strip, raw_data.split("|", 1))
            updated = await fs_mgr.add_custom_button(label, url)
            await message.reply_text(f"✅ **Button Added:** [{label}]({url})\nTotal Extra Buttons: {len(updated)}")
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")

    @userbot.on_message(filters.command("delbutton", prefixes=".") & filters.outgoing)
    async def del_btn_cmd(client: Client, message: Message):
        if len(message.command) < 2 or not message.command[1].isdigit():
            return await message.reply_text("❌ **Usage:** `.delbutton <button_number>` (e.g. `.delbutton 1`)")

        idx = int(message.command[1]) - 1
        updated = await fs_mgr.remove_custom_button(idx)
        await message.reply_text(f"🗑️ **Button Removed.** Remaining Custom Buttons: {len(updated)}")

    @userbot.on_message(filters.command("listbuttons", prefixes=".") & filters.outgoing)
    async def list_btns_cmd(client: Client, message: Message):
        btns = await fs_mgr.get_custom_buttons()
        if not btns:
            return await message.reply_text("ℹ️ No custom buttons set.")

        text = "🔘 **Custom Extra Buttons:**\n\n"
        for idx, b in enumerate(btns, start=1):
            text += f"{idx}. [{b['label']}]({b['url']})\n"
        await message.reply_text(text)

    @userbot.on_message(filters.command("clearbuttons", prefixes=".") & filters.outgoing)
    async def clear_btns_cmd(client: Client, message: Message):
        await fs_mgr.clear_custom_buttons()
        await message.reply_text("🚫 **All custom extra buttons removed!**")


    # ==========================================
    # 2. BOT API HANDLERS (PM + Group + Forward)
    # ==========================================

    # --- Verification Callback Button ---
    @bot.on_callback_query(filters.regex("^check_forcesub$"))
    async def check_fs_callback(client: Client, callback: CallbackQuery):
        user_id = callback.from_user.id
        unjoined = await fs_mgr.get_unjoined_channels(client, user_id)

        if not unjoined:
            await callback.answer("✅ Aapne sabhi channels join kar liye hain!", show_alert=True)
            try:
                await callback.message.delete()
            except Exception:
                pass
        else:
            await callback.answer("❌ Aapne abhi tak saare channels join nahi kiye hain!", show_alert=True)

    # --- Group ForceSub Guard ---
    @bot.on_message(filters.group & ~filters.me)
    async def group_forcesub_guard(client: Client, message: Message):
        if not message.from_user:
            return

        user_id = message.from_user.id
        unjoined = await fs_mgr.get_unjoined_channels(client, user_id)

        if unjoined:
            try:
                # Delete user message
                await message.delete()

                # Send temporary warning
                msg_text = await fs_mgr.get_forcesub_text()
                reply_markup = await fs_mgr.build_forcesub_markup(client, unjoined)

                warn_msg = await message.reply_text(
                    f"⚠️ {message.from_user.mention}\n\n{msg_text}",
                    reply_markup=reply_markup
                )

                # Auto-delete warning after 15s to keep group clean
                await asyncio.sleep(15)
                await warn_msg.delete()
            except Exception as e:
                logger.error(f"Group ForceSub error: {e}")

    # --- PM ForceSub Guard & Owner PM Forwarder ---
    @bot.on_message(filters.private & ~filters.me)
    async def pm_forcesub_and_forwarder(client: Client, message: Message):
        user_id = message.from_user.id
        owner_id = userbot.me.id  # Automatically targets your personal account ID

        # 1. Ignore messages sent by Owner
        if user_id == owner_id:
            return

        # 2. Check ForceSub
        unjoined = await fs_mgr.get_unjoined_channels(client, user_id)
        if unjoined:
            msg_text = await fs_mgr.get_forcesub_text()
            reply_markup = await fs_mgr.build_forcesub_markup(client, unjoined)
            await message.reply_text(msg_text, reply_markup=reply_markup)
            return

        # 3. ForceSub Passed -> Forward PM to Owner
        try:
            fwd = await message.forward(owner_id)
            FORWARD_MAP[fwd.id] = user_id
            
            # Send notification header to owner
            await bot.send_message(
                owner_id,
                f"📩 **New PM Message from:** {message.from_user.mention} (`{user_id}`)\n"
                f"💡 *Reply to the forwarded message above to send a response back.*"
            )
        except Exception as e:
            logger.error(f"Failed to forward PM to Owner ({owner_id}): {e}")

    # --- Reply Back System (Owner replies to user from PM) ---
    @userbot.on_message(filters.private & filters.outgoing & filters.reply)
    async def reply_back_to_user(client: Client, message: Message):
        reply_to_id = message.reply_to_message_id
        if reply_to_id in FORWARD_MAP:
            target_user_id = FORWARD_MAP[reply_to_id]
            try:
                await bot.send_message(target_user_id, message.text)
                await message.reply_text(f"✅ **Reply sent to user** (`{target_user_id}`)")
            except Exception as e:
                await message.reply_text(f"❌ Failed to send reply: {e}")
