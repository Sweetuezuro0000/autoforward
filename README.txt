FILES

main.py
- starts Pyrogram userbot
- starts Render health server
- starts Telegram Bot polling

bot.py
- ALL session commands in one file
- .help
- .addchat
- .delchat
- .listchats
- .setchatdelay
- .setmsg
- .automsg
- .stats
- /start
- /help
- /status
- auto broadcast
- SQLite persistence

force.py
- ForceSub logic only

requirements.txt
- dependencies

ENV
API_ID
API_HASH
SESSION_STRING
BOT_TOKEN
PORT (Render gives this automatically)

RUN
python main.py
