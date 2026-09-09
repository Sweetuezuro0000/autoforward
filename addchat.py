from pyrogram import filters
from pyrogram.types import Message
from core import app,conn,cursor,parse_target
@app.on_message(filters.me & filters.command('addchat',prefixes='.'))
async def addchat_handler(client,message:Message):
    a=(message.text or '').split(); interval=60
    if len(a)==1: target=message.chat.id
    elif len(a)==2: target=message.chat.id if a[1].isdigit() else parse_target(a[1]); interval=int(a[1]) if a[1].isdigit() else 60
    else: target=parse_target(a[1]); interval=int(a[2]) if a[2].isdigit() else 60
    try:
        chat=await client.get_chat(target); cid=str(chat.id); title=chat.title or chat.first_name or 'Unknown'
        cursor.execute('INSERT OR REPLACE INTO chats(chat_id,title,interval_sec,last_sent) VALUES(?,?,?,?)',(cid,title,interval,0)); conn.commit()
        await message.edit_text(f'✅ **Chat Added!**\n\n📌 **Title:** `{title}`\n🆔 **ID:** `{cid}`\n⏱ **Interval:** `{interval}` seconds')
    except Exception as e: await message.edit_text(f'❌ **Error adding chat:** `{e}`')
