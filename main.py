import asyncio
from core import app,init_defaults,start_health_thread,auto_broadcast_loop
import addchat,delchat,listchats,setchatdelay,setmsg,forcesub,forcesuboff,automsg,stats,help_cmd
async def main():
    init_defaults(); start_health_thread(); await app.start(); print('🚀 Userbot started - all commands loaded'); asyncio.create_task(auto_broadcast_loop()); await asyncio.Event().wait()
if __name__=='__main__': asyncio.run(main())
