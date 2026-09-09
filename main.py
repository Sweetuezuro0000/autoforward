import os
import sys
import time
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Main")

from pyrogram import Client, idle
from pyrogram.errors import FloodWait, RPCError
from motor.motor_asyncio import AsyncIOMotorClient

from force import ForceSubManager
from bot import register_handlers

# Mandatory Config Checks
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not all([API_ID, API_HASH, STRING_SESSION, BOT_TOKEN, MONGO_URI]):
    logger.critical("Missing required environment variables! Check API_ID, API_HASH, STRING_SESSION, BOT_TOKEN, MONGO_URI.")
    sys.exit(1)

API_ID = int(API_ID)

# MongoDB Initialization
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["userbot_db"]
config_col = db["config"]
chats_col = db["chats"]

fs_mgr = ForceSubManager(db)

# Pyrogram / Hydrogram Clients Initialization
userbot = Client(
    name="userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION,
    in_memory=True
)

bot = Client(
    name="bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

async def auto_broadcast_worker():
    logger.info("Auto Broadcast Worker task initiated.")
    while True:
        try:
            await asyncio.sleep(5)
            
            cfg = await config_col.find_one({"_id": "global"}) or {}
            if not cfg.get("automsg_enabled", False):
                continue

            automsg_text = cfg.get("automsg_text")
            if not automsg_text:
                continue

            # Verify ForceSub validity before broadcasting
            fs = await fs_mgr.get_forcesub()
            if fs:
                try:
                    await userbot.get_chat(fs)
                except Exception as e:
                    logger.error(f"ForceSub channel check failed ({fs}): {e}. Turning off auto broadcast.")
                    await config_col.update_one(
                        {"_id": "global"},
                        {"$set": {"automsg_enabled": False}},
                        upsert=True
                    )
                    continue

            cursor = chats_col.find({})
            async for item in cursor:
                chat_id = item["_id"]
                delay = item.get("delay", 60)
                last_sent = item.get("last_sent", 0)

                now = time.time()
                if now - last_sent < delay:
                    continue

                try:
                    await userbot.send_message(chat_id, automsg_text)
                    await chats_col.update_one(
                        {"_id": chat_id},
                        {"$set": {"last_sent": time.time()}}
                    )
                    logger.info(f"Broadcast sent successfully to chat: {chat_id}")
                except FloodWait as fw:
                    logger.warning(f"FloodWait hit on chat {chat_id}. Sleeping for {fw.value + 2}s")
                    await asyncio.sleep(fw.value + 2)
                except RPCError as rpc:
                    logger.error(f"RPC Error on chat {chat_id}: {rpc}")
                except Exception as ex:
                    logger.error(f"Failed to send broadcast to {chat_id}: {ex}")

        except asyncio.CancelledError:
            break
        except Exception as global_ex:
            logger.error(f"Unexpected error in broadcast worker loop: {global_ex}")
            await asyncio.sleep(10)

async def main():
    logger.info("Registering handlers...")
    register_handlers(userbot, bot, db, fs_mgr)

    logger.info("Starting Pyrogram Userbot and Bot API clients...")
    await userbot.start()
    await bot.start()
    
    logger.info("Clients successfully started!")

    # Start broadcast worker ONLY AFTER clients are fully started
    broadcast_task = asyncio.create_task(auto_broadcast_worker())

    logger.info("System is up and running.")
    await idle()

    logger.info("Stopping system and cleaning up...")
    broadcast_task.cancel()
    await userbot.stop()
    await bot.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
