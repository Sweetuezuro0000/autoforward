import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# =========================================================
# PYROGRAM PEER-ID FIX
# Pyrogram 2.0.106 has an outdated channel-id boundary.
# This allows newer -100xxxxxxxxxxxx channel IDs.
# =========================================================

import pyrogram.utils

pyrogram.utils.MIN_CHANNEL_ID = -1007852516352
pyrogram.utils.MIN_CHAT_ID = -999999999999

# =========================================================
# IMPORTS
# =========================================================

from pyrogram import Client
from aiogram import Bot, Dispatcher
from motor.motor_asyncio import AsyncIOMotorClient

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("autoforward")

# =========================================================
# ENVIRONMENT
# =========================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "autoforward")

FORCE_SUB_ENV = os.environ.get("FORCE_SUB", "")

PORT = int(os.environ.get("PORT", "10000"))

if not API_ID:
    raise RuntimeError("API_ID missing")

if not API_HASH:
    raise RuntimeError("API_HASH missing")

if not SESSION_STRING:
    raise RuntimeError("SESSION_STRING missing")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI missing")

# =========================================================
# PYROGRAM USERBOT
# =========================================================

app = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# =========================================================
# TELEGRAM BOT
# =========================================================

tg_bot = Bot(BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

# =========================================================
# MONGODB
# =========================================================

mongo_client = AsyncIOMotorClient(MONGO_URI)

db = mongo_client[MONGO_DB]

config_col = db["config"]
chats_col = db["chats"]

# =========================================================
# MONGO CONFIG
# =========================================================

async def mongo_init():

    await config_col.update_one(
        {"_id": "status"},
        {"$setOnInsert": {"value": "OFF"}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "delay"},
        {"$setOnInsert": {"value": 8}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "msg"},
        {"$setOnInsert": {"value": "Default Message"}},
        upsert=True
    )

    await config_col.update_one(
        {"_id": "force_sub"},
        {"$setOnInsert": {"value": []}},
        upsert=True
    )


async def get_config(key, default=None):

    doc = await config_col.find_one(
        {"_id": key}
    )

    if not doc:
        return default

    return doc.get(
        "value",
        default
    )


async def set_config(key, value):

    await config_col.update_one(
        {"_id": key},
        {"$set": {"value": value}},
        upsert=True
    )

# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"OK"
        )

    def log_message(self, *args):
        return


def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    log.info(
        f"Health server running on port {PORT}"
    )

    server.serve_forever()


def start_health_thread():

    Thread(
        target=start_health_server,
        daemon=True
    ).start()

# =========================================================
# MAIN
# =========================================================

async def main():

    # Mongo first
    await mongo_init()

    # Register handlers only after shared objects exist
    import force
    import bot

    # Load ForceSub from Render ENV
    if FORCE_SUB_ENV:

        await force.load_force_sub_from_env(
            FORCE_SUB_ENV
        )

    # Health server
    start_health_thread()

    # IMPORTANT:
    # Pyrogram must be started BEFORE broadcast worker
    await app.start()

    log.info(
        "🚀 USERBOT STARTED"
    )

    log.info(
        "✅ MongoDB connected"
    )

    log.info(
        "✅ Session commands loaded"
    )

    # Start auto broadcaster AFTER app.start()
    asyncio.create_task(
        bot.broadcast_worker()
    )

    # Start Bot API
    if tg_bot:

        await tg_bot.delete_webhook(
            drop_pending_updates=True
        )

        asyncio.create_task(
            dp.start_polling(
                tg_bot,
                allowed_updates=dp.resolve_used_update_types()
            )
        )

        log.info(
            "🤖 BOT API STARTED"
        )

    # Keep process alive
    await asyncio.Event().wait()


if __name__ == "__main__":

    asyncio.run(main())
