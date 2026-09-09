from pyrogram import filters
from pyrogram.types import Message
from core import app,conn,cursor
@app.on_message(filters.me & filters.command('setchatdelay',prefixes='.'))
async def setchatdelay_handler(client,message:Message):
    a=(message.text or '').split(maxsplit=2)
    if len(a)<3 or not a[2].isdigit(): return await message.edit_text('❌ **Usage:** `.setchatdelay <chat_id> <seconds>`')
    cid=a[1].strip(); sec=int(a[2]); cursor.execute('UPDATE chats SET interval_sec=? WHERE chat_id=?',(sec,cid)); conn.commit()
    if cursor.rowcount==0: return await message.edit_text(f'❌ **Chat `{cid}` nahi mila.** Pehle `.addchat` karo.')
    await message.edit_text(f'⏱ **Chat `{cid}` ka delay `{sec}` seconds set kar diya gaya!**')
