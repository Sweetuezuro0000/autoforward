from pyrogram import filters
from pyrogram.types import Message
from core import app
@app.on_message(filters.me & filters.command('help',prefixes='.'))
async def help_handler(client,message:Message):
    await message.edit_text('🤖 **Session Commands**\n\n`.addchat`\n`.addchat 30`\n`.addchat @group 60`\n`.setchatdelay <chat_id> <seconds>`\n`.delchat <chat_id>`\n`.listchats`\n`.setmsg <text>`\n`.forcesub <channel>`\n`.forcesuboff`\n`.automsg on/off`\n`.stats`')
