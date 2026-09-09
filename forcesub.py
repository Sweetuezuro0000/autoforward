from pyrogram import filters
from pyrogram.types import Message
from core import app,set_config,parse_target
@app.on_message(filters.me & filters.command('forcesub',prefixes='.'))
async def forcesub_handler(client,message:Message):
    a=(message.text or '').split(maxsplit=1)
    if len(a)<2: return await message.edit_text('❌ **Usage:** `.forcesub <channel_username/link/id>`')
    try:
        chat=await client.get_chat(parse_target(a[1])); value=chat.username or str(chat.id); set_config('force_sub',value.lstrip('@')); shown='@'+chat.username if chat.username else str(chat.id); await message.edit_text(f'🔒 **Force Sub set to:** `{shown}`')
    except Exception as e: await message.edit_text(f'❌ **ForceSub Error:** `{e}`')
