from pyrogram import filters
from pyrogram.types import Message
from core import app,get_all_chats,get_config,get_stat
@app.on_message(filters.me & filters.command('stats',prefixes='.'))
async def stats_handler(client,message:Message):
    t=('📊 **Auto-Bot Dashboard**\n━━━━━━━━━━━━━━━━━━━━\n'
       f"⚡ **Status:** `{get_config('status','OFF')}`\n"
       f"🔒 **Force Sub:** `@{get_config('force_sub') or 'None'}`\n"
       f"🎯 **Target Chats:** `{len(get_all_chats())}`\n\n"
       '📈 **Sending History:**\n'
       f"├ 🚀 **Total Attempts:** `{get_stat('total_sent')}`\n"
       f"├ ✅ **Successfully Sent:** `{get_stat('success_sent')}`\n"
       f"└ ❌ **Failed:** `{get_stat('failed_sent')}`\n\n"
       f"📝 **Current Message:**\n`{get_config('msg','')}`")
    await message.edit_text(t)
