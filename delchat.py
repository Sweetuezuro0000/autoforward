from pyrogram import filters
from pyrogram.types import Message
from core import app,conn,cursor
@app.on_message(filters.me & filters.command('delchat',prefixes='.'))
async def delchat_handler(client,message:Message):
    a=(message.text or '').split(maxsplit=1); cid=str(message.chat.id) if len(a)<2 else a[1].strip(); cursor.execute('DELETE FROM chats WHERE chat_id=?',(cid,)); conn.commit(); await message.edit_text(f'🗑 **Chat `{cid}` Removed!**')
