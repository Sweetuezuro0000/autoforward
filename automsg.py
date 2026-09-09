from pyrogram import filters
from pyrogram.types import Message
from core import app,get_config,set_config,is_force_sub_active
@app.on_message(filters.me & filters.command('automsg',prefixes='.'))
async def automsg_handler(client,message:Message):
    a=(message.text or '').split(maxsplit=1)
    if len(a)<2 or a[1].lower() not in {'on','off'}: return await message.edit_text('❌ **Usage:** `.automsg on` ya `.automsg off`')
    state=a[1].upper()
    if state=='ON' and not await is_force_sub_active(): return await message.edit_text(f"❌ **Force Sub active nahi hai:** `@{get_config('force_sub') or 'None'}`")
    set_config('status',state); await message.edit_text(f'🤖 **Auto Messaging is `{state}`!**')
