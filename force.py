import logging
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from hydrogram.errors import UserNotParticipant, RPCError

logger = logging.getLogger(__name__)

DEFAULT_FSUB_TEXT = (
    "⚠️ **Access Restricted!**\n\n"
    "Is Bot / Group ko use karne ke liye aapko hamare sabhi official channels join karne honge.\n"
    "Niche diye gaye buttons par click karke join karein aur **Verify / Check** button dabayein."
)

class ForceSubManager:
    def __init__(self, db):
        self.config_col = db["config"]

    # --- Channel Management ---
    async def get_forcesubs(self) -> list:
        cfg = await self.config_col.find_one({"_id": "global"}) or {}
        return cfg.get("forcesub_channels", [])

    async def add_forcesub(self, channel_id_or_username: str) -> list:
        channels = await self.get_forcesubs()
        if channel_id_or_username not in channels:
            channels.append(channel_id_or_username)
            await self.config_col.update_one(
                {"_id": "global"},
                {"$set": {"forcesub_channels": channels}},
                upsert=True
            )
        return channels

    async def remove_forcesub(self, channel_id_or_username: str) -> list:
        channels = await self.get_forcesubs()
        if channel_id_or_username in channels:
            channels.remove(channel_id_or_username)
            await self.config_col.update_one(
                {"_id": "global"},
                {"$set": {"forcesub_channels": channels}},
                upsert=True
            )
        return channels

    async def clear_forcesubs(self):
        await self.config_col.update_one(
            {"_id": "global"},
            {"$set": {"forcesub_channels": []}},
            upsert=True
        )

    # --- Custom Text & Buttons Management ---
    async def get_forcesub_text(self) -> str:
        cfg = await self.config_col.find_one({"_id": "global"}) or {}
        return cfg.get("forcesub_text", DEFAULT_FSUB_TEXT)

    async def set_forcesub_text(self, text: str):
        await self.config_col.update_one(
            {"_id": "global"},
            {"$set": {"forcesub_text": text}},
            upsert=True
        )

    async def get_custom_buttons(self) -> list:
        cfg = await self.config_col.find_one({"_id": "global"}) or {}
        return cfg.get("custom_buttons", [])

    async def add_custom_button(self, label: str, url: str) -> list:
        buttons = await self.get_custom_buttons()
        buttons.append({"label": label, "url": url})
        await self.config_col.update_one(
            {"_id": "global"},
            {"$set": {"custom_buttons": buttons}},
            upsert=True
        )
        return buttons

    async def remove_custom_button(self, index: int) -> list:
        buttons = await self.get_custom_buttons()
        if 0 <= index < len(buttons):
            buttons.pop(index)
            await self.config_col.update_one(
                {"_id": "global"},
                {"$set": {"custom_buttons": buttons}},
                upsert=True
            )
        return buttons

    async def clear_custom_buttons(self):
        await self.config_col.update_one(
            {"_id": "global"},
            {"$set": {"custom_buttons": []}},
            upsert=True
        )

    # --- Verification & Markup Generator ---
    async def get_unjoined_channels(self, client, user_id: int) -> list:
        channels = await self.get_forcesubs()
        unjoined = []

        for ch in channels:
            try:
                target = int(ch) if (ch.startswith("-100") or ch.isdigit()) else ch
                member = await client.get_chat_member(target, user_id)
                if member.status in ["kicked", "left"]:
                    unjoined.append(ch)
            except UserNotParticipant:
                unjoined.append(ch)
            except Exception as e:
                logger.error(f"Error checking ForceSub for {ch}: {e}")
                unjoined.append(ch)

        return unjoined

    async def build_forcesub_markup(self, client, unjoined_channels: list) -> InlineKeyboardMarkup:
        keyboard = []
        
        # 1. Dynamic Unjoined Channel Links
        for idx, ch in enumerate(unjoined_channels, start=1):
            try:
                chat = await client.get_chat(ch)
                invite_link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)
                if not invite_link:
                    invite_link = await client.export_chat_invite_link(chat.id)

                keyboard.append([InlineKeyboardButton(f"📢 Join Channel #{idx} ({chat.title[:15]})", url=invite_link)])
            except Exception as e:
                logger.error(f"Could not build button for {ch}: {e}")

        # 2. Check / Verify Button
        keyboard.append([InlineKeyboardButton("🔄 Check / Verify Joined", callback_data="check_forcesub")])

        # 3. Custom Editable Buttons
        custom_btns = await self.get_custom_buttons()
        for btn in custom_btns:
            keyboard.append([InlineKeyboardButton(btn["label"], url=btn["url"])])

        return InlineKeyboardMarkup(keyboard)
