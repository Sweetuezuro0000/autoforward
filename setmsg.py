from pyrogram import filters
from pyrogram.types import Message
from core import app,set_config
@app.on_message(filters.me & filters.command('setmsg',prefixes='.'))
async def setmsg_handler(client,message:Message):
    a=(message.text or '').split(maxsplit=1)
    if len(a)<2: return await message.edit_text('❌ **Usage:** `.setmsg <text>`')
    set_config('msg',a[1]); await message.edit_text('✅ **Broadcast message updated!**')
