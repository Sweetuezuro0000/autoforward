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

from hydrogram import Client, idle
from hydrogram.errors import FloodWait, RPCError
from motor.motor_asyncio import AsyncIOMotorClient

from force import ForceSubManager
from bot import register_handlers

REQUIRED_ENV_VARS = ["API_ID", "API_HASH", "STRING_SESSION", "BOT_TOKEN", "MONGO_URI"]
env_data = {}
missing_vars = []

for var in REQUIRED_ENV_VARS:
    val = os.environ.get(var)
    if val is not None:
        val = val.strip().strip("'").strip('"')
    if not val:
        missing_vars.append(var)
    else:
        env_data[var] = val

if missing_vars:
    logger.critical(f"❌ Missing or empty Environment Variable(s): {', '.join(missing_vars)}")
    sys.exit(1)

try:
    API_ID = int(env_data["API_ID"])
except ValueError:
    logger.critical("❌ API_ID must be a valid integer!")
    sys.exit(1)

API_HASH = env_data["API_HASH"]
STRING_SESSION = env_data["STRING_SESSION"]
BOT_TOKEN = env_data["BOT_TOKEN"]
MONGO_URI = env_data["MONGO_URI"]

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["userbot_db"]
config_col = db["config"]
chats_col = db["chats"]

fs_mgr = ForceSubManager(db)

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

            fs_list = await fs_mgr.get_forcesubs()
            for fs in fs_list:
                try:
                    await userbot.get_chat(fs)
                except Exception as e:
                    logger.error(f"ForceSub check failed ({fs}): {e}")

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

async def dummy_health_check(reader, writer):
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()

async def main():
    logger.info("Starting Hydrogram Userbot and Bot API clients...")
    await userbot.start()
    await bot.start()
    logger.info("Clients successfully started!")

    # Register handlers AFTER clients start so Owner ID is dynamically detected
    logger.info("Registering handlers...")
    register_handlers(userbot, bot, db, fs_mgr)

    # Render Port Server Binding Fix
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(dummy_health_check, "0.0.0.0", port)
    logger.info(f"Dummy Web Server running on port {port} for Render health check.")

    broadcast_task = asyncio.create_task(auto_broadcast_worker())

    logger.info("System is up and running.")
    await idle()

    logger.info("Stopping system and cleaning up...")
    server.close()
    await server.wait_closed()
    broadcast_task.cancel()
    await userbot.stop()
    await bot.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
