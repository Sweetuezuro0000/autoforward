import asyncio
import os
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyrogram import Client

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

app = Client(
    "userbot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass

def start_health():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

async def main():
    # Import after app is created so command handlers register on this app.
    import force
    import bot

    bot.init_db()

    Thread(target=start_health, daemon=True).start()

    await app.start()
    print("🚀 Userbot started - all commands loaded")

    asyncio.create_task(bot.broadcast_loop())

    print("🤖 Telegram Bot handlers loaded")

    # Bot API polling and userbot run together.
    await asyncio.gather(
        bot.dp.start_polling(bot.tg_bot),
        asyncio.Event().wait()
    )

if __name__ == "__main__":
    asyncio.run(main())
