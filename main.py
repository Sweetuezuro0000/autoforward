import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from aiogram import Bot, Dispatcher
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client

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
# AIORGRAM BOT
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
# CONFIG FUNCTIONS
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
    doc = await config_col.find_one({"_id": key})

    if not doc:
        return default

    return doc.get("value", default)


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
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def health_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    log.info(f"Health server running on port {PORT}")

    server.serve_forever()


def start_health_thread():
    Thread(
        target=health_server,
        daemon=True
    ).start()

# =========================================================
# MAIN
# =========================================================

async def main():
    await mongo_init()

    # Import handlers AFTER app/db are ready.
    import force
    import bot

    # Load FORCE_SUB from Render environment on startup.
    if FORCE_SUB_ENV:
        await force.load_force_sub_from_env(FORCE_SUB_ENV)

    start_health_thread()

    await app.start()

    log.info("🚀 Userbot started")
    log.info("✅ MongoDB connected")
    log.info("✅ Session commands loaded")

    asyncio.create_task(
        bot.broadcast_worker()
    )

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

        log.info("🤖 Bot API started")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
