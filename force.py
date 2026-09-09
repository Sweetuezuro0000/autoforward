import os
from pyrogram.enums import ChatMemberStatus

from main import app

FORCE_SUB = os.environ.get("FORCE_SUB", "").strip()

def set_force_sub(value: str):
    global FORCE_SUB
    FORCE_SUB = value.strip().lstrip("@")

def get_force_sub():
    return FORCE_SUB

def normalize_target(value):
    value = str(value).strip()
    if "t.me/" in value:
        value = value.split("t.me/", 1)[1].split("/", 1)[0]
    value = value.replace("+", "").strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") else "@" + value

async def force_sub_ok():
    channel = get_force_sub()
    if not channel:
        return True

    try:
        me = await app.get_me()
        member = await app.get_chat_member(normalize_target(channel), me.id)
        return member.status in {
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        }
    except Exception as e:
        print(f"[ForceSub] {e}")
        return False
