#!/usr/bin/env python3
# language: Python 3.10+
# file: rich_all.py
# target: Windows / Linux / Termux / VPS / Docker / Railway
# deps: pip install "python-telegram-bot>=21" telethon yt-dlp gTTS requests edge-tts
#       ffmpeg required on PATH

import asyncio
import atexit
import copy
import io
import json
import logging
import os
import random
import re
import signal
import subprocess
import sys
import time
import traceback
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional, Set

BASE_DIR  = Path(__file__).resolve().parent
DATA_DIR  = Path(os.environ.get("RICH_DATA_DIR", str(BASE_DIR)))
USERS_DIR = DATA_DIR / "users"
SESS_DIR  = DATA_DIR / "sessions"
RUN_DIR   = DATA_DIR / "run"
DL_DIR    = DATA_DIR / "downloads"
TMP_DIR   = DATA_DIR / "temp"
for d in (DATA_DIR, USERS_DIR, SESS_DIR, RUN_DIR, DL_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.environ.get(
    "RICH_BOT_TOKEN",
    "8850641545:AAHjscoQxO6FQh0OZ3h0HjfYu5B7cNJmcm4", # ISKO CHANGE KAR RAILWAY PE
)

# ─── fee config ───
FEE_RATE      = float(os.environ.get("RICH_FEE_RATE", "1.0"))   # percent
FEE_MIN       = float(os.environ.get("RICH_FEE_MIN",  "10"))    # rupees

logging.disable(logging.WARNING)
for _n in ("telethon", "telethon.network", "telethon.network.mtprotosender",
           "telethon.extensions", "telethon.client", "telethon.crypto",
           "telegram", "telegram.ext", "httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)

LOG = logging.getLogger("rich")
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                                      datefmt="%H:%M:%S"))
    LOG.addHandler(_h)

IS_USERBOT = "--userbot" in sys.argv

try:
    import requests
    import yt_dlp
    from gtts import gTTS
    from telethon import TelegramClient, events, functions, types
    from telethon.errors import (
        FloodWaitError, RPCError, SessionPasswordNeededError,
        PhoneCodeInvalidError, PhoneCodeExpiredError,
        PasswordHashInvalidError, ApiIdInvalidError,
    )
    from telethon.tl.functions.channels import (
        CreateChannelRequest, EditAdminRequest, EditTitleRequest,
        EditPhotoRequest, InviteToChannelRequest, LeaveChannelRequest,
    )
    from telethon.tl.functions.messages import (
        ExportChatInviteRequest, SendReactionRequest, EditChatTitleRequest,
        AddChatUserRequest, EditChatAdminRequest,
    )
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.functions.account import (
        UpdateProfileRequest, GetAuthorizationsRequest, 
        ResetAuthorizationRequest
    )
    from telethon.tl.functions.auth import ResetAuthorizationsRequest
    from telethon.tl.functions.photos import (
        UploadProfilePhotoRequest, DeletePhotosRequest,
    )
    from telethon.tl.types import (
        ChatAdminRights, ReactionEmoji,
        DocumentAttributeAudio, InputChatUploadedPhoto,
    )
except Exception as e:
    print(f"missing dep: {e}")
    print('pip install "python-telegram-bot>=21" telethon yt-dlp gTTS requests edge-tts')
    sys.exit(1)

try:
    import edge_tts
    _EDGE_OK = True
except Exception:
    edge_tts = None
    _EDGE_OK = False


def _ffmpeg_ok() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# ==================================================================
#  SECTION 1 — USERBOT
# ==================================================================

API_ID    = int(os.environ.get("RICH_API_ID",   "0"))
API_HASH  = os.environ.get("RICH_API_HASH",     "")
OWNER_ID  = int(os.environ.get("RICH_OWNER_ID", "8929130604"))
PHONE     = os.environ.get("RICH_PHONE",        "")
SESSION   = os.environ.get("RICH_SESSION",      str(DATA_DIR / "rich_session"))
STATE_FILE = os.environ.get("RICH_STATE",       str(DATA_DIR / "rich_state.json"))

PRIMARY_OWNERS: Set[int] = {8929130604, 8846085944}
if OWNER_ID and OWNER_ID not in PRIMARY_OWNERS:
    PRIMARY_OWNERS.add(OWNER_ID)

BRAND = "𝐑 𝐈 𝐂 𝐇"

GC_DEFAULT_TITLE = "Rich | MM | service"
GC_MM_TEMPLATE = (
    "Hey. Please state the terms of the deal.\n\n"
    "- What is the deal?\n"
    "- Who is the buyer/seller?\n"
    "- what is deal ammount?\n"
    "- Include any other relevant information."
)

FASTGC_EMOJIS    = ["🤖"] * 23
GC_FAST_INTERVAL = 0.30
MSG_CACHE_LIMIT  = 300
SNIPE_LIMIT      = 50
EDIT_LIMIT       = 30

_DEFAULT_STATE = {
    "admins":       [],
    "mutes":        {},
    "gmutes":       [],
    "locks":        [],
    "muted_gcs":    [],
    "banner":       None,
    "reply_targets":  [],
    "rr_targets":     [],
    "flag_targets":   [],
    "shadow_targets": [],
    "auto_react":   None,
    "auto_clear_titles": False,
    "config": {
        "spray_delay": 0.25,
        "raid_delay":  0.25,
        "cooldown":    1.0,
        "debug":       False,
    },
}


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = copy.deepcopy(_DEFAULT_STATE)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                disk = json.load(f)
            for k, v in disk.items():
                self.data[k] = v
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            LOG.warning("state file corrupt, starting fresh")
        except Exception as e:
            LOG.warning(f"state load failed: {e}")

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            LOG.warning(f"state save failed: {e}")


ST = State(STATE_FILE)

admins: Set[int]         = set(int(x) for x in ST.data.get("admins", []) if str(x).lstrip("-").isdigit())
global_muted: Set[int]   = set(int(x) for x in ST.data.get("gmutes", []) if str(x).lstrip("-").isdigit())
group_locks: Set[int]    = set(int(x) for x in ST.data.get("locks", []) if str(x).lstrip("-").isdigit())
muted_gcs: Set[int]      = set(int(x) for x in ST.data.get("muted_gcs", []) if str(x).lstrip("-").isdigit())
muted_users: Dict[int, Set[int]] = {
    int(k): set(int(u) for u in v)
    for k, v in ST.data.get("mutes", {}).items()
    if str(k).lstrip("-").isdigit()
}
reply_users: Set[int]      = set(int(x) for x in ST.data.get("reply_targets", []))
rr_users: Set[int]         = set(int(x) for x in ST.data.get("rr_targets", []))
flag_users: Set[int]       = set(int(x) for x in ST.data.get("flag_targets", []))
replyshadow_users: Set[int]= set(int(x) for x in ST.data.get("shadow_targets", []))
menu_banner_msg: Optional[tuple] = tuple(ST.data["banner"]) if ST.data.get("banner") else None
auto_react_emoji: Optional[str]  = ST.data.get("auto_react")


def persist():
    ST.data["admins"]         = sorted(admins)
    ST.data["gmutes"]         = sorted(global_muted)
    ST.data["locks"]          = sorted(group_locks)
    ST.data["muted_gcs"]      = sorted(muted_gcs)
    ST.data["mutes"]          = {str(k): sorted(v) for k, v in muted_users.items() if v}
    ST.data["reply_targets"]  = sorted(reply_users)
    ST.data["rr_targets"]     = sorted(rr_users)
    ST.data["flag_targets"]   = sorted(flag_users)
    ST.data["shadow_targets"] = sorted(replyshadow_users)
    ST.data["auto_react"]     = auto_react_emoji
    ST.data["banner"]         = list(menu_banner_msg) if menu_banner_msg else None
    ST.save()


if API_ID and API_HASH:
    bot = TelegramClient(
        SESSION, API_ID, API_HASH,
        auto_reconnect=True, connection_retries=10, retry_delay=2,
        request_retries=5, timeout=30,
    )
else:
    bot = None

START_TIME = time.time()

raid_tasks:  Dict[int, asyncio.Task] = {}
spray_tasks: Dict[int, asyncio.Task] = {}
swipe_tasks: Dict[int, asyncio.Task] = {}
snipe_msgs:  Dict[int, Dict[int, dict]] = {}
snipe_cache: Dict[int, list] = {}
edit_cache:  Dict[int, list] = {}
CLONE_ACTIVE  = False
LAST_CLONE_ID = None
CLONE_DATA    = {"name": None, "last": None, "bio": None, "photo_bytes": None}
FASTGC_STATE  = {"active": False, "template": None, "task": None, "chat_id": None}
_cooldowns: Dict[tuple, float] = {}


RAID_MESSAGES = [
    "🤖 ʀᴀɴᴅɪ ᴋᴇ ʙᴀᴄʜʜᴇ ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴀᴀɢ ʟᴀɢᴀ ᴅᴜɴɢᴀ 🤖",
    "🤖 ʜᴀʀ ᴍᴇꜱꜱᴀɢᴇ ᴘᴇ ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋɪ ʏᴀᴀᴅ ᴅɪʟᴀᴛᴀ ʜᴜ 🤖",
    "🤖 ᴛᴇʀᴇ ʙᴀᴀᴘ ᴋᴀ ɴᴀᴀᴍ ʟᴇᴋᴇ ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ᴄʜᴏᴅ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴋʏᴀ ʀᴇ ᴄʜᴏᴅᴜ ᴛʏᴘɪɴɢ ʀᴏᴋ ɢᴀʏɪ ᴋʏᴀ ᴛᴇʀɪ 🤖",
    "🤖 ɴᴀꜱᴀ ꜱᴇ ɴᴇᴡꜱ ᴀᴀʏɪ ᴛᴇʀɪ ᴍᴀᴀ ɢᴀʟɪ ɢᴀʟɪ ᴄʜᴜᴅ ʀᴀʜɪ ʜᴀɪ 🤖",
    "🤖 ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋɪ ᴘɪᴄ ᴅᴇᴋʜɪ ᴏʀ ʟᴜɴᴅ ᴋʜᴀᴅᴀ ʜᴏ ɢᴀʏᴀ 🤖",
    "🤖 ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ᴍᴇɴᴇ ʀᴀɴᴅɪ ʙᴀɴᴀ ᴋᴇ ᴛᴇʀᴇ ᴄʜᴀᴄʜᴀ ᴋᴇ ꜱᴀᴛʜ ʙᴇᴄʜ ᴅɪʏᴀ 🤖",
    "🤖 ʙʜᴏꜱᴅɪᴋᴇ ᴛᴇʀɪ ᴍᴀᴀ ᴋᴀ ʙʜᴏꜱᴅᴀ ᴍᴇʀᴀ ʜᴀɪ ᴊᴀʙ ᴄʜᴀʜᴇ ᴄʜᴏᴅ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴋʏᴀ ʀᴇ ᴍᴀᴅᴀʀᴄʜᴏᴅ ᴛᴇʀɪ ɢᴀᴀɴᴅ ᴍᴇ ᴍᴇʀᴀ ʟᴜɴᴅ ɢʜᴜꜱᴀ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴛᴇʀᴇ ᴋʜᴀɴᴅᴀɴ ᴋɪ ʟᴀꜱʜ ᴋᴏ ɢᴀʟɪ ɢᴀʟɪ ɢʜᴜᴍᴀᴜɴɢᴀ 🤖",
    "🤖 ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴍᴇʀᴀ ʙᴇᴛᴀ ʜᴀɪ ᴊᴀɴᴛᴀ ʜᴀɪ ᴋʏᴀ 🤖",
    "🤖 ʀᴀɴᴅɪ ᴋᴇ ʙᴀᴄʜʜᴇ ᴀᴋᴀʟ ᴀᴀᴛɪ ᴛᴏ ʙᴏʟ ᴡʀɴᴀ ᴛᴇʀɪ ɢᴀᴀɴᴅ ᴍᴀᴀʀ ᴅᴜɴɢᴀ 🤖",
]
SWIPE_MESSAGES = [
    "🤖 ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ᴄʜᴏᴅ ᴋᴇ ᴋᴏᴛʜɪ ᴍᴇ ꜰᴇᴋ ᴀᴀᴜɴɢᴀ 🤖",
    "🤖 ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ꜱᴜᴘᴇʀᴄᴀʀ ɢʜᴜꜱᴀ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴋʏᴀ ʀᴇ ʀᴀɴᴅɪ ᴋᴇ ʀᴇᴘʟʏ ɴᴀʜɪ ᴋᴀʀᴛᴀ 🤖",
    "🤖 ᴛᴇʀᴀ ʙᴀᴀᴘ ʜᴜ ᴍᴀɪ ᴄʜᴜᴘ ᴋᴀʀ ʀᴀɴᴅɪᴋᴇ 🤖",
    "🤖 ᴛᴇʀɪ ᴍᴀᴀ ᴋᴀ ʙʜᴏꜱᴅᴀ ᴛᴇʀᴇ ʜᴀᴡᴀʟᴇ ᴋᴀʀ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴀᴀɢ ʟᴀɢᴀ ᴋᴇ ʀᴀᴀᴋʜ ᴋᴀʀ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴇᴋ ᴍᴇꜱꜱᴀɢᴇ ᴏʀ ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ʟᴀꜱʜ ɢᴀʟɪ ᴍᴇ ᴘᴀᴅɪ ʜᴏɢɪ 🤖",
    "🤖 ʀᴀɴᴅɪ ᴋᴇ ʙᴀᴄʜʜᴇ ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ᴍᴇɴᴇ ʀᴀɴᴅɪ ʙᴀɴᴀ ᴅɪʏᴀ 🤖",
    "🤖 ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ʟᴜɴᴅ ᴅᴀᴀʟ ᴅᴜɴɢᴀ 🤖",
    "🤖 ᴛᴇʀᴀ ᴋʜᴀɴᴅᴀɴ ᴋʜᴀᴛᴍ ᴛᴇʀɪ ᴍᴀᴀ ꜱᴇ ꜱʜᴜʀᴜ ʜᴏᴛᴀ ʜᴀɪ 🤖",
]
reply_list = [
    "ᴄʜᴜᴘ ᴋᴀʀ ʀᴀɴᴅɪ ᴋᴇ 🤖", "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴀᴀɢ 🤖",
    "ᴋʏᴀ ʀᴇ ᴄʜᴏᴅᴜ ʀᴇᴘʟʏ ᴅᴇ 🤖", "ᴛᴇʀɪ ʙᴇʜᴇɴ ʀᴀɴᴅɪ ʜᴀɪ 🤖",
    "ᴛᴇʀᴀ ʙᴀᴀᴘ ʜᴜ ᴍᴀɪ 🤖", "ɢᴀᴀɴᴅ ᴍᴀʀᴀ ᴀᴘɴɪ ᴍᴀᴅᴀʀᴄʜᴏᴅ 🤖",
    "ꜱʜᴀᴋᴀʟ ᴅᴇᴋʜ ᴀᴘɴɪ 🤖", "ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ʙʜᴇᴊ ᴍᴇʀᴇ ᴘᴀᴀꜱ 🤖",
    "ᴄʜᴜᴅ ᴊᴀ ᴄʜᴜᴘᴄʜᴀᴘ 🤖", "ᴛᴇʀɪ ɢᴀᴀɴᴅ ᴍᴇ ʟᴜɴᴅ 🤖",
    "ʀᴀɴᴅɪ ᴋᴇ ʙᴀᴄʜʜᴇ 🤖", "ᴛᴇʀɪ ᴍᴀᴀ ᴄʜᴜᴅ ʀᴀʜɪ ʜᴀɪ 🤖",
    "ᴋʏᴀ ʀᴇ ɢᴀɴᴅᴜ 🤖", "ᴛᴇʀᴀ ᴋʜᴀɴᴅᴀɴ ʀᴀɴᴅɪ ᴋʜᴀɴᴀ 🤖",
    "ᴀᴀ ᴊᴀ ᴛᴇʀɪ ɢᴀᴀɴᴅ ᴍᴀᴀʀᴜ 🤖",
]
reply_texts = [
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ʟᴜɴᴅ ᴅᴀᴀʟ ᴅᴜɴɢᴀ 🤖",
    "ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋᴏ ʀᴏᴢ ᴄʜᴏᴅᴛᴀ ʜᴜ ᴍᴀɪ 🤖",
    "ᴀᴘɴɪ ᴍᴀᴀ ᴋᴏ ʙᴏʟ ᴋᴇ ᴀᴀ 🤖",
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ᴍᴇʀᴇ ᴘᴀᴀꜱ ʙʜᴇᴊ 🤖",
    "ᴄʜᴜᴘ ᴋᴀʀ ᴡʀɴᴀ ɢᴀᴀɴᴅ ᴍᴀʀ ᴅᴜɴɢᴀ 🤖",
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ɢᴀᴀɴᴅ ᴄʜᴀᴛ ᴅᴜɴɢᴀ 🤖",
    "ʟᴜɴᴅ ᴘᴇ ʙᴀɪᴛʜ ᴊᴀ 🤖",
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴅᴀɪ ᴅᴇᴋʜ ʟᴇ ᴀᴘɴɪ 🤖",
    "ɢᴀɴᴅᴜ ᴋᴀʜɪ ᴋᴀ 🤖",
    "ꜱʜᴀᴋᴀʟ ꜱᴇ ʜɪ ʀᴀɴᴅɪ ʟᴀɢᴛᴀ ʜᴀɪ 🤖",
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋᴏ ꜱᴀᴍʙʜᴀʟ ᴋᴇ ʀᴀᴋʜ 🤖",
    "ᴍᴀᴀ ᴄʜᴜᴅᴀ ᴀᴘɴɪ ʏᴀʜᴀ ꜱᴇ 🤖",
]
fun_texts = [
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋᴇ ʙʜᴏꜱᴅᴇ ᴍᴇ ᴍᴇʀᴀ ʟᴜɴᴅ ꜰᴀꜱ ɢᴀʏᴀ 🤖",
    "ᴋʏᴀ ʀᴇ ᴄʜᴏᴅᴜ ʀᴇᴘʟʏ ɴᴀʜɪ ᴋᴀʀ ʀᴀʜᴀ 🤖",
    "ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋᴏ ᴄʜᴏᴅ ᴄʜᴜᴋᴀ ʜᴜ ᴍᴀɪ 🤖",
    "ᴀᴘɴɪ ᴍᴀᴀ ᴋɪ ꜱʜᴀᴋᴀʟ ᴅᴇᴋʜ ʟᴇ 🤖",
    "ɢᴀᴀɴᴅ ᴍᴀʀᴀ ʟᴇ ᴀᴘɴɪ 🤖",
    "ᴋʏᴀ ʀᴇ ʀᴀɴᴅɪ ᴋᴇ ʙᴀᴄʜʜᴇ 🤖",
    "ᴄʜᴜᴅ ᴊᴀ ʙʜᴀɪ ᴄʜᴜᴘᴄʜᴀᴘ 🤖",
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ᴀᴀɢ ʟᴀɢᴀ ᴅᴜɴɢᴀ 🤖",
]
flag_texts = [
    "ᴛᴇʀɪ ᴍᴀᴀ ᴋɪ ᴄʜᴜᴛ ᴍᴇ ʜᴀᴍʟᴀ ᴋᴀʀ ᴅᴜɴɢᴀ 🤖",
    "ᴀᴘɴɪ ᴍᴀᴀ ᴋᴏ ꜱᴀᴍʙʜᴀʟ ᴋᴇ ʀᴀᴋʜ 🤖",
    "ʙᴏʟ ᴋʏᴀ ᴘʀᴏʙʟᴇᴍ ʜᴀɪ 🤖",
    "ᴋʏᴀ ʀᴇ ɢᴀɴᴅᴜ 🤖",
    "ᴍᴀᴀ ᴄʜᴜᴅᴀ ᴀᴘɴɪ 🤖",
    "ᴄʜᴜᴅ ᴊᴀ ᴄʜᴜᴘᴄʜᴀᴘ 🤖",
    "ᴀᴀ ᴊᴀ ᴛᴇʀɪ ɢᴀᴀɴᴅ ᴍᴀᴀʀᴜ 🤖",
    "ᴛᴇʀɪ ʙᴇʜᴇɴ ᴋᴀɪꜱɪ ʜᴀɪ 🤖",
]


def now() -> float: return time.time()

def is_admin(uid: Optional[int]) -> bool:
    if not uid: return False
    return uid in PRIMARY_OWNERS or uid in admins

def is_user_muted(chat_id: int, user_id: int) -> bool:
    if user_id in global_muted: return True
    return chat_id in muted_users and user_id in muted_users[chat_id]

async def safe_edit(event, text: str, **kw):
    if not text: return None
    try:
        return await event.edit(text, **kw)
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 1)
        try: return await event.edit(text, **kw)
        except Exception: pass
    except Exception:
        pass
    try:
        return await event.reply(text, **kw)
    except Exception:
        return None

async def safe_delete(event, msgs=None):
    try:
        if msgs is None:
            await event.delete()
        else:
            await bot.delete_messages(event.chat_id, msgs)
    except Exception:
        pass

async def get_targets(event, arg: str = "") -> Set[int]:
    targets: Set[int] = set()
    if event.is_reply:
        try:
            r = await event.get_reply_message()
            if r and r.sender_id: targets.add(int(r.sender_id))
        except Exception: pass
    if arg:
        for part in arg.strip().split():
            if not part: continue
            if part.lstrip("-").isdigit():
                try: targets.add(int(part)); continue
                except Exception: pass
            try:
                ent = await bot.get_entity(part)
                if ent and getattr(ent, "id", None): targets.add(int(ent.id))
            except Exception: pass
    try:
        me = await bot.get_me(); targets.discard(me.id)
    except Exception: pass
    return targets

def human_uptime(seconds: int) -> str:
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d: return f"{d}d {h}h {m}m"
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"


commands: Dict[str, dict] = {}

def register_cmd(name, needs_reply=False, group_only=False, cooldown=None):
    def deco(func):
        key = (name or "").lower().strip()
        if not key: raise ValueError("empty command name")
        commands[key] = {
            "func": func,
            "needs_reply": needs_reply,
            "group_only": group_only,
            "cooldown": cooldown,
        }
        return func
    return deco

def check_cooldown(uid: int, cmd: str, secs: float) -> bool:
    if secs <= 0: return True
    key = (uid, cmd)
    t = now()
    if t - _cooldowns.get(key, 0) < secs: return False
    _cooldowns[key] = t
    return True

async def run_with_flood_retry(coro_factory, event, cmd_name: str):
    try:
        return await coro_factory()
    except FloodWaitError as fw:
        LOG.warning(f"[{cmd_name}] flood {fw.seconds}s, retrying")
        await asyncio.sleep(fw.seconds + 1)
        return await coro_factory()
    except asyncio.CancelledError:
        raise
    except RPCError as e:
        await safe_edit(event, f"🤖 RPC: {str(e)[:120]}")
    except Exception as e:
        LOG.error(f"[{cmd_name}] {type(e).__name__}: {e}\n{traceback.format_exc()}")
        await safe_edit(event, f"🤖 {type(e).__name__}: {str(e)[:100]}")


GIRL_VOICES = [
    "hi-IN-SwaraNeural", "en-IN-NeerjaNeural", "en-US-AriaNeural",
    "en-US-JennyNeural", "en-GB-SoniaNeural",
]
BOY_VOICES = [
    "hi-IN-MadhurNeural", "en-IN-PrabhatNeural",
    "en-US-GuyNeural", "en-GB-RyanNeural",
]

async def _edge_save(text: str, voice: str, out_path: str) -> bool:
    if not _EDGE_OK: return False
    try:
        comm = edge_tts.Communicate(text, voice, rate="+4%", pitch="-2Hz", volume="+0%")
        await comm.save(out_path)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        LOG.warning(f"[voice] edge error: {e}")
        return False


async def _tts_send(event, text: str, voice: str):
    fname = os.path.join(str(TMP_DIR), f"vn_{int(time.time())}.mp3")
    ok = await _edge_save(text, voice, fname)
    if not ok:
        try:
            lang = "hi" if voice.startswith("hi-") else "en"
            gTTS(text=text[:5000], lang=lang, slow=False).save(fname)
        except Exception as e:
            return await safe_edit(event, f"🤖 voice failed: {str(e)[:60]}")
    target_id = None
    if event.is_reply:
        try:
            r = await event.get_reply_message()
            if r: target_id = r.id
        except Exception: pass
    try:
        with open(fname, "rb") as fh:
            data = BytesIO(fh.read()); data.name = "rich.ogg"
        await bot.send_file(event.chat_id, data, voice_note=True, reply_to=target_id)
        try: await event.delete()
        except Exception: pass
    finally:
        try: os.remove(fname)
        except Exception: pass


MENU_TEXT = f"""
╔══════════════════════════════╗
      🤖   {BRAND}   🤖
╚══════════════════════════════╝

╭━━━━━ 🤖 𝐅𝐄𝐄𝐒 ━━━━━━╮
┃ 🤖 .fees <amount>  » mm fee calc
┃ 🤖 .feesrate       » current rate
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐀𝐃𝐌𝐈𝐍 ━━━━━╮
┃ 🤖 .admins / .addadmin / .deladmin
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐂𝐇𝐀𝐓 ━━━━━━╮
┃ 🤖 .id / .chatinfo / .pin / .unpin
┃ 🤖 .gc / .leave / .setgcpfp
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐌𝐔𝐓𝐄 ━━━━━━╮
┃ 🤖 .mute / .unmute / .gmute / .gunmute
┃ 🤖 .mutelist / .lock / .unlock
┃ 🤖 .mutegc on/off   » mute whole GC
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐆𝐑𝐎𝐔𝐏 ━━━━━╮
┃ 🤖 .purge / .throw
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐑𝐀𝐈𝐃 ━━━━━━╮
┃ 🤖 .reply / .rr / .flag / .replyshadow
┃ 🤖 .sreply / .srr / .sflag / .sshadow
┃ 🤖 .raid / .swipe / .sraid / .sswipe
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐒𝐏𝐀𝐌 ━━━━━━╮
┃ 🤖 .spray / .dspray / .fastgc
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐀𝐔𝐓𝐎 ━━━━━━╮
┃ 🤖 .ar <emoji> / .sar
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐂𝐋𝐄𝐀𝐑 ━━━━━╮
┃ 🤖 .clear on/off   » nuke title/photo
┃ 🤖 .clear stats    » counters
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐒𝐍𝐈𝐏𝐄 ━━━━━╮
┃ 🤖 .snipe / .edit / .seen
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐓𝐎𝐎𝐋𝐒 ━━━━━╮
┃ 🤖 .music / .dl / .weather
┃ 🤖 .voice / .voicegirl / .voiceboy
┃ 🤖 .cookies / .setcookies / .musicdebug
┃ 🤖 .post / .setpostpic
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 ━━━╮
┃ 🤖 .copy / .normal
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐒𝐘𝐒 ━━━━━━━╮
┃ 🤖 .ping / .status / .stats / .health
┃ 🤖 .backup / .restore / .reload / .stopall / .debug
┃ 🤖 .restart / .kill
╰━━━━━━━━━━━━━━━━━━━━╯

╭━━━━━ 🤖 𝐅𝐔𝐍 ━━━━━━━╮
┃ 🤖 .flip / .dice / .info / .heart
╰━━━━━━━━━━━━━━━━━━━━╯
""".strip()


# ---------- commands ----------
@register_cmd("menu")
async def cmd_menu(event, _):
    if event.out:
        try: await event.edit(MENU_TEXT)
        except Exception:
            try: await event.reply(MENU_TEXT)
            except Exception: pass
    else:
        await safe_delete(event)
        try: await bot.send_message(event.chat_id, MENU_TEXT)
        except Exception: pass

# ─── FEE CALC ───
def _parse_amount(raw: str) -> Optional[float]:
    if not raw: return None
    s = raw.strip().lower().replace(",", "").replace("₹", "").replace("$", "")
    mult = 1.0
    if s.endswith("cr"):
        mult = 10_000_000.0; s = s[:-2]
    elif s.endswith("lakh"):
        mult = 100_000.0; s = s[:-4]
    elif s.endswith("k"):
        mult = 1_000.0; s = s[:-1]
    elif s.endswith("l"):
        mult = 100_000.0; s = s[:-1]
    try:
        v = float(s) * mult
    except Exception:
        return None
    if v <= 0 or v != v: return None
    return v

def _calc_fee(amount: float) -> Dict[str, float]:
    raw = amount * (FEE_RATE / 100.0)
    fee = max(raw, FEE_MIN)
    return {
        "amount":   amount,
        "raw":      raw,
        "fee":      fee,
        "seller_a": amount - fee,       # fee from deal
        "buyer_b":  amount + fee,       # fee on top
        "min_hit":  raw < FEE_MIN,
    }

@register_cmd("fees")
async def cmd_fees(event, arg):
    if not arg:
        return await safe_edit(event,
            f"🤖 ʀɪᴄʜ ꜰᴇᴇ ᴄᴀʟᴄᴜʟᴀᴛᴏʀ\n\n"
            f"▸ use     : `.fees <amount>`\n"
            f"▸ example : `.fees 5000`  or  `.fees 5k`  or  `.fees 1lakh`\n\n"
            f"▸ rate    : {FEE_RATE}%\n"
            f"▸ minimum : ₹{FEE_MIN:.0f}")
    amount = _parse_amount(arg)
    if amount is None:
        return await safe_edit(event,
            "🤖 invalid amount.\n"
            "▸ try: `.fees 5000` / `.fees 5k` / `.fees 2lakh` / `.fees 1cr`")
    c = _calc_fee(amount)
    min_note = "  (min applied)" if c["min_hit"] else ""
    txt = (
        f"🤖 ʀɪᴄʜ ꜰᴇᴇ ᴄᴀʟᴄᴜʟᴀᴛᴏʀ 🤖\n\n"
        f"▸ ᴅᴇᴀʟ ᴀᴍᴏᴜɴᴛ : ₹{c['amount']:,.2f}\n"
        f"▸ ꜰᴇᴇ ({FEE_RATE:g}% · ᴍɪɴ ₹{FEE_MIN:,.0f}) : ₹{c['fee']:,.2f}{min_note}\n\n"
        f"ᴏᴘᴛɪᴏɴ ᴀ — ꜰᴇᴇ ꜰʀᴏᴍ ᴅᴇᴀʟ\n"
        f"  ▸ ꜱᴇʟʟᴇʀ ɢᴇᴛꜱ : ₹{c['seller_a']:,.2f}\n"
        f"  ▸ ᴍᴍ ᴋᴇᴇᴘꜱ : ₹{c['fee']:,.2f}\n\n"
        f"ᴏᴘᴛɪᴏɴ ʙ — ꜰᴇᴇ ᴏɴ ᴛᴏᴘ\n"
        f"  ▸ ʙᴜʏᴇʀ ᴘᴀʏꜱ : ₹{c['buyer_b']:,.2f}\n"
        f"  ▸ ꜱᴇʟʟᴇʀ ɢᴇᴛꜱ : ₹{c['amount']:,.2f}\n"
        f"  ▸ ᴍᴍ ᴋᴇᴇᴘꜱ : ₹{c['fee']:,.2f}\n\n"
        f"✦ ʀᴀᴛᴇ {FEE_RATE:g}% · ᴍɪɴ ₹{FEE_MIN:,.0f} ✦"
    )
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)


@register_cmd("feesrate")
async def cmd_feesrate(event, _):
    txt = (f"🤖 ʀɪᴄʜ ꜰᴇᴇ ʀᴀᴛᴇ\n\n"
           f"▸ ʀᴀᴛᴇ    : {FEE_RATE}%\n"
           f"▸ ᴍɪɴɪᴍᴜᴍ : ₹{FEE_MIN:,.0f}\n\n"
           f"▸ ᴛᴏ ᴄᴀʟᴄᴜʟᴀᴛᴇ: `.fees <amount>`\n"
           f"▸ ᴛᴏ ᴄʜᴀɴɢᴇ  : set env `RICH_FEE_RATE` / `RICH_FEE_MIN`")
    await safe_edit(event, txt)


@register_cmd("admins")
async def cmd_admins(event, _):
    primary = ", ".join(f"`{a}`" for a in sorted(PRIMARY_OWNERS))
    body = "\n".join(f"▸ `{a}`" for a in sorted(admins)) if admins else "▸ none"
    txt = (f"🤖━━〔 ᴀᴅᴍɪɴꜱ 〕━━🤖\nowner(s) ➤ {primary}\n\n{body}\n\n"
           f"total ➤ {len(admins)}\n🤖━━━━━━━━━━━━━━━━🤖")
    await safe_edit(event, txt)

@register_cmd("addadmin", needs_reply=True)
async def cmd_addadmin(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    added, already = [], []
    for uid in targets:
        if uid in PRIMARY_OWNERS: continue
        if uid in admins: already.append(str(uid))
        else: admins.add(uid); added.append(str(uid))
    persist()
    parts = []
    if added:   parts.append(f"added: {', '.join(added)}")
    if already: parts.append(f"already: {', '.join(already)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("deladmin", needs_reply=True)
async def cmd_deladmin(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    removed, not_admin = [], []
    for uid in targets:
        if uid in PRIMARY_OWNERS: continue
        if uid in admins: admins.discard(uid); removed.append(str(uid))
        else: not_admin.append(str(uid))
    persist()
    parts = []
    if removed:    parts.append(f"removed: {', '.join(removed)}")
    if not_admin:  parts.append(f"not admin: {', '.join(not_admin)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("id")
async def cmd_id(event, arg):
    lines = [f"your id ➤ `{event.sender_id}`", f"chat id ➤ `{event.chat_id}`"]
    if event.is_reply:
        try:
            r = await event.get_reply_message()
            if r:
                lines.append(f"reply id ➤ `{r.sender_id}`")
                lines.append(f"msg id ➤ `{r.id}`")
        except Exception: pass
    txt = "╭━━━〔 🤖 𝙄𝘿 🤖 〕━━━╮\n" + "\n".join("┃ " + l for l in lines) + \
          "\n╰━━━━━━━━━━━━━━━━━━━╯"
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)

@register_cmd("chatinfo")
async def cmd_chatinfo(event, _):
    try:
        chat = await event.get_chat()
        full = await bot(functions.channels.GetFullChannelRequest(chat.id)) \
               if isinstance(chat, types.Channel) \
               else await bot(functions.messages.GetFullChatRequest(chat_id=chat.id))
        about = getattr(full.full_chat, "about", "") or ""
        members = getattr(full.full_chat, "participants_count", None)
        txt = (f"🤖━━〔 ᴄʜᴀᴛ ɪɴꜰᴏ 〕━━🤖\n"
               f"▸ id    : `{chat.id}`\n▸ title : {getattr(chat, 'title', 'n/a')}\n"
               f"▸ user  : @{getattr(chat, 'username', None) or 'none'}\n"
               f"▸ type  : {type(chat).__name__}\n"
               f"▸ count : {members if members is not None else 'n/a'}\n"
               f"▸ about : {about[:200]}\n🤖━━━━━━━━━━━━━━━━🤖")
        await safe_edit(event, txt)
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:100]}")

@register_cmd("pin", needs_reply=True)
async def cmd_pin(event, arg):
    try:
        r = await event.get_reply_message()
        await bot.pin_message(event.chat_id, r.id, notify=False)
        await safe_edit(event, "🤖 Pinned")
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:80]}")

@register_cmd("unpin")
async def cmd_unpin(event, _):
    try:
        await bot.unpin_message(event.chat_id)
        await safe_edit(event, "🤖 Unpinned")
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:80]}")


# ─── GC PFP helper ───
async def _apply_gc_pfp(chat_id) -> bool:
    for base in (DATA_DIR, BASE_DIR):
        p = base / "gc_pfp.jpg"
        if p.exists():
            try:
                up = await bot.upload_file(str(p))
                await bot(EditPhotoRequest(
                    channel=chat_id,
                    photo=InputChatUploadedPhoto(file=up)))
                return True
            except Exception as e:
                LOG.warning(f"gc pfp local fail: {e}")
    url = os.environ.get("RICH_GC_PFP_URL", "").strip()
    if url:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and r.content:
                path = TMP_DIR / f"gc_pfp_{int(time.time())}.jpg"
                path.write_bytes(r.content)
                up = await bot.upload_file(str(path))
                await bot(EditPhotoRequest(
                    channel=chat_id,
                    photo=InputChatUploadedPhoto(file=up)))
                try: path.unlink()
                except Exception: pass
                return True
        except Exception as e:
            LOG.warning(f"gc pfp url fail: {e}")
    return False


@register_cmd("setgcpfp", needs_reply=True)
async def cmd_setgcpfp(event, arg):
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await safe_edit(event,
            "🤖 Reply to a photo/image with `.setgcpfp`\n"
            "▸ saved as default GC profile photo\n"
            "▸ used automatically on every `.gc`")
    await safe_edit(event, "🤖 saving GC PFP...")
    try:
        raw = await reply.download_media(file=bytes)
        if not raw: return await safe_edit(event, "download failed")
        target = DATA_DIR / "gc_pfp.jpg"
        target.write_bytes(raw)
        try: (BASE_DIR / "gc_pfp.jpg").write_bytes(raw)
        except Exception: pass
        await safe_edit(event,
            f"🤖 GC PFP saved\n▸ {target}\n▸ {len(raw)} bytes\n"
            f"▸ now .gc will use it")
    except Exception as e:
        await safe_edit(event, f"🤖 failed: {str(e)[:80]}")


@register_cmd("gc")
async def cmd_gc(event, arg):
    title = (arg or "").strip()[:128] or GC_DEFAULT_TITLE
    await safe_edit(event, "🤖 Creating GC...")
    try:
        result = await bot(CreateChannelRequest(title=title, about="", megagroup=True))
        new_chat = result.chats[0]
        link = None
        try:
            inv = await bot(ExportChatInviteRequest(peer=new_chat.id))
            link = getattr(inv, "link", None)
        except Exception: pass

        pfp_ok = await _apply_gc_pfp(new_chat.id)

        mm_msg = None
        try:
            mm_msg = await bot.send_message(new_chat.id, GC_MM_TEMPLATE)
            try:
                await bot.pin_message(new_chat.id, mm_msg.id, notify=True)
            except Exception: pass
        except Exception as e:
            LOG.warning(f"mm msg fail: {e}")

        await safe_delete(event)
        conf = (f"🤖 ɢᴄ ᴄʀᴇᴀᴛᴇᴅ 🤖\n\n"
                f"  ▸ ɴᴀᴍᴇ : {title}\n"
                f"  ▸ ɪᴅ   : `{new_chat.id}`\n"
                f"  ▸ ᴘғᴘ  : {'✅ set' if pfp_ok else '❌ set RICH_GC_PFP_URL or .setgcpfp'}\n"
                f"  ▸ ᴛᴇʀᴍꜱ : {'✅ pinned' if mm_msg else '❌'}")
        if link: conf += f"\n  ▸ ʟɪɴᴋ : {link}"
        try: await bot.send_message(event.chat_id, conf)
        except Exception: pass
    except FloodWaitError as fw:
        await safe_edit(event, f"🤖 Flood {fw.seconds}s")
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:100]}")

@register_cmd("leave")
async def cmd_leave(event, _):
    try:
        await safe_delete(event)
        await bot(LeaveChannelRequest(channel=event.chat_id))
    except Exception:
        try: await bot.delete_dialog(event.chat_id)
        except Exception: pass

@register_cmd("mute", needs_reply=True)
async def cmd_mute(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    chat_id = event.chat_id
    muted_users.setdefault(chat_id, set())
    added, already = [], []
    for uid in targets:
        if uid in muted_users[chat_id]: already.append(str(uid))
        else: muted_users[chat_id].add(uid); added.append(str(uid))
    persist()
    parts = []
    if added:   parts.append(f"🤖 muted: {', '.join(added)}")
    if already: parts.append(f"🤖 already: {', '.join(already)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("unmute", needs_reply=True)
async def cmd_unmute(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    chat_id = event.chat_id
    removed, not_muted = [], []
    for uid in targets:
        if chat_id in muted_users and uid in muted_users[chat_id]:
            muted_users[chat_id].discard(uid); removed.append(str(uid))
        else: not_muted.append(str(uid))
    if chat_id in muted_users and not muted_users[chat_id]:
        muted_users.pop(chat_id, None)
    persist()
    parts = []
    if removed:   parts.append(f"🤖 unmuted: {', '.join(removed)}")
    if not_muted: parts.append(f"🤖 wasn't muted: {', '.join(not_muted)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("gmute", needs_reply=True)
async def cmd_gmute(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    added, already = [], []
    for uid in targets:
        if uid in global_muted: already.append(str(uid))
        else: global_muted.add(uid); added.append(str(uid))
    persist()
    parts = []
    if added:   parts.append(f"🤖 global muted: {', '.join(added)}")
    if already: parts.append(f"🤖 already: {', '.join(already)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("gunmute", needs_reply=True)
async def cmd_gunmute(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    removed, not_muted = [], []
    for uid in targets:
        if uid in global_muted: global_muted.discard(uid); removed.append(str(uid))
        else: not_muted.append(str(uid))
    persist()
    parts = []
    if removed:   parts.append(f"🤖 globally unmuted: {', '.join(removed)}")
    if not_muted: parts.append(f"🤖 wasn't muted: {', '.join(not_muted)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

@register_cmd("mutelist")
async def cmd_mutelist(event, _):
    txt = "🤖━━━ mute panel ━━━🤖\n\n"
    local = muted_users.get(event.chat_id, set())
    txt += "🤖 this chat:\n"
    if local:
        for uid in list(local): txt += f"▸ `{uid}`\n"
    else: txt += "▸ none\n"
    txt += "\n🤖 global:\n"
    if global_muted:
        for uid in list(global_muted): txt += f"▸ `{uid}`\n"
    else: txt += "▸ none\n"
    txt += "\n🤖 locked gc:\n"
    if group_locks:
        for gid in list(group_locks): txt += f"▸ `{gid}`\n"
    else: txt += "▸ none\n"
    txt += "\n🤖 muted gc (mutegc):\n"
    if muted_gcs:
        for gid in list(muted_gcs): txt += f"▸ `{gid}`\n"
    else: txt += "▸ none\n"
    await safe_edit(event, txt)

@register_cmd("mutegc")
async def cmd_mutegc(event, arg):
    arg = (arg or "").strip().lower()
    chat_id = event.chat_id
    if not event.is_group:
        return await safe_edit(event, "Group only")
    if arg == "on":
        if chat_id in muted_gcs:
            return await safe_edit(event, "🤖 This GC is already muted")
        muted_gcs.add(chat_id); persist()
        return await safe_edit(event,
            "🔇 WHOLE GC MUTED\n"
            "▸ every non-owner message auto-deletes\n"
            "▸ admins can also talk (safe)\n"
            "▸ stop with `.mutegc off`")
    elif arg == "off":
        if chat_id not in muted_gcs:
            return await safe_edit(event, "🤖 This GC is not muted")
        muted_gcs.discard(chat_id); persist()
        return await safe_edit(event, "🔊 GC unmuted")
    else:
        state = chat_id in muted_gcs
        return await safe_edit(event,
            f"🤖 GC mute: {'ON' if state else 'OFF'}\n"
            f"▸ use: `.mutegc on` / `.mutegc off`")

@register_cmd("lock", group_only=True)
async def cmd_lock(event, _):
    try:
        perms = await bot.get_permissions(event.chat_id, "me")
        if not perms.is_admin: return await safe_edit(event, "Need admin rights")
    except Exception:
        return await safe_edit(event, "Permission check failed")
    if event.chat_id in group_locks: return await safe_edit(event, "Already locked")
    group_locks.add(event.chat_id); persist()
    await safe_edit(event, "🤖 Group locked")

@register_cmd("unlock", group_only=True)
async def cmd_unlock(event, _):
    try:
        perms = await bot.get_permissions(event.chat_id, "me")
        if not perms.is_admin: return await safe_edit(event, "Need admin rights")
    except Exception:
        return await safe_edit(event, "Permission check failed")
    if event.chat_id not in group_locks: return await safe_edit(event, "Not locked")
    group_locks.discard(event.chat_id); persist()
    await safe_edit(event, "🤖 Group unlocked")

@register_cmd("purge")
async def cmd_purge(event, arg):
    try: count = int(arg) if arg else 50
    except Exception: count = 50
    count = max(1, min(count, 300))
    await safe_edit(event, "Purging...")
    ids = []
    try:
        async for m in bot.iter_messages(event.chat_id, limit=count + 1):
            ids.append(m.id)
    except Exception: pass
    if not ids: return await safe_edit(event, "No messages")
    try: await bot.delete_messages(event.chat_id, ids)
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 1)
        try: await bot.delete_messages(event.chat_id, ids)
        except Exception: pass
    except Exception: pass
    await safe_edit(event, f"Purged: {max(len(ids) - 1, 0)}")

@register_cmd("throw", needs_reply=True, group_only=True)
async def cmd_throw(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / id")
    try:
        perms = await bot.get_permissions(event.chat_id, "me")
        if not perms.is_admin: return await safe_edit(event, "Need admin rights")
    except Exception:
        return await safe_edit(event, "Permission check failed")
    kicked, failed, skipped = [], [], []
    me = await bot.get_me()
    for uid in targets:
        if uid == me.id: skipped.append(str(uid)); continue
        try:
            await bot.kick_participant(event.chat_id, uid); kicked.append(str(uid))
        except Exception: failed.append(str(uid))
    parts = []
    if kicked:  parts.append(f"kicked: {', '.join(kicked)}")
    if failed:  parts.append(f"failed: {', '.join(failed)}")
    if skipped: parts.append(f"skipped: {', '.join(skipped)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

async def _toggle_target(event, arg, target_set: Set[int], label: str):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply or @user / ID")
    added, already = [], []
    for uid in targets:
        if uid in target_set: already.append(str(uid))
        else: target_set.add(uid); added.append(str(uid))
    persist()
    parts = []
    if added:   parts.append(f"{label} on: {', '.join(added)}")
    if already: parts.append(f"already: {', '.join(already)}")
    await safe_edit(event, "\n".join(parts) or "No changes")

async def _stop_target(event, arg, target_set: Set[int], label: str):
    targets = await get_targets(event, arg)
    if targets:
        stopped, na = [], []
        for uid in targets:
            if uid in target_set: target_set.discard(uid); stopped.append(str(uid))
            else: na.append(str(uid))
        parts = []
        if stopped: parts.append(f"stopped: {', '.join(stopped)}")
        if na:      parts.append(f"not active: {', '.join(na)}")
        txt = "\n".join(parts) or "No changes"
    else:
        target_set.clear()
        txt = f"{label} off (all)"
    persist()
    await safe_edit(event, txt)

@register_cmd("reply",       needs_reply=True)
async def cmd_reply(event, arg):       await _toggle_target(event, arg, reply_users,       "reply raid")
@register_cmd("rr",          needs_reply=True)
async def cmd_rr(event, arg):          await _toggle_target(event, arg, rr_users,          "rr")
@register_cmd("flag",        needs_reply=True)
async def cmd_flag(event, arg):        await _toggle_target(event, arg, flag_users,        "flag")
@register_cmd("replyshadow", needs_reply=True)
async def cmd_replyshadow(event, arg): await _toggle_target(event, arg, replyshadow_users, "shadow")
@register_cmd("sreply")
async def cmd_sreply(event, arg):      await _stop_target(event, arg, reply_users,       "reply raid")
@register_cmd("srr")
async def cmd_srr(event, arg):         await _stop_target(event, arg, rr_users,          "rr")
@register_cmd("sflag")
async def cmd_sflag(event, arg):       await _stop_target(event, arg, flag_users,        "flag")
@register_cmd("sshadow")
async def cmd_sshadow(event, arg):     await _stop_target(event, arg, replyshadow_users, "shadow")

async def _raid_worker(chat, reply_to_id: int, chat_id: int):
    try:
        while True:
            for msg in RAID_MESSAGES:
                if chat_id not in raid_tasks: return
                try: await bot.send_message(chat, msg, reply_to=reply_to_id)
                except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 1)
                except asyncio.CancelledError: raise
                except Exception: await asyncio.sleep(0.3)
                await asyncio.sleep(ST.data["config"]["raid_delay"])
    except asyncio.CancelledError: return
    finally: raid_tasks.pop(chat_id, None)

@register_cmd("raid", needs_reply=True, group_only=True)
async def cmd_raid(event, arg):
    chat_id = event.chat_id
    if chat_id in raid_tasks and not raid_tasks[chat_id].done():
        return await safe_edit(event, "Raid already active")
    reply_msg = await event.get_reply_message()
    try: chat = await event.get_input_chat()
    except Exception: chat = chat_id
    await safe_delete(event)
    raid_tasks[chat_id] = asyncio.create_task(_raid_worker(chat, reply_msg.id, chat_id))

@register_cmd("sraid")
async def cmd_sraid(event, _):
    task = raid_tasks.get(event.chat_id)
    if not task or task.done(): return await safe_edit(event, "No raid running")
    task.cancel(); raid_tasks.pop(event.chat_id, None)
    await safe_edit(event, "🤖 Raid stopped")

async def _swipe_worker(chat_id: int, reply_to_id: int):
    try:
        for msg in SWIPE_MESSAGES:
            if chat_id not in swipe_tasks: return
            try: await bot.send_message(chat_id, msg, reply_to=reply_to_id)
            except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 1)
            except asyncio.CancelledError: raise
            except Exception: await asyncio.sleep(0.3)
            await asyncio.sleep(0.25)
    except asyncio.CancelledError: return
    finally: swipe_tasks.pop(chat_id, None)

@register_cmd("swipe", needs_reply=True, group_only=True)
async def cmd_swipe(event, arg):
    chat_id = event.chat_id
    if chat_id in swipe_tasks and not swipe_tasks[chat_id].done():
        return await safe_edit(event, "Swipe already running")
    reply_msg = await event.get_reply_message()
    await safe_delete(event)
    swipe_tasks[chat_id] = asyncio.create_task(_swipe_worker(chat_id, reply_msg.id))

@register_cmd("sswipe")
async def cmd_sswipe(event, _):
    task = swipe_tasks.get(event.chat_id)
    if not task or task.done(): return await safe_edit(event, "No swipe running")
    task.cancel(); swipe_tasks.pop(event.chat_id, None)
    await safe_edit(event, "🤖 Swipe stopped")

@register_cmd("spray")
async def cmd_spray(event, arg):
    if not arg: return await safe_edit(event, "Use: .spray <text>")
    chat = event.chat_id
    arg = arg[:4000]
    if chat in spray_tasks and not spray_tasks[chat].done():
        return await safe_edit(event, "Spray already active")
    async def loop():
        try:
            while True:
                if chat not in spray_tasks: break
                try: await bot.send_message(chat, arg)
                except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 1)
                except asyncio.CancelledError: raise
                except RPCError: break
                except Exception: await asyncio.sleep(0.3)
                await asyncio.sleep(ST.data["config"]["spray_delay"])
        except asyncio.CancelledError: pass
        finally: spray_tasks.pop(chat, None)
    spray_tasks[chat] = asyncio.create_task(loop())
    await safe_edit(event, f"Spray started\n{arg[:40]}")

@register_cmd("dspray")
async def cmd_dspray(event, _):
    chat = event.chat_id
    task = spray_tasks.get(chat)
    if not task: return await safe_edit(event, "No spray active")
    task.cancel(); spray_tasks.pop(chat, None)
    await safe_edit(event, "Spray stopped")

async def _fast_title(chat_id, title: str) -> bool:
    t = (title or "").strip()[:255]
    if not t: return False
    try:
        await bot(EditTitleRequest(channel=chat_id, title=t)); return True
    except Exception:
        try:
            await bot(EditChatTitleRequest(chat_id=chat_id, title=t)); return True
        except Exception: return False

async def _fastgc_loop(chat_id: int):
    try:
        while FASTGC_STATE.get("active"):
            tpl = FASTGC_STATE.get("template")
            if not tpl: break
            new = tpl.replace("{emoji}", random.choice(FASTGC_EMOJIS))
            ok = await _fast_title(chat_id, new)
            await asyncio.sleep(GC_FAST_INTERVAL if ok else 1.0)
    except asyncio.CancelledError: return
    except Exception as e: LOG.warning(f"fastgc loop: {e}")

@register_cmd("fastgc")
async def cmd_fastgc(event, arg):
    if not arg: return await safe_edit(event, "Use: .fastgc set <template {emoji}> | .fastgc stop")
    arg = arg.strip()
    if arg.startswith("set "):
        tpl = arg[4:].strip()
        if "{emoji}" not in tpl: return await safe_edit(event, "Template must contain {emoji}")
        FASTGC_STATE["active"]   = True
        FASTGC_STATE["template"] = tpl
        FASTGC_STATE["chat_id"]  = event.chat_id
        old = FASTGC_STATE.get("task")
        if old and not old.done(): old.cancel()
        FASTGC_STATE["task"] = asyncio.create_task(_fastgc_loop(event.chat_id))
        return await safe_edit(event, "FastGC started")
    if arg == "stop":
        FASTGC_STATE["active"]   = False
        FASTGC_STATE["template"] = None
        old = FASTGC_STATE.get("task")
        if old: old.cancel()
        FASTGC_STATE["task"] = None
        return await safe_edit(event, "FastGC stopped")
    return await safe_edit(event, "Use: .fastgc set <template {emoji}> | .fastgc stop")

@register_cmd("ar")
async def cmd_ar(event, arg):
    global auto_react_emoji
    if not arg: return await safe_edit(event, "Use: .ar <emoji>")
    emoji = arg.strip()
    if len(emoji) > 8: return await safe_edit(event, "Emoji too long")
    auto_react_emoji = emoji; persist()
    await safe_edit(event, f"Auto-react on ➤ {emoji}")

@register_cmd("sar")
async def cmd_sar(event, _):
    global auto_react_emoji
    if auto_react_emoji is None: return await safe_edit(event, "Not active")
    auto_react_emoji = None; persist()
    await safe_edit(event, "Auto-react off")

@register_cmd("snipe")
async def cmd_snipe(event, arg):
    chat_id = event.chat_id
    arg = (arg or "").strip().lower()
    if arg == "clear":
        snipe_cache.pop(chat_id, None)
        return await safe_edit(event, "🤖 Snipe cache cleared")
    limit = 1
    if arg.isdigit(): limit = max(1, min(int(arg), 10))
    cache = snipe_cache.get(chat_id, [])
    if not cache: return await safe_edit(event, "🤖 No deleted messages yet")
    lines = []
    for e in cache[-limit:]:
        lines.append(f"🤖 {e.get('name','?')}\n{e.get('text','')[:250]}")
    txt = "🤖 ꜱɴɪᴘᴇᴅ 🤖\n\n" + "\n\n━━━━━━━━━━\n\n".join(lines)
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)

@register_cmd("edit")
async def cmd_edit(event, arg):
    chat_id = event.chat_id
    limit = 1
    if arg and arg.strip().isdigit():
        limit = max(1, min(int(arg.strip()), 10))
    cache = edit_cache.get(chat_id, [])
    if not cache: return await safe_edit(event, "🤖 No edited messages yet")
    lines = []
    for e in cache[-limit:]:
        lines.append(f"🤖 {e.get('name','?')}\n· was: {e.get('old','')[:200]}\n· now: {e.get('new','')[:200]}")
    txt = "🤖 ᴇᴅɪᴛᴇᴅ 🤖\n\n" + "\n\n━━━━━━━━━━\n\n".join(lines)
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)

@register_cmd("seen")
async def cmd_seen(event, arg):
    targets = await get_targets(event, arg)
    if not targets: return await safe_edit(event, "Reply / @user / ID")
    uid = next(iter(targets))
    cache = snipe_msgs.get(event.chat_id, {})
    hits = [v for v in cache.values() if v.get("sender") == uid]
    if not hits: return await safe_edit(event, "No recent messages from that user")
    last = max(hits, key=lambda x: x["time"])
    dt = int(now() - last["time"])
    txt = (f"🤖 ʟᴀꜱᴛ ꜱᴇᴇɴ\n▸ user : {last.get('name', uid)}\n"
           f"▸ ago  : {human_uptime(dt)}\n▸ text : {last.get('text','')[:300]}")
    await safe_edit(event, txt)


# ---------- yt-dlp + music ----------
def _ydl_youtube_opts(base: dict) -> dict:
    opts = dict(base)
    for ck_dir in (DATA_DIR, BASE_DIR):
        ck = ck_dir / "cookies.txt"
        if ck.exists():
            opts["cookiefile"] = str(ck)
            break
    for cf_dir in (DATA_DIR, BASE_DIR):
        cf = cf_dir / "cookies_from_browser.txt"
        if cf.exists():
            try:
                br = cf.read_text(encoding="utf-8").strip().lower()
                if br in ("chrome", "edge", "firefox", "brave", "opera", "chromium"):
                    opts["cookiesfrombrowser"] = (br,)
            except Exception: pass
            break
    return opts


def _yt_dlp_try(query: str, out_dir: str):
    import glob
    client_chains = [
        ["tv"], ["mweb"], ["ios"], ["android"], ["web_safari"],
        ["tv_embedded"], ["web_creator"],
        ["android", "web_safari"], ["ios", "web"],
        ["android", "ios", "web_safari"],
    ]
    attempts = []
    for clients in client_chains:
        attempts.append({
            "extractor_args": {"youtube": {
                "player_client": clients,
                "player_skip": ["webpage"],
            }},
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        })
    attempts.append({
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "format": "bestaudio/best",
    })
    attempts.append({"format": "best"})

    last_err = None
    for i, strat in enumerate(attempts):
        opts = _ydl_youtube_opts({
            "outtmpl": os.path.join(out_dir, "vn_%(id)s.%(ext)s"),
            "quiet": True, "no_warnings": True,
            "default_search": "ytsearch1",
            "noplaylist": True, "retries": 2,
            "socket_timeout": 20,
            "nocheckcertificate": True,
            "merge_output_format": "mp4",
            "geo_bypass": True,
            **strat,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=True)
            if not info: continue
            if "entries" in info:
                entries = info.get("entries") or []
                if not entries: continue
                info = entries[0]
            vid = info.get("id")
            files = glob.glob(os.path.join(out_dir, f"vn_{vid}.*"))
            files = [f for f in files
                     if not f.endswith((".part", ".ytdl", ".webp", ".jpg", ".png", ".jpeg"))]
            if files:
                LOG.info(f"yt-dlp #{i} ok clients={strat.get('extractor_args')}")
                return files[0], vid, info
        except Exception as e:
            last_err = str(e)[:120]
            LOG.warning(f"yt-dlp #{i} failed: {last_err}")
            continue
    LOG.warning(f"all yt-dlp failed — last: {last_err}")
    return None, None, None


def _invidious_fallback(query: str, out_dir: str):
    instances = [
        "https://inv.nadeko.net",
        "https://yewtu.be",
        "https://invidious.nerdvpn.de",
        "https://inv.tux.pizza",
    ]
    try:
        r = requests.get(
            f"https://invidious.nerdvpn.de/api/v1/search?q={requests.utils.quote(query)}&type=video",
            timeout=10).json()
        if not r: return None, None, None
        vid = r[0].get("videoId")
        title = r[0].get("title") or query
        if not vid: return None, None, None
    except Exception:
        return None, None, None
    for inst in instances:
        try:
            url = f"{inst}/latest_version?id={vid}&itag=22"
            resp = requests.get(url, timeout=30, stream=True)
            if resp.status_code != 200: continue
            path = os.path.join(out_dir, f"vn_{vid}.mp4")
            with open(path, "wb") as f:
                for chunk in resp.iter_content(64 * 1024):
                    if chunk: f.write(chunk)
            if os.path.getsize(path) > 10_000:
                return path, vid, {"id": vid, "title": title}
        except Exception:
            continue
    return None, None, None


def _extract_voice_ogg(video_path: str, out_ogg: str) -> bool:
    if not _ffmpeg_ok(): return False
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-c:a", "libopus", "-b:a", "64k",
            "-ar", "48000", "-ac", "1",
            out_ogg,
        ]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        return r.returncode == 0 and os.path.isfile(out_ogg) and os.path.getsize(out_ogg) > 0
    except Exception as e:
        LOG.warning(f"ffmpeg extract failed: {e}")
        return False


@register_cmd("dl")
async def cmd_dl(event, arg):
    if not arg: return await safe_edit(event, "Use: .dl <url>")
    url = arg.strip()
    if not url.startswith(("http://", "https://")): url = "https://" + url
    await safe_edit(event, "🤖 Downloading...")
    async def dl_task():
        try:
            import glob
            loop = asyncio.get_event_loop()
            base = os.path.join(str(DL_DIR), f"dl_{event.id}_{int(time.time())}")
            opts = _ydl_youtube_opts({
                "format": "best[filesize<80M]/best",
                "outtmpl": f"{base}.%(ext)s",
                "quiet": True, "no_warnings": True,
                "nocheckcertificate": True, "retries": 5,
                "noplaylist": False, "ignoreerrors": True,
                "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
            })
            def run():
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=True)
            info = await loop.run_in_executor(None, run)
            files = [f for f in glob.glob(f"{base}.*") if not f.endswith((".part", ".ytdl"))]
            if not files: return await safe_edit(event, "🤖 Download failed")
            caption = f"🤖 {BRAND} 🤖"
            if isinstance(info, dict):
                title = info.get("title") or ""
                if title: caption += f"\n\n{title[:150]}"
            sent = 0
            for f in files:
                try:
                    await bot.send_file(event.chat_id, f, caption=caption if sent == 0 else "")
                    sent += 1
                except Exception: pass
            for f in files:
                try: os.remove(f)
                except Exception: pass
            await safe_delete(event)
        except Exception as e:
            LOG.warning(f"dl: {e}")
            await safe_edit(event, f"🤖 {str(e)[:120]}")
    asyncio.create_task(dl_task())


@register_cmd("music")
async def cmd_music(event, arg):
    if not arg: return await safe_edit(event, "Use: .music [a|v] <song>")
    arg = arg.strip()
    mode = "both"
    if arg.startswith("a "):
        mode = "audio"; arg = arg[2:].strip()
    elif arg.startswith("v "):
        mode = "video"; arg = arg[2:].strip()
    query = arg
    if not query: return await safe_edit(event, "Use: .music [a|v] <song>")

    await safe_edit(event, f"🎵 Searching: {query}")
    async def dl_task():
        try:
            loop = asyncio.get_event_loop()
            out_dir = str(TMP_DIR)
            path, vid, info = await loop.run_in_executor(None, _yt_dlp_try, query, out_dir)
            if not path:
                await safe_edit(event, "🎵 yt-dlp blocked — trying invidious...")
                path, vid, info = await loop.run_in_executor(None, _invidious_fallback, query, out_dir)
            if not path:
                has_ck = (DATA_DIR / "cookies.txt").exists() or (BASE_DIR / "cookies.txt").exists()
                return await safe_edit(event,
                    "🤖 Music failed on all methods.\n"
                    f"▸ cookies : {'installed' if has_ck else 'MISSING'}\n"
                    + ("▸ cookies present but YT still blocked — refresh cookies\n"
                       if has_ck else
                       "▸ send cookies.txt → reply `.setcookies`\n")
                    + "▸ run `.musicdebug <song>` for details")

            raw_ttl = (info.get("title") if isinstance(info, dict) else None) or query
            clean = re.sub(r"[^\w\s-]", "", raw_ttl).strip()[:60] or "audio"
            dur = int((info.get("duration") if isinstance(info, dict) else 0) or 0)
            uploader = (info.get("uploader") if isinstance(info, dict) else None) or BRAND

            await safe_edit(event, f"🎵 Sending: {clean}")

            if mode in ("both", "video"):
                try:
                    await bot.send_file(
                        event.chat_id, path,
                        caption=f"🎬 {clean}\n👤 {uploader}\n⏱ {dur//60}:{dur%60:02d}\n✦ {BRAND} ✦",
                        supports_streaming=True,
                    )
                    await asyncio.sleep(0.4)
                except Exception as e:
                    LOG.warning(f"video send failed: {e}")

            if mode in ("both", "audio"):
                ogg = os.path.join(out_dir, f"vn_{vid or int(time.time())}.ogg")
                ok = await loop.run_in_executor(None, _extract_voice_ogg, path, ogg)
                if ok:
                    try:
                        with open(ogg, "rb") as fh:
                            data = BytesIO(fh.read())
                        data.name = f"{clean}.ogg"
                        await bot.send_file(
                            event.chat_id, data,
                            voice_note=True,
                            caption=f"🎵 {clean}\n👤 {uploader}\n✦ {BRAND} ✦",
                            attributes=[DocumentAttributeAudio(
                                duration=dur, title=clean, performer=uploader, voice=True,
                            )],
                        )
                    except Exception as e:
                        LOG.warning(f"voice send failed: {e}")
                    finally:
                        try: os.remove(ogg)
                        except Exception: pass
                else:
                    if mode == "audio":
                        await safe_edit(event, "🤖 ffmpeg missing — voice note needs ffmpeg")

            try: os.remove(path)
            except Exception: pass
            await safe_delete(event)
        except Exception as e:
            LOG.warning(f"music: {e}")
            await safe_edit(event, f"Music error: {str(e)[:100]}")
    asyncio.create_task(dl_task())


@register_cmd("musicdebug")
async def cmd_musicdebug(event, arg):
    if not arg: return await safe_edit(event, "Use: .musicdebug <song>")
    query = arg.strip()
    await safe_edit(event, f"🤖 probing: {query}...")
    def _probe():
        opts = _ydl_youtube_opts({
            "format": "bestaudio/best",
            "quiet": True, "no_warnings": True,
            "skip_download": True,
            "default_search": "ytsearch1",
            "socket_timeout": 15,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
            if "entries" in info: info = info["entries"][0]
            return "OK", info.get("id"), (info.get("title") or "")[:80]
        except Exception as e:
            return "ERR", type(e).__name__, str(e)[:400]
    loop = asyncio.get_event_loop()
    status, a, b = await loop.run_in_executor(None, _probe)
    ck = DATA_DIR / "cookies.txt"
    ck_ok = ck.exists()
    ck_size = ck.stat().st_size if ck_ok else 0
    txt = (f"🤖 music debug 🤖\n"
           f"▸ cookies : {'yes' if ck_ok else 'NO'} ({ck_size}B)\n"
           f"▸ yt-dlp  : {yt_dlp.version.__version__}\n"
           f"▸ status  : {status}\n"
           f"▸ info    : {a}\n"
           f"▸ detail  : {b}")
    await safe_edit(event, txt)


@register_cmd("setcookies", needs_reply=True)
async def cmd_setcookies(event, arg):
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await safe_edit(event,
            "🤖 Reply to a `cookies.txt` file with `.setcookies`\n"
            "▸ Export YouTube cookies via 'Get cookies.txt LOCALLY' extension\n"
            "▸ Send the .txt file here, reply with `.setcookies`")
    fname = getattr(reply.file, "name", "") or ""
    if not fname.lower().endswith(".txt"):
        return await safe_edit(event, "🤖 file must end with .txt")
    await safe_edit(event, "🤖 installing cookies...")
    try:
        raw = await reply.download_media(file=bytes)
        if not raw or len(raw) < 50:
            return await safe_edit(event, "🤖 file too small / empty")
        head = raw[:300].decode("utf-8", "ignore").lower()
        if "\t" not in head and "youtube" not in head and "google" not in head:
            return await safe_edit(event,
                "🤖 not a cookies file\n"
                "▸ use 'Get cookies.txt LOCALLY' extension (Netscape format)")
        target = DATA_DIR / "cookies.txt"
        target.write_bytes(raw)
        try: (BASE_DIR / "cookies.txt").write_bytes(raw)
        except Exception: pass
        return await safe_edit(event,
            f"🤖 cookies installed ✅\n"
            f"▸ saved : `{target}`\n"
            f"▸ size  : {len(raw)} bytes\n"
            f"▸ now run `.music <song>`\n"
            f"▸ if still fails, run `.restart` first")
    except Exception as e:
        return await safe_edit(event, f"🤖 install failed: {str(e)[:100]}")


@register_cmd("cookies")
async def cmd_cookies(event, _):
    paths = [DATA_DIR / "cookies.txt", BASE_DIR / "cookies.txt"]
    lines = []
    for p in paths:
        if p.exists():
            size = p.stat().st_size
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))
            lines.append(f"▸ ✅ `{p}` — {size}B · {mtime}")
        else:
            lines.append(f"▸ ❌ `{p}`")
    txt = "🤖 cookies status 🤖\n" + "\n".join(lines)
    txt += "\n\n▸ install: send a cookies.txt → reply `.setcookies`"
    await safe_edit(event, txt)


@register_cmd("voicegirl")
async def cmd_voicegirl(event, arg):
    if not arg:
        return await safe_edit(event, "Use: .voicegirl <text>  or  .voicegirl <voice> | <text>")
    if not _EDGE_OK:
        return await safe_edit(event, "🤖 edge-tts missing. Run: pip install edge-tts")
    voice = GIRL_VOICES[0]; text = arg
    if "|" in arg:
        head, rest = arg.split("|", 1)
        head = head.strip()
        if head: voice = head; text = rest.strip()
    if not text: return await safe_edit(event, "text required")
    await safe_edit(event, f"🤖 girl voice [{voice}]...")
    await _tts_send(event, text[:3000], voice)


@register_cmd("voiceboy")
async def cmd_voiceboy(event, arg):
    if not arg:
        return await safe_edit(event, "Use: .voiceboy <text>  or  .voiceboy <voice> | <text>")
    if not _EDGE_OK:
        return await safe_edit(event, "🤖 edge-tts missing. Run: pip install edge-tts")
    voice = BOY_VOICES[0]; text = arg
    if "|" in arg:
        head, rest = arg.split("|", 1)
        head = head.strip()
        if head: voice = head; text = rest.strip()
    if not text: return await safe_edit(event, "text required")
    await safe_edit(event, f"🤖 boy voice [{voice}]...")
    await _tts_send(event, text[:3000], voice)


@register_cmd("voice")
async def cmd_voice(event, arg):
    if not arg: return await safe_edit(event, "Use: .voice <text>")
    if not _EDGE_OK:
        return await safe_edit(event, "🤖 edge-tts missing. Run: pip install edge-tts")
    await safe_edit(event, "🤖 voice...")
    await _tts_send(event, arg[:3000], GIRL_VOICES[0])


@register_cmd("weather")
async def cmd_weather(event, arg):
    if not arg: return await safe_edit(event, "Use: .weather <city>")
    await safe_edit(event, "Fetching...")
    try:
        geo = requests.get(
            f"https://geocoding-api.open-meteo.com/v1/search?name={arg}&count=1",
            timeout=8).json()
    except Exception:
        return await safe_edit(event, "Geocode failed")
    if not geo.get("results"): return await safe_edit(event, "City not found")
    r = geo["results"][0]
    city = r.get("name"); country = r.get("country", ""); admin = r.get("admin1", "")
    try:
        w = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={r['latitude']}"
            f"&longitude={r['longitude']}&current_weather=true",
            timeout=8).json()
    except Exception:
        return await safe_edit(event, "Weather fetch failed")
    cw = w.get("current_weather")
    if not cw: return await safe_edit(event, "No data")
    temp = cw.get("temperature"); wind = cw.get("windspeed")
    code = cw.get("weathercode", 0)
    cond_map = {
        0: "Clear Sky",
        **{c: "Partly Cloudy" for c in (1, 2, 3)},
        **{c: "Foggy" for c in (45, 48)},
        **{c: "Drizzle" for c in (51, 53, 55, 56, 57)},
        **{c: "Rainy" for c in (61, 63, 65, 66, 67)},
        **{c: "Snowy" for c in (71, 73, 75, 77)},
        **{c: "Rain Showers" for c in (80, 81, 82)},
        **{c: "Snow Showers" for c in (85, 86)},
        **{c: "Thunderstorm" for c in (95, 96, 99)},
    }
    cond = cond_map.get(code, "Unknown")
    loc = city
    if admin: loc += f", {admin}"
    if country: loc += f", {country}"
    txt = (f"🤖 weather 🤖\n\n▸ location  : {loc}\n▸ condition : {cond}\n"
           f"▸ temp      : {temp}°C\n▸ wind      : {wind} km/h")
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)

@register_cmd("copy")
async def cmd_copy(event, args):
    global CLONE_DATA, CLONE_ACTIVE, LAST_CLONE_ID
    reply = await event.get_reply_message()
    target = None
    if reply:
        try:
            if reply.sender_id: target = await bot.get_entity(reply.sender_id)
        except Exception: pass
        if not target and getattr(reply, "fwd_from", None):
            try:
                fid = reply.fwd_from.from_id
                if fid: target = await bot.get_entity(fid)
            except Exception: pass
    if not target and args:
        try: target = await bot.get_entity(args.strip())
        except Exception: pass
    if not target: return await safe_edit(event, "Reply / @user / ID")
    me = await bot.get_me()
    if target.id == me.id: return await safe_edit(event, "Cannot clone self")
    if CLONE_ACTIVE and LAST_CLONE_ID == target.id:
        return await safe_edit(event, "Already cloned")
    await safe_edit(event, "Cloning...")
    if not CLONE_ACTIVE:
        try:
            full = await bot(GetFullUserRequest(me.id))
            CLONE_DATA["name"] = me.first_name
            CLONE_DATA["last"] = me.last_name
            CLONE_DATA["bio"]  = full.full_user.about
            try:
                dp = await bot.download_profile_photo("me", file=bytes, download_big=True)
                if dp:
                    b = BytesIO(dp); b.name = "orig.jpg"
                    CLONE_DATA["photo_bytes"] = b
                else: CLONE_DATA["photo_bytes"] = None
            except Exception: CLONE_DATA["photo_bytes"] = None
            CLONE_ACTIVE = True
        except Exception: pass
    try:
        await bot(UpdateProfileRequest(first_name=target.first_name or "",
                                       last_name=target.last_name or ""))
    except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 1)
    except Exception: pass
    try:
        tfull = await bot(GetFullUserRequest(target.id))
        b = (tfull.full_user.about or "").encode("utf-16", "surrogatepass")\
                                          .decode("utf-16", "ignore").strip()[:70]
        await bot(UpdateProfileRequest(about=""))
        await asyncio.sleep(0.4)
        await bot(UpdateProfileRequest(about=b))
    except Exception: pass
    try:
        f = await bot.download_profile_photo(target, file=bytes, download_big=True)
        if f:
            b = BytesIO(f); b.name = "clone.jpg"
            up = await bot.upload_file(b)
            try:
                cur = await bot.get_profile_photos("me", limit=1)
                if cur: await bot(DeletePhotosRequest(id=[cur[0]]))
            except Exception: pass
            await bot(UploadProfilePhotoRequest(file=up))
    except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 1)
    except Exception: pass
    LAST_CLONE_ID = target.id
    await safe_edit(event, "Clone complete")

@register_cmd("normal")
async def cmd_normal(event, _):
    global CLONE_DATA, CLONE_ACTIVE, LAST_CLONE_ID
    if not CLONE_ACTIVE: return await safe_edit(event, "No clone active")
    try:
        await bot(UpdateProfileRequest(first_name=CLONE_DATA.get("name") or "",
                                       last_name=CLONE_DATA.get("last") or ""))
    except Exception: pass
    try:
        await bot(UpdateProfileRequest(about=""))
        await asyncio.sleep(0.4)
        await bot(UpdateProfileRequest(about=CLONE_DATA.get("bio") or ""))
    except Exception: pass
    try:
        cur = await bot.get_profile_photos("me", limit=1)
        if cur: await bot(DeletePhotosRequest(id=[cur[0]]))
        if CLONE_DATA.get("photo_bytes"):
            b = CLONE_DATA["photo_bytes"]; b.name = "restore.jpg"
            up = await bot.upload_file(b)
            await bot(UploadProfilePhotoRequest(file=up))
    except Exception: pass
    CLONE_ACTIVE = False; LAST_CLONE_ID = None
    CLONE_DATA = {"name": None, "last": None, "bio": None, "photo_bytes": None}
    await safe_edit(event, "Original restored")

@register_cmd("ping")
async def cmd_ping(event, _):
    t0 = time.perf_counter()
    await safe_edit(event, "🤖 ping...")
    t1 = time.perf_counter()
    await safe_edit(event, f"🤖 pong ➤ `{round((t1 - t0) * 1000)} ms`")

@register_cmd("status")
async def cmd_status(event, _):
    uptime = max(0, int(now() - START_TIME))
    local_muted = sum(len(s) for s in muted_users.values())
    txt = (f"🤖 status 🤖\n▸ uptime : {human_uptime(uptime)}\n"
           f"▸ admins : {len(admins)}\n"
           f"▸ muted  : {local_muted} local / {len(global_muted)} global\n"
           f"▸ raids  : {len(raid_tasks)}\n▸ sprays : {len(spray_tasks)}\n"
           f"▸ swipes : {len(swipe_tasks)}\n▸ mode   : operational\n"
           f"▸ clear  : {'ON' if ST.data.get('auto_clear_titles') else 'OFF'}\n"
           f"▸ mutegc : {len(muted_gcs)} GCs\n"
           f"▸ fee    : {FEE_RATE:g}% · min ₹{FEE_MIN:,.0f}")
    await safe_edit(event, txt)

@register_cmd("stats")
async def cmd_stats(event, _):
    me = await bot.get_me()
    uptime = max(0, int(now() - START_TIME))
    txt = (f"🤖 stats 🤖\n▸ account : {me.first_name} (@{me.username or 'none'})\n"
           f"▸ user id : `{me.id}`\n▸ uptime  : {human_uptime(uptime)}\n"
           f"▸ cmds    : {len(commands)}\n▸ chats   : {len(snipe_msgs)} cached\n"
           f"▸ snipes  : {sum(len(v) for v in snipe_cache.values())}\n"
           f"▸ edits   : {sum(len(v) for v in edit_cache.values())}")
    await safe_edit(event, txt)

@register_cmd("health")
async def cmd_health(event, _):
    results = []
    t0 = time.perf_counter()
    try:
        await bot.get_me(); tg = round((time.perf_counter() - t0) * 1000)
        results.append(f"▸ telegram : `{tg} ms`")
    except Exception as e:
        results.append(f"▸ telegram : FAIL ({str(e)[:40]})")
    try:
        import yt_dlp as _y
        results.append(f"▸ yt-dlp   : {_y.version.__version__}")
    except Exception:
        results.append("▸ yt-dlp   : FAIL")
    results.append(f"▸ edge-tts : {'yes' if _EDGE_OK else 'NO (pip install edge-tts)'}")
    results.append(f"▸ ffmpeg   : {'yes' if _ffmpeg_ok() else 'NO'}")
    ck = DATA_DIR / "cookies.txt"
    results.append(f"▸ cookies  : {'yes' if ck.exists() else 'no'}")
    pfp = (DATA_DIR / "gc_pfp.jpg").exists() or (BASE_DIR / "gc_pfp.jpg").exists()
    results.append(f"▸ gc pfp   : {'yes' if pfp else 'no (use .setgcpfp)'}")
    results.append(f"▸ fee rate : {FEE_RATE:g}% · min ₹{FEE_MIN:,.0f}")
    results.append(f"▸ data dir : {DATA_DIR}")
    txt = "🤖 health 🤖\n" + "\n".join(results)
    await safe_edit(event, txt)

@register_cmd("backup")
async def cmd_backup(event, _):
    try:
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("rich_state.json",
                       json.dumps(ST.data, ensure_ascii=False, indent=2))
        buf.seek(0)
        buf.name = f"rich_backup_{int(now())}.zip"
        await bot.send_file(event.chat_id, buf, caption=f"🤖 {BRAND} backup 🤖")
    except Exception as e:
        await safe_edit(event, f"Backup failed: {str(e)[:80]}")

@register_cmd("restore", needs_reply=True)
async def cmd_restore(event, _):
    reply = await event.get_reply_message()
    if not reply or not reply.media: return await safe_edit(event, "Reply to a backup zip")
    try:
        import zipfile
        raw = await reply.download_media(file=bytes)
        data = None
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if "rich_state.json" in names:
                data = json.loads(z.read("rich_state.json").decode("utf-8"))
            elif "shadow_state.json" in names:
                data = json.loads(z.read("shadow_state.json").decode("utf-8"))
        if not data: raise Exception("no state in zip")
        ST.data = {**_DEFAULT_STATE, **data}
        ST.save()
        global admins, global_muted, group_locks, muted_users, muted_gcs
        global reply_users, rr_users, flag_users, replyshadow_users
        global menu_banner_msg, auto_react_emoji
        admins = set(int(x) for x in ST.data.get("admins", []))
        global_muted = set(int(x) for x in ST.data.get("gmutes", []))
        group_locks  = set(int(x) for x in ST.data.get("locks", []))
        muted_gcs    = set(int(x) for x in ST.data.get("muted_gcs", []))
        muted_users  = {int(k): set(int(u) for u in v)
                        for k, v in ST.data.get("mutes", {}).items()}
        reply_users       = set(int(x) for x in ST.data.get("reply_targets", []))
        rr_users          = set(int(x) for x in ST.data.get("rr_targets", []))
        flag_users        = set(int(x) for x in ST.data.get("flag_targets", []))
        replyshadow_users = set(int(x) for x in ST.data.get("shadow_targets", []))
        menu_banner_msg = tuple(ST.data["banner"]) if ST.data.get("banner") else None
        auto_react_emoji = ST.data.get("auto_react")
        await safe_edit(event, "🤖 Restored")
    except Exception as e:
        await safe_edit(event, f"Restore failed: {str(e)[:80]}")

@register_cmd("reload")
async def cmd_reload(event, _):
    ST.load()
    global admins, global_muted, group_locks, muted_users, muted_gcs
    global reply_users, rr_users, flag_users, replyshadow_users
    global menu_banner_msg, auto_react_emoji
    admins = set(int(x) for x in ST.data.get("admins", []))
    global_muted = set(int(x) for x in ST.data.get("gmutes", []))
    group_locks  = set(int(x) for x in ST.data.get("locks", []))
    muted_gcs    = set(int(x) for x in ST.data.get("muted_gcs", []))
    muted_users  = {int(k): set(int(u) for u in v)
                    for k, v in ST.data.get("mutes", {}).items()}
    reply_users       = set(int(x) for x in ST.data.get("reply_targets", []))
    rr_users          = set(int(x) for x in ST.data.get("rr_targets", []))
    flag_users        = set(int(x) for x in ST.data.get("flag_targets", []))
    replyshadow_users = set(int(x) for x in ST.data.get("shadow_targets", []))
    menu_banner_msg = tuple(ST.data["banner"]) if ST.data.get("banner") else None
    auto_react_emoji = ST.data.get("auto_react")
    await safe_edit(event, "🤖 Reloaded from disk")

@register_cmd("restart")
async def cmd_restart(event, _):
    await safe_edit(event, "🤖 restarting userbot process...")
    await asyncio.sleep(1.0)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await safe_edit(event, f"🤖 restart failed: {e}\n▸ manually: kill + rerun")

@register_cmd("debug")
async def cmd_debug(event, arg):
    arg = (arg or "").strip().lower()
    if arg not in ("on", "off"):
        return await safe_edit(event, f"🤖 debug is {'on' if ST.data['config'].get('debug') else 'off'}")
    ST.data["config"]["debug"] = (arg == "on"); ST.save()
    await safe_edit(event, f"🤖 debug {arg}")

@register_cmd("banner", needs_reply=True)
async def cmd_banner(event, _):
    global menu_banner_msg
    reply = await event.get_reply_message()
    if not reply or not reply.media: return await safe_edit(event, "Reply to a photo/video")
    await safe_edit(event, "Saving...")
    try:
        try: saved = await reply.forward_to("me")
        except Exception:
            raw = await reply.download_media(file=bytes)
            if not raw: return await safe_edit(event, "Download failed")
            bio = BytesIO(raw); bio.name = "banner"
            saved = await bot.send_file("me", bio)
        menu_banner_msg = (saved.chat_id, saved.id); persist()
        await safe_edit(event, f"Banner set id: {saved.id}")
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:80]}")

@register_cmd("rembanner")
async def cmd_rembanner(event, _):
    global menu_banner_msg
    if not menu_banner_msg: return await safe_edit(event, "No banner set")
    c, m = menu_banner_msg
    try:
        try: await bot.delete_messages(c, [m])
        except Exception: pass
        menu_banner_msg = None; persist()
        await safe_edit(event, "Banner removed")
    except Exception as e:
        await safe_edit(event, f"🤖 {str(e)[:80]}")

@register_cmd("stopall")
async def cmd_stopall(event, _):
    global auto_react_emoji
    stopped = []
    for name, tasks in (("raid", raid_tasks), ("spray", spray_tasks), ("swipe", swipe_tasks)):
        for c in list(tasks.keys()):
            try: tasks[c].cancel()
            except Exception: pass
            tasks.pop(c, None)
        if tasks: stopped.append(name)
    if reply_users:       reply_users.clear();       stopped.append("reply")
    if rr_users:          rr_users.clear();          stopped.append("rr")
    if flag_users:        flag_users.clear();        stopped.append("flag")
    if replyshadow_users: replyshadow_users.clear(); stopped.append("shadow")
    if auto_react_emoji:  auto_react_emoji = None;   stopped.append("ar")
    if group_locks:       group_locks.clear();       stopped.append("locks")
    if muted_users:       muted_users.clear();       stopped.append("mutes")
    if global_muted:      global_muted.clear();      stopped.append("gmutes")
    if muted_gcs:         muted_gcs.clear();         stopped.append("mutegc")
    if FASTGC_STATE.get("active"):
        FASTGC_STATE["active"] = False
        FASTGC_STATE["template"] = None
        t = FASTGC_STATE.get("task")
        if t: t.cancel()
        FASTGC_STATE["task"] = None
        stopped.append("fastgc")
    persist()
    txt = ("🤖 Everything stopped\n" + ", ".join(stopped)) if stopped else "🤖 Nothing was active"
    await safe_edit(event, txt)

@register_cmd("clear")
async def cmd_clear(event, arg):
    arg = (arg or "").strip().lower()
    if arg == "on":    state = True
    elif arg == "off": state = False
    elif arg == "stats":
        return await safe_edit(event,
            f"🤖 nuke stats\n"
            f"▸ hits    : {_nuke_stats['hits']}\n"
            f"▸ deleted : {_nuke_stats['deleted']}\n"
            f"▸ calls   : {_nuke_stats['calls']}\n"
            f"▸ fails   : {_nuke_stats['fails']}")
    else:              state = not ST.data.get("auto_clear_titles", False)
    ST.data["auto_clear_titles"] = state
    ST.save()
    await safe_edit(event,
        f"🤖 auto-clear service msgs ➤ {'ON' if state else 'OFF'}\n"
        f"▸ scope: chat title + photo change notifications\n"
        f"▸ mode : instant delete via raw update hook\n"
        f"▸ stats: {_nuke_stats['hits']} hits · {_nuke_stats['deleted']} deleted · "
        f"{_nuke_stats['calls']} calls")

@register_cmd("setpostpic", needs_reply=True)
async def cmd_setpostpic(event, arg):
    reply = await event.get_reply_message()
    if not reply or not reply.media:
        return await safe_edit(event, "🤖 Reply to the scammer alert image with `.setpostpic`")
    await safe_edit(event, "🤖 saving post image...")
    try:
        raw = await reply.download_media(file=bytes)
        if not raw: return await safe_edit(event, "download failed")
        target = DATA_DIR / "scammer_alert.jpg"
        target.write_bytes(raw)
        await safe_edit(event, "🤖 Scammer alert image saved successfully.")
    except Exception as e:
        await safe_edit(event, f"🤖 failed: {str(e)[:80]}")

@register_cmd("post")
async def cmd_post(event, arg):
    arg = (arg or "").strip()
    if not arg:
        return await safe_edit(event, "Use: `.post @username <channel_link>`\nOr reply to a user: `.post <channel_link>`")

    parts = arg.split()
    target = None
    link = None

    if len(parts) == 1 and event.is_reply:
        link = parts[0]
        r = await event.get_reply_message()
        target = await bot.get_entity(r.sender_id)
    elif len(parts) >= 2:
        target_str = parts[0]
        link = parts[1]
        if not link.startswith(("http://", "https://", "tg://")):
            if link.startswith("@"):
                link = "https://t.me/" + link[1:]
            else:
                link = "https://" + link
        try:
            if target_str.lstrip("-").isdigit():
                target = await bot.get_entity(int(target_str))
            else:
                target = await bot.get_entity(target_str)
        except Exception:
            pass
    else:
        return await safe_edit(event, "Invalid format.")

    if not target:
        return await safe_edit(event, "Could not find user.")

    username = f"@{target.username}" if getattr(target, "username", None) else "Unknown"
    uid = target.id

    text = f"❌ User {username} (`{uid}`) has been marked as a scammer."

    profile_link = f"tg://user?id={uid}"
    buttons = [
        types.KeyboardButtonRow([
            types.KeyboardButtonUrl(text="👤 View Profile", url=profile_link)
        ]),
        types.KeyboardButtonRow([
            types.KeyboardButtonUrl(text="📄 View Full Report", url=link)
        ])
    ]
    markup = types.ReplyInlineMarkup(buttons)

    img_path = DATA_DIR / "scammer_alert.jpg"
    
    try:
        await event.delete()
    except Exception:
        pass

    try:
        if img_path.exists():
            await bot.send_file(
                event.chat_id,
                str(img_path),
                caption=text,
                buttons=markup,
                parse_mode="md"
            )
        else:
            await bot.send_message(
                event.chat_id,
                text,
                buttons=markup,
                parse_mode="md"
            )
    except Exception as e:
        await event.reply(f"🤖 Post failed: {e}")

@register_cmd("heart")
async def cmd_heart(event, arg):
    name = (arg or "").strip()
    hearts = ["❤️","🧡","💛","💚","💙","💜","🖤","🤍","🤎","🩷","🩵","🩶","💖","💘","💝","💗"]
    shape = [
        "  HH     HH  ",
        " HHHH   HHHH ",
        "HHHHHHHHHHHHH",
    ]
    bottom = [
        "HHHHHHHHHHHHH",
        " HHHHHHHHHHH ",
        "  HHHHHHHHH  ",
        "   HHHHHHH   ",
        "    HHHHH    ",
        "     HHH     ",
        "      H      ",
    ]
    def paint(r): return "".join(random.choice(hearts) if c == "H" else " " for c in r)
    out = [paint(r) for r in shape]
    if name:
        out.append(f"{paint('HHHH')} {name[:14]} {paint('HHHH')}")
    else:
        out.append(paint("HHHHHHHHHHHHH"))
    out += [paint(r) for r in bottom]
    txt = "\n".join(out)
    if event.out: await safe_edit(event, txt)
    else: await event.reply(txt)

@register_cmd("flip")
async def cmd_flip(event, _):
    await safe_edit(event, f"🤖 flip ➤ {random.choice(['heads', 'tails'])}")

@register_cmd("dice")
async def cmd_dice(event, _):
    await safe_edit(event, f"🤖 dice ➤ {random.randint(1, 6)}")

@register_cmd("info")
async def cmd_info(event, arg):
    target = None
    if event.is_reply:
        try:
            r = await event.get_reply_message()
            if r and r.sender_id: target = r.sender_id
        except Exception: pass
    elif arg:
        try:
            ent = await bot.get_entity(arg); target = ent.id
        except Exception:
            return await safe_edit(event, "Could not find user")
    if not target: return await safe_edit(event, "Reply or give @username / id")
    try:
        user = await bot.get_entity(target)
        if getattr(user, "deleted", False): return await safe_edit(event, "Deleted account")
        full = await bot(GetFullUserRequest(user.id))
        bio = full.full_user.about or "no bio"
        un = f"@{user.username}" if getattr(user, "username", None) else "no username"
        txt = (f"🤖 user info\n\n▸ id       ➤ `{user.id}`\n"
               f"▸ name     ➤ {user.first_name or ''} {user.last_name or ''}\n"
               f"▸ username ➤ {un}\n▸ bio      ➤ {bio}")
        await safe_edit(event, txt)
    except Exception as e:
        await safe_edit(event, f"Error: {str(e)[:60]}")


# ==================================================================
#  SESSION MANAGEMENT COMMANDS
# ==================================================================

@register_cmd("kill")
async def cmd_kill(event, arg):
    if not arg:
        return await safe_edit(event, "Use: `.kill <hash>`\n▸ hashes can be sourced from the admin panel")
    arg = arg.strip().replace("#", "")
    
    try:
        result = await bot(GetAuthorizationsRequest())
        auths = result.authorizations
    except Exception as e:
        return await safe_edit(event, f"🤖 error fetching sessions: {str(e)[:120]}")

    target_hash = None
    target_info = ""

    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(auths):
            auth = auths[idx]
            if auth.current:
                return await safe_edit(event, "🤖 Cannot kill your CURRENT session.")
            target_hash = auth.hash
            target_info = f"#{arg} ({auth.device_model} | {auth.ip})"
        else:
            return await safe_edit(event, "🤖 Invalid session number.")
    else:
        target_hash = arg
        target_info = f"hash `{arg}`"

    if not target_hash:
        return await safe_edit(event, "🤖 target not found.")

    try:
        await bot(ResetAuthorizationRequest(hash=int(target_hash)))
        await safe_edit(event, 
            f"🤖 ꜱᴇꜱꜱɪᴏɴ ᴋɪʟʟᴇᴅ ✅\n"
            f"▸ target: {target_info}\n"
            f"▸ use `.sessions` to verify.")
    except Exception as e:
        await safe_edit(event, f"🤖 error: {str(e)[:120]}")


# ---------- nuke system ----------
_nuke_stats = {"hits": 0, "deleted": 0, "calls": 0, "fails": 0}

_SERVICE_ACTIONS = (
    types.MessageActionChatEditTitle,
    types.MessageActionChatEditPhoto,
    types.MessageActionChatDeletePhoto,
)

async def _nuke_delete(peer_id, msg_id):
    try:
        await bot.delete_messages(peer_id, [msg_id])
        _nuke_stats["deleted"] += 1
        _nuke_stats["calls"]   += 1
    except FloodWaitError as fw:
        await asyncio.sleep(min(fw.seconds, 2.0))
        try:
            await bot.delete_messages(peer_id, [msg_id])
            _nuke_stats["deleted"] += 1
            _nuke_stats["calls"]   += 1
        except Exception as e:
            _nuke_stats["fails"] += 1
            LOG.warning(f"nuke retry fail: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        _nuke_stats["fails"] += 1
        LOG.warning(f"nuke fail: {type(e).__name__}: {str(e)[:120]}")


# ---------- userbot event handlers ----------
if bot is not None:
    @bot.on(events.NewMessage)
    async def on_new_incoming(event):
        if event.out: return
        if not event.raw_text: return
        chat_id = event.chat_id
        sender = event.sender_id
        if not sender: return
        name = str(sender)
        try:
            s = await event.get_sender()
            if s:
                name = getattr(s, "first_name", None) or getattr(s, "username", None) or str(sender)
        except Exception: pass
        snipe_msgs.setdefault(chat_id, {})[event.id] = {
            "sender": sender, "name": name,
            "text": event.raw_text, "time": now(),
        }
        if len(snipe_msgs[chat_id]) > MSG_CACHE_LIMIT:
            for old in sorted(snipe_msgs[chat_id].keys())[:60]:
                snipe_msgs[chat_id].pop(old, None)

    @bot.on(events.MessageDeleted)
    async def on_message_deleted(event):
        chat_id = event.chat_id
        if not chat_id: return
        snipe_cache.setdefault(chat_id, [])
        for mid in event.deleted_ids:
            data = snipe_msgs.get(chat_id, {}).pop(mid, None)
            if data: snipe_cache[chat_id].append(data)
        if len(snipe_cache[chat_id]) > SNIPE_LIMIT:
            snipe_cache[chat_id] = snipe_cache[chat_id][-SNIPE_LIMIT:]

    @bot.on(events.MessageEdited)
    async def on_message_edited(event):
        if event.out: return
        if not event.raw_text: return
        chat_id = event.chat_id
        sender = event.sender_id
        if not sender: return
        prev = snipe_msgs.get(chat_id, {}).get(event.id)
        if not prev: return
        old_text = prev.get("text", "")
        if old_text == event.raw_text: return
        name = prev.get("name", str(sender))
        edit_cache.setdefault(chat_id, []).append({
            "sender": sender, "name": name,
            "old": old_text, "new": event.raw_text, "time": now(),
        })
        if len(edit_cache[chat_id]) > EDIT_LIMIT:
            edit_cache[chat_id] = edit_cache[chat_id][-EDIT_LIMIT:]
        snipe_msgs[chat_id][event.id]["text"] = event.raw_text

    @bot.on(events.NewMessage)
    async def on_mutegc_handler(event):
        if event.out: return
        chat = event.chat_id
        if chat not in muted_gcs: return
        sender = event.sender_id
        if not sender: return
        if is_admin(sender): return
        try:
            await event.delete()
        except FloodWaitError as fw:
            await asyncio.sleep(min(fw.seconds, 2.0))
            try: await event.delete()
            except Exception: pass
        except Exception: pass

    @bot.on(events.NewMessage)
    async def on_auto_handler(event):
        if event.out: return
        sender = event.sender_id
        chat = event.chat_id
        if not sender: return
        if is_admin(sender): return
        if is_user_muted(chat, sender):
            await safe_delete(event); return
        if chat in group_locks and not is_admin(sender):
            await safe_delete(event); return
        try:
            if sender in reply_users:
                try: await event.reply(random.choice(reply_list))
                except Exception: pass
            if sender in replyshadow_users:
                try:
                    for _ in range(3):
                        await event.reply(random.choice(reply_texts))
                        await asyncio.sleep(0.15)
                except Exception: pass
            if sender in flag_users:
                try: await event.reply(random.choice(flag_texts))
                except Exception: pass
            if sender in rr_users:
                try:
                    bm = await event.reply(random.choice(fun_texts))
                    try:
                        await bot(SendReactionRequest(
                            peer=chat, msg_id=bm.id,
                            reaction=[ReactionEmoji(emoticon="🤖")]))
                    except Exception: pass
                except Exception: pass
        except Exception: pass

    @bot.on(events.NewMessage(outgoing=True))
    async def on_auto_react(event):
        e = auto_react_emoji
        if not e: return
        if not event.id or not event.chat_id: return
        try:
            await bot(SendReactionRequest(
                peer=event.chat_id, msg_id=event.id,
                reaction=[ReactionEmoji(emoticon=e)]))
        except Exception: pass

    @bot.on(events.Raw(types=[
        types.UpdateNewChannelMessage,
        types.UpdateNewMessage,
        types.UpdateEditChannelMessage,
        types.UpdateEditMessage,
    ]))
    async def on_service_clear(event):
        if not ST.data.get("auto_clear_titles"):
            return
        msg = getattr(event, "message", None)
        if msg is None or not isinstance(msg, types.MessageService):
            return
        action = getattr(msg, "action", None)
        if not isinstance(action, _SERVICE_ACTIONS):
            return
        _nuke_stats["hits"] += 1
        asyncio.create_task(_nuke_delete(msg.peer_id, msg.id))

    @bot.on(events.NewMessage(outgoing=True))
    async def on_command(event):
        text = event.raw_text
        if not text or not text.startswith("."): return
        body = text[1:].strip()
        if not body: return
        parts = body.split(maxsplit=1)
        cmd = parts[0].lower().strip()
        arg = parts[1].strip() if len(parts) > 1 else ""
        meta = commands.get(cmd)
        if not meta: return
        if meta["group_only"] and not event.is_group:
            return await safe_edit(event, "Group only")
        if meta["needs_reply"] and not event.is_reply and not arg:
            return await safe_edit(event, f"Reply or argument for .{cmd}")
        cd = meta["cooldown"] if meta["cooldown"] is not None else ST.data["config"]["cooldown"]
        if not check_cooldown(event.sender_id or 0, cmd, cd):
            return
        await run_with_flood_retry(lambda: meta["func"](event, arg), event, cmd)


_shutdown_done = False

async def _graceful_shutdown():
    global _shutdown_done
    if _shutdown_done: return
    _shutdown_done = True
    LOG.info("shutting down userbot ...")
    for tmap in (raid_tasks, spray_tasks, swipe_tasks):
        for c in list(tmap.keys()):
            try: tmap[c].cancel()
            except Exception: pass
            tmap.pop(c, None)
    t = FASTGC_STATE.get("task")
    if t: t.cancel()
    persist()
    try:
        if bot and bot.is_connected(): await bot.disconnect()
    except Exception: pass


async def userbot_main():
    if not bot:
        print("missing RICH_API_ID / RICH_API_HASH env")
        return
    try:
        await bot.start(phone=PHONE)
        me = await bot.get_me()
        un = f"@{me.username}" if me.username else "NoUsername"
        print("=" * 52)
        print(f"  🤖  {BRAND}  🤖")
        print(f"  logged in : {me.first_name} ({un})")
        print(f"  user id   : {me.id}")
        print(f"  session   : {SESSION}.session")
        print(f"  data dir  : {DATA_DIR}")
        print(f"  edge-tts  : {'on' if _EDGE_OK else 'OFF — pip install edge-tts'}")
        print(f"  ffmpeg    : {'on' if _ffmpeg_ok() else 'OFF'}")
        print(f"  fee rate  : {FEE_RATE:g}% · min ₹{FEE_MIN:,.0f}")
        print("=" * 52)
        LOG.info(f"userbot online · {len(commands)} commands")
        try:
            await bot.run_until_disconnected()
        except (ConnectionError, OSError) as e:
            LOG.warning(f"connection dropped: {e}")
    except KeyboardInterrupt:
        LOG.info("interrupted")
    except Exception as e:
        LOG.error(f"startup error: {e}\n{traceback.format_exc()}")
    finally:
        await _graceful_shutdown()


# ==================================================================
#  SECTION 2 — ONBOARDING BOT
# ==================================================================

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

CODE_LEN = 5
ASK_API_ID, ASK_API_HASH, ASK_PHONE, ASK_CODE, ASK_PASSWORD = range(5)

ADMIN_IDS      = {8929130604, 8846085944}
ADMIN_ID       = 8929130604
APPROVAL_FILE  = BASE_DIR / "approved_users.json"
PENDING_FILE   = BASE_DIR / "pending_users.json"


def _load_id_set(path: Path) -> set:
    try:
        return set(int(x) for x in json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_id_set(path: Path, s: set) -> None:
    try:
        path.write_text(json.dumps(sorted(int(x) for x in s)), encoding="utf-8")
    except Exception as e:
        print(f"save {path.name} failed: {e}")


APPROVED_USERS: set = _load_id_set(APPROVAL_FILE)
PENDING_USERS:  set = _load_id_set(PENDING_FILE)
APPROVED_USERS.update(ADMIN_IDS)

_onboard_state: dict[int, dict] = {}
_running: dict[int, dict] = {}


def _cfg_path(chat_id: int) -> Path:
    return USERS_DIR / f"user_{chat_id}.json"

def _session_path(chat_id: int) -> Path:
    return SESS_DIR / f"session_{chat_id}"

def _state_path_for(chat_id: int) -> Path:
    return RUN_DIR / f"state_{chat_id}.json"


async def _track(bot_app, chat_id: int, text: str, **kw):
    m = await bot_app.send_message(chat_id, text, **kw)
    st = _onboard_state.setdefault(chat_id, {"bot_msg_ids": []})
    st.setdefault("bot_msg_ids", []).append(m.message_id)
    return m


async def _wipe_tracked(bot_app, chat_id: int, st: dict):
    ids = list(st.get("bot_msg_ids") or [])
    st["bot_msg_ids"] = []
    for mid in ids:
        try: await bot_app.delete_message(chat_id, mid)
        except Exception: pass


async def _kill_running(chat_id: int, do_logout: bool = True):
    r = _running.pop(chat_id, None)
    if not r: return
    proc = r.get("proc")
    if proc and proc.poll() is None:
        try: proc.terminate()
        except Exception: pass
        try: proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
    if do_logout:
        await asyncio.sleep(0.5)
        await _logout_via_session(chat_id)


async def _logout_via_session(chat_id: int):
    cfg_file = _cfg_path(chat_id)
    if not cfg_file.exists(): return
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return
    try:
        c = TelegramClient(cfg["session"], int(cfg["api_id"]), cfg["api_hash"])
        await c.connect()
        if await c.is_user_authorized():
            try: await c.log_out()
            except Exception:
                try: await c.disconnect()
                except Exception: pass
        else:
            try: await c.disconnect()
            except Exception: pass
    except Exception:
        pass


async def _close_telethon(chat_id: int):
    st = _onboard_state.get(chat_id)
    if not st: return
    c = st.get("client")
    if c:
        try: await c.disconnect()
        except Exception: pass
        st["client"] = None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user

    if chat_id not in ADMIN_IDS and chat_id not in APPROVED_USERS:
        if chat_id not in PENDING_USERS:
            PENDING_USERS.add(chat_id)
            _save_id_set(PENDING_FILE, PENDING_USERS)
            name  = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or "unknown"
            uname = f"@{user.username}" if user.username else "no username"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"appr:{chat_id}"),
                InlineKeyboardButton("❌ Reject",  callback_data=f"rej:{chat_id}"),
            ]])
            for admin in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin,
                        "🔔 ɴᴇᴡ ᴀᴄᴄᴇꜱꜱ ʀᴇQᴜᴇꜱᴛ\n\n"
                        f"▸ name     : {name}\n"
                        f"▸ username : {uname}\n"
                        f"▸ user id  : `{chat_id}`",
                        reply_markup=kb,
                    )
                except Exception as e:
                    print(f"admin notify failed: {e}")

        await context.bot.send_message(
            chat_id,
            "⏳ Aapki request admin ko bhej di gayi hai.\n"
            "Approve hone ke baad /start dobara bhejein.",
        )
        return ConversationHandler.END

    await _kill_running(chat_id, do_logout=True)
    await _close_telethon(chat_id)
    _onboard_state[chat_id] = {"bot_msg_ids": []}
    await _track(
        context.bot, chat_id,
        "Welcome.\n\nStep 1/4 — API ID\n\n"
        "Get API ID and API Hash from https://my.telegram.org/apps\n"
        "Login, open 'API development tools', copy the numbers.\n\n"
        "Send your API ID (numbers only).",
    )
    return ASK_API_ID


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = _onboard_state.pop(chat_id, None) or {}
    await _close_telethon(chat_id)
    try: await _wipe_tracked(context.bot, chat_id, st)
    except Exception: pass
    await context.bot.send_message(chat_id, "Cancelled. Send /start to begin again.")
    return ConversationHandler.END


async def got_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except Exception: pass
    txt = (update.message.text or "").strip()
    if not txt.isdigit() or not (5 <= len(txt) <= 12):
        await _track(context.bot, chat_id, "API ID must be numeric. Send again.")
        return ASK_API_ID
    st = _onboard_state.setdefault(chat_id, {"bot_msg_ids": []})
    st["api_id"] = int(txt)
    await _track(context.bot, chat_id,
                 "Saved.\n\nStep 2/4 — API Hash\n\nSend your 32-character API Hash.")
    return ASK_API_HASH


async def got_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except Exception: pass
    txt = (update.message.text or "").strip()
    if len(txt) != 32 or any(c not in "0123456789abcdefABCDEF" for c in txt):
        await _track(context.bot, chat_id, "API Hash must be 32 hex characters. Send again.")
        return ASK_API_HASH
    st = _onboard_state.setdefault(chat_id, {"bot_msg_ids": []})
    st["api_hash"] = txt
    await _track(context.bot, chat_id,
                 "Saved.\n\nStep 3/4 — Phone number\n\n"
                 "Send in international format with leading +.\n"
                 "Example: +919999999999")
    return ASK_PHONE


async def got_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except Exception: pass
    raw = (update.message.text or "").strip().replace(" ", "").replace("-", "")
    if not (raw.startswith("+") and raw[1:].isdigit() and 7 <= len(raw) <= 16):
        await _track(context.bot, chat_id, "Invalid. Send like +919999999999")
        return ASK_PHONE
    st = _onboard_state.setdefault(chat_id, {"bot_msg_ids": []})
    api_id, api_hash = st.get("api_id"), st.get("api_hash")
    if not api_id or not api_hash:
        await _track(context.bot, chat_id, "Session lost. Send /start.")
        return ConversationHandler.END
    await _track(context.bot, chat_id, "Connecting to Telegram ...")
    client = TelegramClient(str(_session_path(chat_id)), api_id, api_hash)
    try:
        await client.connect()
    except Exception as e:
        await _track(context.bot, chat_id, f"Connect failed: {e}")
        return ConversationHandler.END
    if await client.is_user_authorized():
        st["client"] = client
        st["phone"]  = raw
        return await _finalize(update, context, chat_id)
    try:
        sent = await client.send_code_request(raw)
    except FloodWaitError as fw:
        await _track(context.bot, chat_id, f"Telegram flood-wait {fw.seconds}s. Try /start later.")
        try: await client.disconnect()
        except Exception: pass
        return ConversationHandler.END
    except ApiIdInvalidError:
        await _track(context.bot, chat_id, "API ID / Hash rejected by Telegram. Send /start.")
        try: await client.disconnect()
        except Exception: pass
        return ConversationHandler.END
    except Exception as e:
        await _track(context.bot, chat_id, f"send_code_request failed: {e}")
        try: await client.disconnect()
        except Exception: pass
        return ConversationHandler.END
    st["client"] = client
    st["phone"]  = raw
    st["hash"]   = sent.phone_code_hash
    st["digits"] = []
    await _track(
        context.bot, chat_id,
        "Code sent to your Telegram app.\n\n"
        "Step 4/4 — Enter the 5-digit code, ONE DIGIT AT A TIME.\n"
        "Example: send `1`, then `2`, then `3`, then `4`, then `5`.\n\n"
        "digit 1/5 :",
    )
    return ASK_CODE


async def got_code_digit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except Exception: pass
    st = _onboard_state.get(chat_id)
    if not st or not st.get("client"):
        await context.bot.send_message(chat_id, "Session lost. Send /start.")
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    digits_in = [c for c in txt if c.isdigit()]
    if not digits_in:
        await _track(context.bot, chat_id,
                     f"Send a digit (0-9). digit {len(st['digits'])+1}/{CODE_LEN} :")
        return ASK_CODE
    st["digits"].extend(digits_in)
    if len(st["digits"]) > CODE_LEN:
        st["digits"] = st["digits"][:CODE_LEN]
    if len(st["digits"]) < CODE_LEN:
        await _track(context.bot, chat_id, f"digit {len(st['digits'])+1}/{CODE_LEN} :")
        return ASK_CODE
    code = "".join(st["digits"])
    client = st["client"]
    try:
        await client.sign_in(phone=st["phone"], code=code, phone_code_hash=st["hash"])
    except SessionPasswordNeededError:
        st["digits"] = []
        await _track(context.bot, chat_id,
                     "2FA password required.\n\nSend your cloud password now.\n"
                     "The message you send will be deleted automatically.")
        return ASK_PASSWORD
    except PhoneCodeInvalidError:
        st["digits"] = []
        await _track(context.bot, chat_id, "Invalid code. Restarting entry.\ndigit 1/5 :")
        return ASK_CODE
    except PhoneCodeExpiredError:
        st["digits"] = []
        try:
            sent = await client.send_code_request(st["phone"])
            st["hash"] = sent.phone_code_hash
        except Exception as e:
            await _track(context.bot, chat_id, f"Resend failed: {e}")
            return ConversationHandler.END
        await _track(context.bot, chat_id, "Code expired. A new code was sent.\ndigit 1/5 :")
        return ASK_CODE
    except FloodWaitError as fw:
        await _track(context.bot, chat_id, f"Flood-wait {fw.seconds}s during sign-in.")
        return ConversationHandler.END
    except Exception as e:
        await _track(context.bot, chat_id, f"sign_in failed: {e}")
        return ConversationHandler.END
    return await _finalize(update, context, chat_id)


async def got_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except Exception: pass
    st = _onboard_state.get(chat_id)
    if not st or not st.get("client"):
        return ConversationHandler.END
    client = st["client"]
    pwd = (update.message.text or "").strip()
    try:
        await client.sign_in(password=pwd)
    except PasswordHashInvalidError:
        await _track(context.bot, chat_id, "Wrong password. Try again.")
        return ASK_PASSWORD
    except FloodWaitError as fw:
        await _track(context.bot, chat_id, f"Flood-wait {fw.seconds}s.")
        return ConversationHandler.END
    except Exception as e:
        await _track(context.bot, chat_id, f"2FA failed: {e}")
        return ConversationHandler.END
    return await _finalize(update, context, chat_id)


async def _finalize(update, context, chat_id: int):
    st = _onboard_state.get(chat_id) or {}
    client = st.get("client")
    if client is None:
        return ConversationHandler.END
    me = await client.get_me()
    cfg = {
        "api_id":   st["api_id"],
        "api_hash": st["api_hash"],
        "phone":    st["phone"],
        "user_id":  me.id,
        "session":  str(_session_path(chat_id)),
        "state":    str(_state_path_for(chat_id)),
    }
    cp = _cfg_path(chat_id)
    cp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    bot_me = await context.bot.get_me()
    await asyncio.sleep(0.4)
    try:
        ids = []
        async for m in client.iter_messages(bot_me.id, limit=200):
            ids.append(m.id)
        for i in range(0, len(ids), 80):
            try: await client.delete_messages(bot_me.id, ids[i:i + 80])
            except Exception: pass
            await asyncio.sleep(0.2)
    except Exception:
        pass

    try: await client.disconnect()
    except Exception: pass
    st["client"] = None

    env = os.environ.copy()
    env["RICH_API_ID"]   = str(cfg["api_id"])
    env["RICH_API_HASH"] = cfg["api_hash"]
    env["RICH_OWNER_ID"] = str(cfg["user_id"])
    env["RICH_PHONE"]    = cfg["phone"]
    env["RICH_SESSION"]  = cfg["session"]
    env["RICH_STATE"]    = cfg["state"]
    env["RICH_DATA_DIR"] = str(DATA_DIR)

    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--userbot", str(chat_id)],
        cwd=str(BASE_DIR),
        env=env,
    )
    _running[chat_id] = {"proc": proc, "config": str(cp), "user_id": me.id}

    try: await _wipe_tracked(context.bot, chat_id, st)
    except Exception: pass

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Click here for successfully logout", callback_data="logout")]]
    )
    await context.bot.send_message(
        chat_id,
        "✅ Login successful.\n\n"
        "Your userbot is now running 24/7.\n"
        "Try `.menu` in any chat.\n\n"
        "`.fees <amount>` → fee calculator\n"
        "`.gc` → creates `Rich | MM | service` with PFP + pinned terms\n"
        "`.post @user link` → scammer alert with buttons\n"
        "`.clear on` → nuke GC title/photo change msgs\n"
        "`.mutegc on` → mute entire GC\n"
        "`.music <song>` → video + voice note both\n"
        "`.voicegirl` / `.voiceboy` → TTS voice notes\n\n"
        "Press the button below to log out completely.",
        reply_markup=kb,
    )
    st["finalized"] = True
    return ConversationHandler.END


async def approval_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    try:
        action, uid_s = q.data.split(":", 1)
        uid = int(uid_s)
    except Exception:
        return
    if action == "appr":
        APPROVED_USERS.add(uid)
        _save_id_set(APPROVAL_FILE, APPROVED_USERS)
        PENDING_USERS.discard(uid)
        _save_id_set(PENDING_FILE, PENDING_USERS)
        try:
            await q.edit_message_text(f"✅ ᴀᴘᴘʀᴏᴠᴇᴅ  ·  `{uid}`", parse_mode="Markdown")
        except Exception: pass
        try:
            await context.bot.send_message(
                uid,
                "✅ Aapko approve kar diya gaya hai.\n"
                "Ab /start bhejein shuru karne ke liye.",
            )
        except Exception:
            pass
    elif action == "rej":
        PENDING_USERS.discard(uid)
        _save_id_set(PENDING_FILE, PENDING_USERS)
        APPROVED_USERS.discard(uid)
        _save_id_set(APPROVAL_FILE, APPROVED_USERS)
        try:
            await q.edit_message_text(f"❌ ʀᴇᴊᴇᴄᴛᴇᴅ  ·  `{uid}`", parse_mode="Markdown")
        except Exception: pass
        try:
            await context.bot.send_message(uid, "❌ Aapki request reject kar di gayi hai.")
        except Exception:
            pass


async def logout_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id
    await _kill_running(chat_id, do_logout=True)
    for p in SESS_DIR.glob(f"session_{chat_id}*"):
        try: p.unlink()
        except Exception: pass
    cp = _cfg_path(chat_id)
    if cp.exists():
        try: cp.unlink()
        except Exception: pass
    sp = _state_path_for(chat_id)
    if sp.exists():
        try: sp.unlink()
        except Exception: pass
    try:
        for mid in range(q.message.message_id, max(0, q.message.message_id - 60), -1):
            try: await context.bot.delete_message(chat_id, mid)
            except Exception: pass
    except Exception:
        pass
    await context.bot.send_message(
        chat_id,
        "👋 Logged out successfully.\n"
        "All sessions and data removed.\n"
        "Send /start to begin again.",
    )
    return ConversationHandler.END


def _build_admin_panel():
    running_info = []
    for cid, info in list(_running.items()):
        proc = info.get("proc")
        alive = proc is not None and proc.poll() is None
        running_info.append({
            "chat_id": cid,
            "user_id": info.get("user_id", "?"),
            "alive": alive,
        })

    alive_count = sum(1 for r in running_info if r["alive"])
    dead_count = len(running_info) - alive_count

    try:
        registered_count = len(list(USERS_DIR.glob("user_*.json")))
    except Exception:
        registered_count = 0

    approved_count = len(APPROVED_USERS)
    pending_list = list(PENDING_USERS)
    pending_count = len(pending_list)

    admins_str = "\n".join(f"  ▸ `{a}`" for a in sorted(ADMIN_IDS))

    text = (
        "📊 𝗔𝗗𝗠𝗜𝗡 𝗣𝗔𝗡𝗘𝗟\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 𝗨𝗦𝗘𝗥𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗨𝗦\n"
        f"  ▸ running now    : {alive_count}\n"
        f"  ▸ dead processes : {dead_count}\n"
        f"  ▸ registered     : {registered_count}\n\n"
        "👥 𝗔𝗖𝗖𝗘𝗦𝗦 𝗖𝗢𝗡𝗧𝗥𝗢𝗟\n"
        f"  ▸ approved users : {approved_count}\n"
        f"  ▸ pending users  : {pending_count}\n\n"
        f"👑 𝗔𝗗𝗠𝗜𝗡𝗦\n{admins_str}\n"
    )

    if running_info:
        text += "\n🔧 𝗥𝗨𝗡𝗡𝗜𝗡𝗚 𝗕𝗢𝗧𝗦\n"
        for i, r in enumerate(running_info[:10], 1):
            status = "🟢" if r["alive"] else "🔴"
            text += f"  {i}. {status} `{r['chat_id']}` → `{r['user_id']}`\n"
        if len(running_info) > 10:
            text += f"  ... +{len(running_info) - 10} more\n"

    if pending_list:
        text += "\n⏳ 𝗣𝗘𝗡𝗗𝗜𝗡𝗚 𝗥𝗘𝗤𝗨𝗘𝗦𝗧𝗦\n"
        for uid in pending_list[:8]:
            text += f"  ▸ `{uid}`\n"
        if len(pending_list) > 8:
            text += f"  ... +{len(pending_list) - 8} more\n"

    rows = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin:refresh")],
    ]
    for r in running_info[:3]:
        if r["alive"]:
            rows.append([InlineKeyboardButton(
                f"🔪 Kill {r['user_id']}",
                callback_data=f"admin:kill:{r['chat_id']}",
            )])
    for uid in pending_list[:3]:
        rows.append([
            InlineKeyboardButton(f"✅ {uid}", callback_data=f"appr:{uid}"),
            InlineKeyboardButton(f"❌ {uid}", callback_data=f"rej:{uid}"),
        ])
    kb = InlineKeyboardMarkup(rows)
    return text, kb


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔ Not authorized.")
    text, kb = _build_admin_panel()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def admin_action_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    data = q.data

    if data == "admin:refresh":
        text, kb = _build_admin_panel()
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return

    if data.startswith("admin:kill:"):
        try:
            cid = int(data.split(":")[2])
        except Exception:
            return
        await _kill_running(cid, do_logout=True)
        text, kb = _build_admin_panel()
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        return


def bot_main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(approval_cb, pattern=r"^(appr|rej):"))
    app.add_handler(CallbackQueryHandler(logout_cb,   pattern=r"^logout$"))
    app.add_handler(CallbackQueryHandler(admin_action_cb, pattern=r"^admin:"))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_API_ID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_id)],
            ASK_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_hash)],
            ASK_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_phone)],
            ASK_CODE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, got_code_digit)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_password)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
        allow_reentry=True,
    )
    app.add_handler(conv)
    print("onboarding bot online — /start in telegram")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ==================================================================
#  DISPATCH
# ==================================================================
if __name__ == "__main__":
    if IS_USERBOT:
        try:
            signal.signal(signal.SIGINT,  lambda *_: None)
            signal.signal(signal.SIGTERM, lambda *_: None)
        except Exception:
            pass
        atexit.register(lambda: persist())
        try:
            asyncio.run(userbot_main())
        except KeyboardInterrupt:
            pass
    else:
        atexit.register(lambda: persist())
        bot_main()
