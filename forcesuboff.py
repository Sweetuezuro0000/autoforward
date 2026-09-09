from pyrogram import filters
from pyrogram.types import Message
from core import app,set_config
@app.on_message(filters.me & filters.command('forcesuboff',prefixes='.'))
async def forcesuboff_handler(client,message:Message): set_config('force_sub',''); await message.edit_text('🔓 **Force-Sub Disabled.**')
