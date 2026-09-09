import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, PeerIdInvalid, ChannelInvalid
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

class ForceSubManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.config = db["config"]

    async def get_forcesub(self):
        doc = await self.config.find_one({"_id": "global"})
        if doc and "forcesub" in doc:
            return doc["forcesub"]
        env_fs = os.getenv("FORCE_SUB")
        if env_fs:
            try:
                return int(env_fs)
            except ValueError:
                return env_fs
        return None

    async def set_forcesub(self, channel_id_or_username):
        await self.config.update_one(
            {"_id": "global"},
            {"$set": {"forcesub": channel_id_or_username}},
            upsert=True
        )

    async def remove_forcesub(self):
        await self.config.update_one(
            {"_id": "global"},
            {"$unset": {"forcesub": ""}},
            upsert=True
        )

    async def check_user_subscribed(self, client: Client, channel, user_id: int) -> bool:
        try:
            member = await client.get_chat_member(channel, user_id)
            if member.status in ["kicked", "banned"]:
                return False
            return True
        except UserNotParticipant:
            return False
        except (ChatAdminRequired, PeerIdInvalid, ChannelInvalid) as e:
            logger.error(f"ForceSub check failed for channel {channel}: {e}")
            return True
        except Exception as e:
            logger.error(f"Unexpected error in ForceSub check: {e}")
            return True

    async def get_invite_link(self, client: Client, channel) -> str:
        try:
            chat = await client.get_chat(channel)
            if chat.invite_link:
                return chat.invite_link
            if chat.username:
                return f"https://t.me/{chat.username}"
            link = await client.export_chat_invite_link(channel)
            return link
        except Exception as e:
            logger.error(f"Error getting invite link for {channel}: {e}")
            return "https://t.me"

    async def handle_pm_guard(self, client: Client, message: Message) -> bool:
        forcesub = await self.get_forcesub()
        if not forcesub:
            return True

        if message.from_user is None or message.from_user.is_bot:
            return True

        user_id = message.from_user.id
        is_sub = await self.check_user_subscribed(client, forcesub, user_id)
        if not is_sub:
            invite_link = await self.get_invite_link(client, forcesub)
            await message.reply_text(
                "⚠️ **Access Denied!**\n\n"
                "To send messages or use this bot, you must join our updates channel first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Channel", url=invite_link)]
                ])
            )
            return False
        return True
