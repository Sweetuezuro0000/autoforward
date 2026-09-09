import os, sqlite3, time, asyncio
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait

API_ID=int(os.environ.get('API_ID','0')); API_HASH=os.environ.get('API_HASH',''); SESSION_STRING=os.environ.get('SESSION_STRING','')
app=Client('userbot_session',api_id=API_ID,api_hash=API_HASH,session_string=SESSION_STRING)
conn=sqlite3.connect('userbot_data.db',check_same_thread=False); cursor=conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS chats (chat_id TEXT PRIMARY KEY,title TEXT,interval_sec INTEGER DEFAULT 60,last_sent REAL DEFAULT 0)')
cursor.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY,value TEXT)')
cursor.execute('CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY,value INTEGER)'); conn.commit()

def get_config(key,default=''):
    cursor.execute('SELECT value FROM config WHERE key=?',(key,)); r=cursor.fetchone(); return r[0] if r else default

def set_config(key,value):
    cursor.execute('INSERT OR REPLACE INTO config(key,value) VALUES(?,?)',(key,str(value))); conn.commit()

def get_stat(key):
    cursor.execute('SELECT value FROM stats WHERE key=?',(key,)); r=cursor.fetchone(); return int(r[0]) if r else 0

def inc_stat(key,amount=1): set_config_stat=None; cursor.execute('SELECT value FROM stats WHERE key=?',(key,)); r=cursor.fetchone(); current=int(r[0]) if r else 0; cursor.execute('INSERT OR REPLACE INTO stats(key,value) VALUES(?,?)',(key,current+amount)); conn.commit()

def get_all_chats():
    cursor.execute('SELECT chat_id,title,interval_sec,last_sent FROM chats ORDER BY title'); return cursor.fetchall()

def update_last_sent(chat_id,ts): cursor.execute('UPDATE chats SET last_sent=? WHERE chat_id=?',(ts,chat_id)); conn.commit()

def parse_target(v):
    v=str(v).strip()
    if 't.me/' in v: v=v.split('t.me/',1)[1].split('/',1)[0]
    v=v.replace('+','').strip()
    if v.lstrip('-').isdigit(): return int(v)
    return v if v.startswith('@') else '@'+v

async def is_force_sub_active():
    ch=get_config('force_sub','').strip()
    if not ch: return True
    try:
        member=await app.get_chat_member(parse_target(ch),(await app.get_me()).id)
        return member.status in {ChatMemberStatus.OWNER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.MEMBER}
    except Exception as e:
        print(f'[ForceSub Error] {e}'); return False

async def auto_broadcast_loop():
    while True:
        try:
            await asyncio.sleep(2)
            if get_config('status','OFF')!='ON': continue
            if not await is_force_sub_active():
                set_config('status','OFF')
                try: await app.send_message('me',f"❌ **Auto Msg Stopped!** Force Sub (@{get_config('force_sub')}) leave kar diya gaya hai.")
                except Exception: pass
                continue
            msg=get_config('msg',''); now=time.time()
            for chat_id,title,interval,last_sent in get_all_chats():
                if get_config('status','OFF')!='ON': break
                if now-float(last_sent or 0)<int(interval): continue
                try:
                    await app.send_message(int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id,msg)
                    update_last_sent(chat_id,time.time()); inc_stat('total_sent'); inc_stat('success_sent'); await asyncio.sleep(2)
                except FloodWait as e: await asyncio.sleep(e.value)
                except Exception as e: inc_stat('failed_sent'); print(f'[Send Error] {chat_id}: {e}')
        except Exception as e:
            print(f'[Broadcast Error] {e}'); await asyncio.sleep(5)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'Userbot is running!')
    def log_message(self,*args): return

def start_health_thread():
    port=int(os.environ.get('PORT','10000')); Thread(target=lambda:ThreadingHTTPServer(('0.0.0.0',port),HealthHandler).serve_forever(),daemon=True).start()

def init_defaults():
    if not get_config('status'): set_config('status','OFF')
    if not get_config('msg'): set_config('msg','Default Auto Message')
    if get_config('force_sub') is None: set_config('force_sub','')
