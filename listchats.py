from pyrogram import filters
from pyrogram.types import Message
from core import app,get_all_chats
@app.on_message(filters.me & filters.command('listchats',prefixes='.'))
async def listchats_handler(client,message:Message):
    rows=get_all_chats()
    if not rows: return await message.edit_text('ℹ️ **No chats added.**')
    text='📋 **Target Chats & Timing**\n\n'
    for i,(cid,title,interval,_) in enumerate(rows,1): text+=f'{i}. **{title}**\n   🆔 `{cid}`\n   ⏱ `{interval}s`\n\n'
    await message.edit_text(text)
