"""
Erebus File Store Bot  -  Hosted By Erebus | Owner Haze @Noxmirage
Same UI kit as the Erebus Tournament Bot (small-caps font, blockquote headers, coloured buttons).
aiogram >= 3.25

How it works
  * Admin sends any files / media to the bot  ->  copied into a PRIVATE storage channel
  * Bot creates a deep link  https://t.me/<bot>?start=<token>
  * A user opens the link  ->  must join the required channels  ->  bot sends the files
"""
import asyncio
import csv
import html
import io
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time as _time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Thread

# Only for the Render / web keep-alive page. Remove these lines (and keep_alive()) if you do not need it.
from flask import Flask, send_from_directory

from dotenv import load_dotenv
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
)

load_dotenv()

# ----------------------------------------------------------------------------
# Web keep-alive (optional)
# ----------------------------------------------------------------------------
web = Flask(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))


@web.route("/")
def web_home():
    if os.path.exists(os.path.join(_HERE, "index.html")):
        return send_from_directory(_HERE, "index.html")
    return "Erebus File Store Bot is running.", 200


@web.route("/health")
def web_health():
    return "OK", 200


def keep_alive():
    port = int(os.getenv("PORT", "10000"))
    Thread(target=lambda: web.run(host="0.0.0.0", port=port), daemon=True).start()


keep_alive()

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
ADMINS = {OWNER_ID} | {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0") or 0)   # private channel, bot = admin
DB_PATH = Path(os.getenv("DB_PATH", "data/erebus_fs.sqlite3"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))
BACKUP_EVERY_HOURS = int(os.getenv("BACKUP_EVERY_HOURS", "1"))
DEV_HANDLE = "@Noxmirage"
USE_CUSTOM_EMOJI = os.getenv("USE_CUSTOM_EMOJI", "1") == "1"

if not BOT_TOKEN or not OWNER_ID or not STORAGE_CHANNEL_ID:
    raise RuntimeError("Set BOT_TOKEN, OWNER_ID and STORAGE_CHANNEL_ID in your .env file")


def _parse_required(raw):
    """REQUIRED_CHATS = @channel1,@channel2,-1001234567890=https://t.me/+inviteLink
    Public channels: just the @username. Private channels: id=invite_link (bot must be admin there)."""
    out = []
    for n, item in enumerate([c.strip() for c in raw.split(",") if c.strip()], 1):
        if "=" in item:
            cid, url = item.split("=", 1)
        else:
            cid, url = item, f"https://t.me/{item.lstrip('@')}"
        cid = cid.strip()
        chat_id = int(cid) if cid.lstrip("-").isdigit() else cid
        label = cid if cid.startswith("@") else f"Channel {n}"
        out.append({"chat": chat_id, "url": url.strip(), "label": label})
    return out


# REQUIRED_CHATS in .env is only used ONCE to seed the database on first start.
# After that, manage force-subscribe chats from the Admin Panel -> Force Subscribe.
ENV_REQUIRED = _parse_required(os.getenv("REQUIRED_CHATS", ""))


def get_required():
    """Force-subscribe chats stored in the database (managed from the admin panel)."""
    out = []
    for r in q_all("SELECT * FROM force_chats ORDER BY id"):
        cid = r["chat_id"]
        out.append({"id": r["id"], "chat": int(cid) if cid.lstrip("-").isdigit() else cid,
                    "url": r["url"], "label": r["title"], "kind": r["kind"]})
    return out

for _d in (DB_PATH.parent, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
    force=True,
)

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
BOT_USERNAME = ""   # filled at start-up


# ----------------------------------------------------------------------------
# UI kit (same as the Erebus bots): font, emojis, panels, buttons, auto-cleaning
# ----------------------------------------------------------------------------
_SMALL_CAPS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)


def sc(text):
    """Bot font: 'free plan' -> 'Fʀᴇᴇ Pʟᴀɴ'. ACRONYMS, numbers, @names and /commands stay untouched."""
    def word(w):
        if any(c.isdigit() or c in "@/₹_<>&" for c in w) or (w.isupper() and len(w) > 1):
            return w
        m = re.search(r"[A-Za-z]", w)
        if not m:
            return w
        i = m.end()
        return w[:i - 1] + w[i - 1].upper() + w[i:].translate(_SMALL_CAPS)
    return "".join(p if p.isspace() else word(p) for p in re.split(r"(\s+)", text))


EMOJI = {
    "title": "✨", "star": "⭐", "camera": "📸", "bolt": "⚡", "user": "👤", "id": "🆔",
    "bot": "👾", "dev": "👨‍💻", "ok": "✅", "err": "❌", "warn": "⚠️", "time": "⏱",
    "pay": "💳", "box": "📦", "money": "💰", "calendar": "📅", "bell": "🔔", "ban": "🚫",
    "admin": "🛠", "mail": "✉️", "link": "🔗", "channel": "📢", "group": "👥",
    "stats": "📊", "lock": "🔒", "ghost": "👻", "home": "🏠", "bank": "🏦",
    "party": "🎉", "pin": "📝", "spin": "🌀",
    "new": "🆕", "web": "🌐", "chat": "💬", "trash": "🗑️", "dot": "🔹", "dot2": "🔸",
    "radio": "🔘", "host": "🏛️", "earth": "🌍", "wait": "⏳", "cat": "🐈‍⬛",
    "trophy": "🏆", "game": "🎮", "reply": "↩️", "backup": "💾", "restore": "♻️",
    "csv": "📄", "info": "ℹ️", "plus": "➕", "list": "📋",
    "medal": "🏅", "history": "🗂️", "cert": "🎓", "crown": "👑",
    "file": "📁", "upload": "📤", "eye": "👁", "shield": "🛡", "fire": "🔥", "gear": "⚙️",
}
EMOJI_IDS = {}
if USE_CUSTOM_EMOJI:
    EMOJI_IDS.update({
        "title": "5424972470023104089", "star": "5438496463044752972", "bolt": "5224607267797606837",
        "ok": "5206607081334906820", "err": "5210952531676504517", "time": "5440621591387980068",
        "box": "6046233639644042899", "money": "5278467510604160626", "calendar": "5413879192267805083",
        "bell": "5458603043203327669", "ban": "5260293700088511294", "admin": "5341715473882955310",
        "mail": "5253742260054409879", "link": "5271604874419647061", "channel": "5424818078833715060",
        "group": "5332724926216428039", "stats": "5231200819986047254", "lock": "5296369303661067030",
        "ghost": "5431609822288033666", "bank": "5332455502917949981", "party": "5276032951342088188",
        "pin": "5397782960512444700", "spin": "5375338737028841420",
        "new": "5386367538735104399", "warn": "4915853119839011973", "pay": "5039623284056917259",
        "web": "5039895103947146186", "chat": "5040042498634810056", "trash": "5042329873662609701",
        "dot": "5039753786638205957", "dot2": "5042020176455795565", "radio": "5042097984083330584",
        "host": "5343636681473935403", "earth": "5042176294222037888", "wait": "5039579582764680065",
        "user": "6237927637906364256", "cat": "5039653765439816618",
    })

MSG_EMOJI_OK = True    # set by setup_emoji() after really testing Telegram
BTN_ICON_OK = True


def E(name):
    fallback = EMOJI.get(name, "•")
    cid = EMOJI_IDS.get(name) if MSG_EMOJI_OK else None
    return f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>' if cid else fallback


_BTN_FIELDS = InlineKeyboardButton.model_fields
HAS_ICON = "icon_custom_emoji_id" in _BTN_FIELDS   # needs aiogram >= 3.25
HAS_STYLE = "style" in _BTN_FIELDS


def btn(text, emoji=None, style=None, **kw):
    """style: 'primary' (blue) / 'success' (green) / 'danger' (red)."""
    label = sc(text)
    extra = {}
    cid = EMOJI_IDS.get(emoji) if (emoji and BTN_ICON_OK) else None
    if cid and HAS_ICON:
        extra["icon_custom_emoji_id"] = cid
    elif emoji:
        label = f"{EMOJI.get(emoji, '')} {label}"
    if style and HAS_STYLE:
        extra["style"] = style
    return InlineKeyboardButton(text=label, **extra, **kw)


SEP = "──────────────────"


def bq(text):
    return f"<blockquote>{text}</blockquote>"


def head(emoji, title, tail=""):
    return f"{E(emoji)} <b>{sc(title)}</b>{tail}"


def row(emoji, label, value):
    return f"{E(emoji)} <b>{sc(label)}</b> ➛ {value}"


def esc(value):
    return html.escape(str(value if value is not None else ""))


def fmt_secs(s):
    s = int(s or 0)
    if s <= 0:
        return sc("Off")
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h"


_TRASH = {}     # chat_id -> temporary message ids (prompts ...)
_PANEL = {}     # chat_id -> current main-menu message id
_TASKS = set()  # keeps background tasks alive


async def _delete(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def remember(message):
    if message:
        _TRASH.setdefault(message.chat.id, []).append(message.message_id)
    return message


async def sweep(chat_id, keep=None):
    for mid in _TRASH.pop(chat_id, []):
        if mid != keep:
            await _delete(chat_id, mid)


async def tidy(message):
    await _delete(message.chat.id, message.message_id)


async def _delete_later(chat_id, message_id, ttl):
    await asyncio.sleep(ttl)
    await _delete(chat_id, message_id)


def spawn(coro):
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def notice(chat_id, text, ttl=8, **kw):
    try:
        msg = await bot.send_message(chat_id, text, **kw)
    except Exception:
        logging.exception("notice to %s failed", chat_id)
        return None
    spawn(_delete_later(chat_id, msg.message_id, ttl))
    return msg


async def ask(chat_id, text, markup=None):
    """Send a prompt that sweep() removes when the step is done."""
    return remember(await bot.send_message(chat_id, text, reply_markup=markup))


async def show_panel(chat_id, text, markup):
    old = _PANEL.pop(chat_id, None)
    if old:
        await _delete(chat_id, old)
    msg = await bot.send_message(chat_id, text, reply_markup=markup)
    _PANEL[chat_id] = msg.message_id
    return msg


async def safe_edit(callback: CallbackQuery, text, reply_markup=None, parse_mode="HTML"):
    msg = callback.message
    if getattr(msg, "photo", None) or getattr(msg, "document", None) or getattr(msg, "video", None):
        await _delete(msg.chat.id, msg.message_id)
        sent = await bot.send_message(msg.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        _PANEL[msg.chat.id] = sent.message_id
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def q_all(sql, args=()):
    with db() as con:
        return con.execute(sql, args).fetchall()


def q_one(sql, args=()):
    with db() as con:
        return con.execute(sql, args).fetchone()


def execute(sql, args=()):
    with db() as con:
        return con.execute(sql, args).lastrowid


def count(sql, args=()):
    return q_one(sql, args)[0]


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, banned INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            protect INTEGER NOT NULL DEFAULT 0, autodelete INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS link_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, link_id INTEGER NOT NULL,
            storage_msg_id INTEGER NOT NULL, kind TEXT, name TEXT, position INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, link_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS force_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            url TEXT NOT NULL, kind TEXT, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, sender TEXT NOT NULL,
            body TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, kind TEXT,
            sent_count INTEGER NOT NULL, failed_count INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_files_link ON link_files(link_id, position);
        CREATE INDEX IF NOT EXISTS idx_log_link ON access_log(link_id);
        """)
        # one-time seed from REQUIRED_CHATS in .env (so old setups keep working)
        seeded = con.execute("SELECT 1 FROM settings WHERE key='force_seeded'").fetchone()
        if not seeded:
            for c in ENV_REQUIRED:
                con.execute("INSERT OR IGNORE INTO force_chats(chat_id, title, url, kind, added_at) VALUES(?,?,?,?,?)",
                            (str(c["chat"]), c["label"], c["url"], "channel", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
            con.execute("INSERT INTO settings(key, value) VALUES('force_seeded','1')")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_admin(uid):
    return uid in ADMINS


def get_setting(key, default="0"):
    r = q_one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))


def upsert_user(user):
    stamp = now_iso()
    execute("""INSERT INTO users(user_id, username, first_name, joined_at, last_seen) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
               first_name=excluded.first_name, last_seen=excluded.last_seen""",
            (user.id, user.username, user.first_name, stamp, stamp))


def is_banned(uid):
    r = q_one("SELECT banned FROM users WHERE user_id=?", (uid,))
    return bool(r and r["banned"])


def get_user(uid):
    return q_one("SELECT * FROM users WHERE user_id=?", (uid,))


def get_link(lid):
    return q_one("SELECT * FROM links WHERE id=?", (lid,))


def link_url(token):
    return f"https://t.me/{BOT_USERNAME}?start={token}"


def body_of(message: Message):
    return message.text or message.caption or f"[{message.content_type}]"


def file_name_of(message: Message):
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj is not None and getattr(obj, "file_name", None):
            return obj.file_name[:60]
    if message.caption:
        return message.caption.strip().replace("\n", " ")[:60]
    return message.content_type


# ----------------------------------------------------------------------------
# Storage channel + backups
# ----------------------------------------------------------------------------
async def log_channel(text=None, document=None):
    try:
        if document:
            await bot.send_document(STORAGE_CHANNEL_ID, document, caption=text)
        else:
            await bot.send_message(STORAGE_CHANNEL_ID, text)
    except Exception:
        logging.exception("storage channel send failed")


async def archive_copy(message: Message):
    try:
        await bot.copy_message(STORAGE_CHANNEL_ID, message.chat.id, message.message_id)
    except Exception:
        logging.exception("archive copy failed")


async def backup_now(reason="manual"):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"erebus_fs_{stamp}.db"

    def _copy():
        with closing(sqlite3.connect(DB_PATH)) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
    await asyncio.to_thread(_copy)
    for old in sorted(BACKUP_DIR.glob("erebus_fs_*.db"))[:-20]:
        old.unlink(missing_ok=True)
    caption = f"{E('backup')} <b>{sc('Database backup')}</b> ({esc(reason)})\n{stamp}"
    await log_channel(caption, document=BufferedInputFile(path.read_bytes(), filename=path.name))
    return path


async def backup_loop():
    while True:
        await asyncio.sleep(BACKUP_EVERY_HOURS * 3600)
        try:
            await backup_now("auto")
        except Exception:
            logging.exception("auto backup failed")


# ----------------------------------------------------------------------------
# Keyboards
# ----------------------------------------------------------------------------
def home_keyboard(admin=False):
    rows = [
        [btn("How It Works", "info", style="primary", callback_data="howto"),
         btn("Message Admin", "mail", style="primary", callback_data="support")],
        [btn("Developer & Host", "dev", callback_data="about")],
    ]
    if admin:
        rows.append([btn("Admin Panel", "admin", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(*top_rows):
    return InlineKeyboardMarkup(inline_keyboard=[*top_rows, [btn("Main Menu", "home", callback_data="home")]])


def join_keyboard():
    rows = [[btn(f"Join {c['label'][:30]}", "channel", url=c["url"])] for c in get_required()]
    rows.append([btn("I've Joined", "ok", style="success", callback_data="checkjoin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("New Link", "upload", style="success", callback_data="anew"),
         btn("My Links", "link", style="primary", callback_data="alinks")],
        [btn("Broadcast", "channel", callback_data="abc"),
         btn("Support Inbox", "mail", style="primary", callback_data="asupport")],
        [btn("Statistics", "stats", callback_data="astats"),
         btn("Users", "user", callback_data="ausers")],
        [btn("Force Subscribe", "channel", style="primary", callback_data="afs"),
         btn("Protect Settings", "shield", callback_data="aset")],
        [btn("Export CSV", "csv", callback_data="acsv"),
         btn("Backup Now", "backup", callback_data="abackup")],
        [btn("Restore Backup", "restore", style="danger", callback_data="arestore")],
        [btn("Main Menu", "home", callback_data="home")],
    ])


def admin_back(*top_rows):
    return InlineKeyboardMarkup(inline_keyboard=[*top_rows, [btn("Admin Panel", "admin", callback_data="admin")]])


# ----------------------------------------------------------------------------
# Screens (text)
# ----------------------------------------------------------------------------
def home_text(name):
    return "\n".join([
        bq(f"{E('user')} {sc('Welcome')}, {esc(name or 'friend')}"),
        head("title", "File Store", " ❗️"),
        "",
        row("lock", "Access", sc("Join our channels to unlock files")),
        row("link", "Files", sc("Open a file link shared by admin")),
        row("shield", "Storage", sc("Everything is stored safely")),
        SEP,
        f"{E('bolt')} {sc('Open a link from admin and your files arrive instantly.')}",
    ])


def howto_text():
    return "\n".join([
        bq(f"{E('info')} {sc('How it works')}"),
        "",
        row("dot", "Step 1", sc("Open a file link shared by admin")),
        row("dot", "Step 2", sc("Join the required channels")),
        row("dot", "Step 3", sc("Tap I've Joined")),
        row("dot", "Step 4", sc("Your files are sent here instantly")),
        SEP,
        f"{E('warn')} {sc('Some files auto-delete after a while. Save them to Saved Messages.')}",
        f"{E('mail')} {sc('Problem with a link? Message admin.')}",
    ])


def about_text():
    return "\n".join([
        bq(f"{E('host')} {sc('Developer & host')}"),
        "",
        row("dev", "Developer", f"Haze {DEV_HANDLE}"),
        row("host", "Hosted By", sc("Erebus")),
        *([row("group", "Community", " ".join(esc(c["label"]) for c in get_required()))] if get_required() else []),
        SEP,
        f"{E('mail')} {sc('Need help? Message the developer.')}",
    ])


def about_keyboard():
    rows = [[btn("Developer", "dev", url=f"https://t.me/{DEV_HANDLE.lstrip('@')}")]]
    for c in get_required():
        rows.append([btn(f"Community {c['label'][:24]}", "group", url=c["url"])])
    return back_keyboard(*rows)


def join_text(first_visit=True):
    title = "Join To Unlock" if first_visit else "Not Joined Yet"
    return "\n".join([
        bq(f"{E('lock')} {sc('Access locked')}"),
        head("channel", title, " ❗️"),
        "",
        sc("Join all the channels below, then tap I've Joined."),
    ])


def link_text(l):
    n = count("SELECT COUNT(*) FROM link_files WHERE link_id=?", (l["id"],))
    return "\n".join([
        bq(f"{E('link')} {sc('Link')} #{l['id']}"),
        f"{E('title')} <b>{esc(l['title'])}</b>",
        "",
        row("file", "Files", n),
        row("eye", "Opened", l["views"]),
        row("shield", "No Forward & SS",
            sc("On (forced by settings)") if get_setting("force_protect") == "1"
            else (sc("On") if l["protect"] else sc("Off"))),
        row("time", "Auto-Delete", fmt_secs(l["autodelete"])),
        row("radio", "Status", sc("Active") if l["active"] else sc("Revoked")),
        row("calendar", "Created", esc(l["created_at"])),
        SEP,
        f"{E('link')} <code>{esc(link_url(l['token']))}</code>",
    ])


def link_keyboard(l):
    return admin_back(
        [btn("No Forward & SS: On" if l["protect"] else "No Forward & SS: Off", "shield",
             style="success" if l["protect"] else None, callback_data=f"lprot:{l['id']}"),
         btn("Auto-Delete", "time", callback_data=f"lauto:{l['id']}")],
        [btn("Revoke" if l["active"] else "Activate", "lock", callback_data=f"lact:{l['id']}"),
         btn("Test Open", "eye", callback_data=f"ltest:{l['id']}")],
        [btn("Rename", "pin", callback_data=f"lren:{l['id']}"),
         btn("Delete", "trash", style="danger", callback_data=f"ldel:{l['id']}")],
        [btn("All Links", "link", callback_data="alinks")])


# ----------------------------------------------------------------------------
# States
# ----------------------------------------------------------------------------
class SupportFlow(StatesGroup):
    message = State()


class AdminFlow(StatesGroup):
    addchat = State()
    chatlink = State()
    upload = State()
    title = State()
    rename = State()
    broadcast = State()
    support_reply = State()
    restore = State()


async def admin_only(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("Not allowed.", show_alert=True)
        return False
    return True


# ----------------------------------------------------------------------------
# Membership gate + private-chat-only middleware
# ----------------------------------------------------------------------------
_JOINED = {}
_PENDING = {}   # user_id -> link token they tried to open before joining


async def membership_ok(user_id):
    ts = _JOINED.get(user_id)
    if ts and _time.time() - ts < 120:
        return True
    for c in get_required():
        try:
            member = await bot.get_chat_member(chat_id=c["chat"], user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return False
            if member.status == ChatMemberStatus.RESTRICTED and not getattr(member, "is_member", True):
                return False
        except Exception:
            logging.exception("Membership check failed for %s", c["chat"])
            return False
    _JOINED[user_id] = _time.time()
    return True


class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        chat = event.chat if isinstance(event, Message) else (event.message.chat if event.message else None)
        if user is None or chat is None or chat.type != "private":
            return None
        upsert_user(user)
        if user.id in ADMINS:
            return await handler(event, data)
        if is_banned(user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Access disabled.", show_alert=True)
            else:
                await notice(chat.id, f"{E('ban')} {sc('Your access has been disabled.')}", ttl=10)
            return None
        if isinstance(event, CallbackQuery) and event.data == "checkjoin":
            return await handler(event, data)
        if await membership_ok(user.id):
            return await handler(event, data)
        # not joined: remember the link they wanted so it opens right after joining
        if isinstance(event, Message) and (event.text or "").startswith("/start "):
            payload = event.text.split(maxsplit=1)[1].strip()
            if payload:
                _PENDING[user.id] = payload
        if isinstance(event, CallbackQuery):
            await safe_edit(event, join_text(), join_keyboard())
            await event.answer()
        else:
            await tidy(event)
            await sweep(chat.id)
            await show_panel(chat.id, join_text(), join_keyboard())
        return None


dp.message.outer_middleware(Gate())
dp.callback_query.outer_middleware(Gate())


# ----------------------------------------------------------------------------
# Delivering a link's files to a user
# ----------------------------------------------------------------------------
async def deliver_link(chat_id, user_id, token):
    l = q_one("SELECT * FROM links WHERE token=?", (token,))
    if not l:
        return await notice(chat_id, f"{E('err')} {sc('This link is invalid or was deleted.')}", ttl=10)
    if not l["active"] and not is_admin(user_id):
        return await notice(chat_id, f"{E('lock')} {sc('This link has been revoked by admin.')}", ttl=10)
    files = q_all("SELECT * FROM link_files WHERE link_id=? ORDER BY position", (l["id"],))
    if not files:
        return await notice(chat_id, f"{E('warn')} {sc('This link has no files.')}", ttl=10)

    prot = bool(l["protect"]) or get_setting("force_protect") == "1"
    sent_ids = []
    head_msg = await bot.send_message(chat_id, "\n".join([
        bq(f"{E('file')} {esc(l['title'])}"),
        row("box", "Files", len(files)),
    ]))
    sent_ids.append(head_msg.message_id)
    failed = 0
    for f in files:
        try:
            m = await bot.copy_message(chat_id, STORAGE_CHANNEL_ID, f["storage_msg_id"],
                                       protect_content=prot)
            sent_ids.append(m.message_id)
        except Exception:
            failed += 1
            logging.exception("could not deliver storage msg %s", f["storage_msg_id"])
        await asyncio.sleep(0.05)

    execute("UPDATE links SET views=views+1 WHERE id=?", (l["id"],))
    execute("INSERT INTO access_log(link_id, user_id, created_at) VALUES(?,?,?)", (l["id"], user_id, now_iso()))

    lines = [f"{E('ok')} {sc('Delivered')} {len(files) - failed}/{len(files)} {sc('files')}"]
    if failed:
        lines.append(f"{E('warn')} {sc('Some files could not be sent. Tell admin.')}")
    if l["autodelete"]:
        lines.append(f"{E('time')} {sc('These files auto-delete in')} <b>{fmt_secs(l['autodelete'])}</b>. "
                     f"{sc('Save them now.')}")
    if prot:
        lines.append(f"{E('shield')} {sc('Forwarding and saving are disabled for these files.')}")
    done = await bot.send_message(chat_id, "\n".join(lines), reply_markup=back_keyboard())
    if l["autodelete"]:
        for mid in sent_ids:
            spawn(_delete_later(chat_id, mid, l["autodelete"]))


# ----------------------------------------------------------------------------
# Commands (registered first so they win over state handlers)
# ----------------------------------------------------------------------------
async def go_home(chat, user):
    await show_panel(chat, home_text(user.first_name), home_keyboard(is_admin(user.id)))


@dp.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    await tidy(message)
    await sweep(message.chat.id)
    payload = (command.args or "").strip()
    await go_home(message.chat.id, message.from_user)
    if payload:
        await deliver_link(message.chat.id, message.from_user.id, payload)


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await tidy(message)
    await sweep(message.chat.id)
    await notice(message.chat.id, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    await go_home(message.chat.id, message.from_user)


@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await tidy(message)
    await sweep(message.chat.id)
    await show_panel(message.chat.id, admin_text(), admin_keyboard())


@dp.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await tidy(message)
    await begin_upload(message.chat.id, state)


@dp.message(Command("done"), StateFilter(AdminFlow.upload))
async def cmd_done(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await tidy(message)
    await finish_upload(message.chat.id, state)


@dp.message(Command("backup"))
async def cmd_backup(message: Message):
    if not is_admin(message.from_user.id):
        return
    await tidy(message)
    path = await backup_now("manual")
    await bot.send_document(message.chat.id, BufferedInputFile(path.read_bytes(), filename=path.name),
                            caption=f"{E('backup')} {sc('Backup saved on host')}: <code>{esc(path)}</code>")


@dp.message(Command("restore"))
async def cmd_restore(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await tidy(message)
    await state.set_state(AdminFlow.restore)
    await ask(message.chat.id, f"{E('restore')} {sc('Send the .db backup file to restore.')}\n{sc('Send /cancel to stop.')}")


@dp.message(Command("ban"))
@dp.message(Command("unban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        return
    await tidy(message)
    parts = (message.text or "").split()
    ban = parts[0].lstrip("/").split("@")[0].lower() == "ban"
    if len(parts) != 2:
        return await notice(message.chat.id, "Usage: <code>/ban @username</code> or <code>/ban 123456</code>", ttl=10)
    tok = parts[1]
    if tok.isdigit():
        u = get_user(int(tok))
    else:
        u = q_one("SELECT * FROM users WHERE lower(username)=?", (tok.lstrip("@").lower(),))
    if not u:
        return await notice(message.chat.id, f"{E('warn')} {sc('User not found. They must have started the bot.')}")
    execute("UPDATE users SET banned=? WHERE user_id=?", (1 if ban else 0, u["user_id"]))
    await notice(message.chat.id, f"{E('ok')} {sc('Banned' if ban else 'Unbanned')} <code>{u['user_id']}</code>", ttl=8)


# ----------------------------------------------------------------------------
# User: menus
# ----------------------------------------------------------------------------
@dp.callback_query(F.data == "checkjoin")
async def check_join(callback: CallbackQuery):
    uid = callback.from_user.id
    _JOINED.pop(uid, None)
    if await membership_ok(uid):
        await safe_edit(callback, home_text(callback.from_user.first_name), home_keyboard(is_admin(uid)))
        await callback.answer("Verified!")
        token = _PENDING.pop(uid, None)
        if token:
            await deliver_link(callback.message.chat.id, uid, token)
    else:
        await callback.answer("You must join first.", show_alert=True)


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await sweep(callback.message.chat.id, keep=callback.message.message_id)
    await safe_edit(callback, home_text(callback.from_user.first_name), home_keyboard(is_admin(callback.from_user.id)))
    await callback.answer()


@dp.callback_query(F.data == "howto")
async def howto(callback: CallbackQuery):
    await safe_edit(callback, howto_text(), back_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    await safe_edit(callback, about_text(), about_keyboard())
    await callback.answer()


# ---- support ------------------------------------------------------------------
@dp.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(SupportFlow.message)
    await ask(callback.message.chat.id,
              f"{E('mail')} {sc('Send your issue or question to admin.')}\n"
              f"{sc('Text, photo or any file works. Your Telegram name, username and ID are included automatically.')}\n"
              f"{sc('Send /cancel to stop.')}")
    await callback.answer()


async def relay(src: Message, chat_id, header, kb=None):
    """Deliver any kind of message (text, photo, file ...) with a header and a button."""
    try:
        if src.text:
            await bot.send_message(chat_id, f"{header}\n\n{esc(src.text)}", reply_markup=kb)
        else:
            await bot.send_message(chat_id, header)
            await bot.copy_message(chat_id, src.chat.id, src.message_id, reply_markup=kb)
        return True
    except Exception:
        logging.exception("relay to %s failed", chat_id)
        return False


@dp.message(SupportFlow.message)
async def support_send(message: Message, state: FSMContext):
    chat = message.chat.id
    user = message.from_user
    handle = f"@{user.username}" if user.username else "no username"
    body = body_of(message)
    sid = execute("INSERT INTO support_messages(user_id, sender, body, created_at, status) VALUES(?,?,?,?, 'open')",
                  (user.id, "user", body, now_iso()))
    header = "\n".join([
        bq(f"{E('mail')} {sc('New support message')} #{sid}"),
        row("user", "Name", esc(user.full_name)),
        row("user", "Username", esc(handle)),
        row("id", "Telegram ID", f"<code>{user.id}</code>"),
        SEP,
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Reply To User", "reply", style="primary", callback_data=f"sreply:{sid}")],
        [btn("Open Support", "mail", callback_data=f"sview:{sid}")]])
    delivered = False
    for admin in ADMINS:
        delivered = await relay(message, admin, header, kb) or delivered
    await state.clear()
    await sweep(chat)
    if delivered:
        await log_channel(f"{E('mail')} Support #{sid} from {user.id}")
        await archive_copy(message)
    await tidy(message)
    await bot.send_message(chat, f"{E('ok')} {sc('Your message was sent to admin.') if delivered else sc('Could not reach admin right now.')}",
                           reply_markup=back_keyboard())


@dp.callback_query(F.data == "asupport")
async def admin_support_list(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    rows = q_all("SELECT * FROM support_messages WHERE sender='user' ORDER BY id DESC LIMIT 25")
    lines = [bq(f"{E('mail')} {sc('Support inbox')}"), ""]
    kb = []
    if not rows:
        lines.append(sc("No support messages yet."))
    for item in rows:
        user = get_user(item["user_id"])
        label = f"@{user['username']}" if user and user["username"] else (user["first_name"] if user else str(item["user_id"]))
        lines.append(f"{E('dot')} #{item['id']} · {esc(label)} · {sc(item['status'])}")
        kb.append([btn(f"#{item['id']} {label[:20]}", "mail", callback_data=f"sview:{item['id']}")])
    await safe_edit(callback, "\n".join(lines), admin_back(*kb))
    await callback.answer()


@dp.callback_query(F.data.startswith("sview:"))
async def admin_support_view(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    sid = int(callback.data.split(":")[1])
    item = q_one("SELECT * FROM support_messages WHERE id=?", (sid,))
    if not item:
        return await callback.answer("Support message not found.", show_alert=True)
    user = get_user(item["user_id"])
    username = f"@{user['username']}" if user and user["username"] else "no username"
    text = "\n".join([
        bq(f"{E('mail')} {sc('Support message')} #{sid}"), "",
        row("user", "Name", esc(user["first_name"] if user else "Unknown")),
        row("user", "Username", esc(username)),
        row("id", "Telegram ID", f"<code>{item['user_id']}</code>"),
        row("time", "Received", esc(item["created_at"])),
        row("wait", "Status", sc(item["status"])), SEP,
        f"<b>{esc(item['body'] or 'Media attachment')}</b>",
    ])
    await safe_edit(callback, text, admin_back(
        [btn("Reply To User", "reply", style="primary", callback_data=f"sreply:{sid}")],
        [btn("Support Inbox", "mail", callback_data="asupport")]))
    await callback.answer()


@dp.callback_query(F.data.startswith("sreply:"))
async def admin_support_reply_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    sid = int(callback.data.split(":")[1])
    item = q_one("SELECT * FROM support_messages WHERE id=?", (sid,))
    if not item:
        return await callback.answer("Support message not found.", show_alert=True)
    await state.clear()
    await state.set_state(AdminFlow.support_reply)
    await state.update_data(support_id=sid)
    await ask(callback.message.chat.id,
              f"{E('reply')} {sc('Send your reply to Telegram ID')} <code>{item['user_id']}</code>.\n"
              f"{sc('Text, photo or any file works. Send /cancel to stop.')}")
    await callback.answer()


@dp.message(AdminFlow.support_reply)
async def admin_support_reply_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat = message.chat.id
    sid = (await state.get_data()).get("support_id")
    item = q_one("SELECT * FROM support_messages WHERE id=?", (sid,))
    if not item:
        await state.clear()
        return await notice(chat, f"{E('err')} {sc('Support message not found.')}", ttl=6)
    delivered = await relay(message, item["user_id"], "\n".join([
        bq(f"{E('reply')} {sc('Reply from admin')}"), row("mail", "Support Ticket", f"#{sid}"), SEP]))
    if delivered:
        execute("INSERT INTO support_messages(user_id, sender, body, created_at, status) VALUES(?,?,?,?, 'replied')",
                (item["user_id"], "admin", body_of(message), now_iso()))
        execute("UPDATE support_messages SET status='replied' WHERE id=?", (sid,))
        await log_channel(f"{E('reply')} Admin {message.from_user.id} replied to support #{sid}")
    await state.clear()
    await sweep(chat)
    await tidy(message)
    await notice(chat, f"{E('ok')} {sc('Reply delivered.') if delivered else sc('Could not deliver the reply.')}", ttl=7)


# ----------------------------------------------------------------------------
# Admin panel
# ----------------------------------------------------------------------------
def admin_text():
    return "\n".join([
        bq(f"{E('admin')} {sc('Admin panel')}"),
        "",
        row("link", "Links", count("SELECT COUNT(*) FROM links")),
        row("file", "Stored Files", count("SELECT COUNT(*) FROM link_files")),
        row("eye", "Total Opens", count("SELECT COUNT(*) FROM access_log")),
        row("channel", "Force-Sub Chats", count("SELECT COUNT(*) FROM force_chats")),
        row("shield", "Protect All Links", sc("On") if get_setting("force_protect") == "1" else sc("Off")),
        row("mail", "Open Support Messages", count("SELECT COUNT(*) FROM support_messages WHERE sender='user' AND status='open'")),
        row("group", "Users", count("SELECT COUNT(*) FROM users")),
        SEP,
        f"{E('upload')} {sc('Tap New Link, send your files, then Done. You get a shareable link.')}",
    ])


@dp.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.clear()
    await sweep(callback.message.chat.id, keep=callback.message.message_id)
    await safe_edit(callback, admin_text(), admin_keyboard())
    await callback.answer()


# ---- create a link: upload files -------------------------------------------------
def upload_text(n):
    return "\n".join([
        bq(f"{E('upload')} {sc('New link')}"),
        "",
        sc("Send the files now: documents, videos, photos, audio, text, anything."),
        sc("They are saved into the private storage channel."),
        SEP,
        row("file", "Files Added", n),
        sc("Tap Done when finished, or send /done."),
    ])


def upload_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        btn("Done", "ok", style="success", callback_data="updone"),
        btn("Cancel", "err", style="danger", callback_data="admin")]])


async def begin_upload(chat, state: FSMContext):
    await state.clear()
    await sweep(chat)
    await state.set_state(AdminFlow.upload)
    msg = await ask(chat, upload_text(0), upload_kb())
    await state.update_data(items=[], status_id=msg.message_id)


@dp.callback_query(F.data == "anew")
async def new_link_button(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await begin_upload(callback.message.chat.id, state)
    await callback.answer()


@dp.message(AdminFlow.upload)
async def upload_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat = message.chat.id
    try:
        copied = await bot.copy_message(STORAGE_CHANNEL_ID, chat, message.message_id)
    except Exception as e:
        logging.exception("storage copy failed")
        return await notice(chat, f"{E('err')} {sc('Could not save to the storage channel. Is the bot admin there?')}\n"
                                  f"<code>{esc(str(e)[:150])}</code>", ttl=10)
    data = await state.get_data()
    items = data.get("items", [])
    items.append({"mid": copied.message_id, "kind": message.content_type, "name": file_name_of(message)})
    await state.update_data(items=items)
    await tidy(message)
    try:
        await bot.edit_message_text(upload_text(len(items)), chat_id=chat, message_id=data["status_id"],
                                    reply_markup=upload_kb())
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data == "updone")
async def upload_done_button(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await callback.answer()
    await finish_upload(callback.message.chat.id, state)


async def finish_upload(chat, state: FSMContext):
    data = await state.get_data()
    if not data.get("items"):
        return await notice(chat, f"{E('warn')} {sc('Send at least one file first.')}", ttl=6)
    await sweep(chat)
    await state.set_state(AdminFlow.title)
    await ask(chat, f"{E('pin')} {sc('Send a title for this link (or send - to skip).')}\n{sc('Send /cancel to stop.')}")


@dp.message(AdminFlow.title)
async def upload_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or not message.text:
        return
    chat = message.chat.id
    data = await state.get_data()
    items = data.get("items", [])
    title = message.text.strip()[:60]
    if title == "-":
        title = "Untitled"
    await state.clear()
    await tidy(message)
    await sweep(chat)
    token = secrets.token_urlsafe(8)
    lid = execute("INSERT INTO links(token, title, created_by, created_at, protect, autodelete) VALUES(?,?,?,?,?,?)",
                  (token, title, message.from_user.id, now_iso(),
                   int(get_setting("default_protect", "0")), int(get_setting("default_autodelete", "0"))))
    for pos, it in enumerate(items, 1):
        execute("INSERT INTO link_files(link_id, storage_msg_id, kind, name, position) VALUES(?,?,?,?,?)",
                (lid, it["mid"], it["kind"], it["name"], pos))
    l = get_link(lid)
    await log_channel(f"{E('new')} Link #{lid} created ({len(items)} files)\n{esc(link_url(token))}")
    spawn(backup_now("new link"))
    await bot.send_message(chat, link_text(l) + f"\n{E('ok')} {sc('Link is live.')}", reply_markup=link_keyboard(l))


# ---- manage links ----------------------------------------------------------------
@dp.callback_query(F.data == "alinks")
async def admin_links(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    rows = q_all("SELECT * FROM links ORDER BY id DESC LIMIT 20")
    lines = [bq(f"{E('link')} {sc('My links')}"), ""]
    kb = []
    if not rows:
        lines.append(sc("None yet. Use New Link."))
    for l in rows:
        n = count("SELECT COUNT(*) FROM link_files WHERE link_id=?", (l["id"],))
        icon = "✅" if l["active"] else "🔒"
        lines.append(f"{icon} #{l['id']} <b>{esc(l['title'])}</b> ➛ {n} {sc('files')} · {l['views']} {sc('opens')}")
        kb.append([btn(f"#{l['id']} {l['title'][:22]}", "link", callback_data=f"lv:{l['id']}")])
    await safe_edit(callback, "\n".join(lines), admin_back(*kb))
    await callback.answer()


@dp.callback_query(F.data.startswith("lv:"))
async def link_view(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    l = get_link(int(callback.data.split(":")[1]))
    if not l:
        return await callback.answer("Not found.", show_alert=True)
    await safe_edit(callback, link_text(l), link_keyboard(l))
    await callback.answer()


@dp.callback_query(F.data.startswith("lprot:"))
async def link_protect(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    l = get_link(lid)
    execute("UPDATE links SET protect=? WHERE id=?", (0 if l["protect"] else 1, lid))
    l = get_link(lid)
    await safe_edit(callback, link_text(l), link_keyboard(l))
    await callback.answer("Updated.")


AUTO_STEPS = [0, 30, 60, 300, 600, 1800, 3600]


@dp.callback_query(F.data.startswith("lauto:"))
async def link_autodelete(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    l = get_link(lid)
    cur = l["autodelete"]
    nxt = AUTO_STEPS[(AUTO_STEPS.index(cur) + 1) % len(AUTO_STEPS)] if cur in AUTO_STEPS else 0
    execute("UPDATE links SET autodelete=? WHERE id=?", (nxt, lid))
    l = get_link(lid)
    await safe_edit(callback, link_text(l), link_keyboard(l))
    await callback.answer(f"Auto-delete: {fmt_secs(nxt)}")


@dp.callback_query(F.data.startswith("lact:"))
async def link_toggle(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    l = get_link(lid)
    execute("UPDATE links SET active=? WHERE id=?", (0 if l["active"] else 1, lid))
    l = get_link(lid)
    await safe_edit(callback, link_text(l), link_keyboard(l))
    await callback.answer("Updated.")


@dp.callback_query(F.data.startswith("ltest:"))
async def link_test(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    l = get_link(int(callback.data.split(":")[1]))
    if not l:
        return await callback.answer("Not found.", show_alert=True)
    await callback.answer("Sending...")
    await deliver_link(callback.message.chat.id, callback.from_user.id, l["token"])


@dp.callback_query(F.data.startswith("lren:"))
async def link_rename_ask(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    await state.clear()
    await state.set_state(AdminFlow.rename)
    await state.update_data(lid=lid)
    await ask(callback.message.chat.id, f"{E('pin')} {sc('Send the new title for link')} #{lid}.\n{sc('Send /cancel to stop.')}")
    await callback.answer()


@dp.message(AdminFlow.rename)
async def link_rename_do(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or not message.text:
        return
    lid = (await state.get_data())["lid"]
    execute("UPDATE links SET title=? WHERE id=?", (message.text.strip()[:60], lid))
    await state.clear()
    await tidy(message)
    await sweep(message.chat.id)
    await notice(message.chat.id, f"{E('ok')} {sc('Title updated for')} #{lid}", ttl=6)


@dp.callback_query(F.data.startswith("ldel:"))
async def link_delete_ask(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    await safe_edit(callback, "\n".join([
        bq(f"{E('trash')} {sc('Delete link')} #{lid}"), "",
        f"{E('warn')} {sc('The link stops working. Files stay in the storage channel. Continue?')}"]),
        admin_back([btn("Yes, Delete", "trash", style="danger", callback_data=f"ldelok:{lid}")],
                   [btn("Cancel", "err", callback_data=f"lv:{lid}")]))
    await callback.answer()


@dp.callback_query(F.data.startswith("ldelok:"))
async def link_delete(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    lid = int(callback.data.split(":")[1])
    execute("DELETE FROM link_files WHERE link_id=?", (lid,))
    execute("DELETE FROM links WHERE id=?", (lid,))
    await log_channel(f"{E('trash')} Link #{lid} deleted by {callback.from_user.id}")
    await safe_edit(callback, f"{E('ok')} {sc('Link deleted.')}", admin_back([btn("All Links", "link", callback_data="alinks")]))
    await callback.answer()


# ---- settings --------------------------------------------------------------------
# ---- force subscribe: add / remove channels and groups from the admin panel ----------
def fs_text():
    rows = get_required()
    lines = [bq(f"{E('channel')} {sc('Force subscribe')}"), ""]
    if rows:
        for i, c in enumerate(rows, 1):
            kind = "👥" if c["kind"] in ("group", "supergroup") else "📢"
            lines.append(f"{E('dot')} {i}. {kind} <b>{esc(c['label'])}</b> ➛ <code>{esc(c['chat'])}</code>")
    else:
        lines.append(sc("No chats added. Users can open links without joining anything."))
    lines += [SEP,
              f"{E('info')} {sc('Add any channel or group. The bot must be an admin there to check members.')}",
              f"{E('bolt')} {sc('Users must join every chat listed here before they get files.')}"]
    return "\n".join(lines)


def fs_kb():
    kb = [[btn("Add Channel Or Group", "plus", style="success", callback_data="afsadd")]]
    for c in get_required():
        kb.append([btn(f"Remove {c['label'][:26]}", "trash", style="danger", callback_data=f"fsdel:{c['id']}")])
    return admin_back(*kb)


def fs_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[btn("Cancel", "err", style="danger", callback_data="afs")]])


@dp.callback_query(F.data == "afs")
async def fs_view(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.clear()
    await sweep(callback.message.chat.id, keep=callback.message.message_id)
    await safe_edit(callback, fs_text(), fs_kb())
    await callback.answer()


@dp.callback_query(F.data == "afsadd")
async def fs_add_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.clear()
    await state.set_state(AdminFlow.addchat)
    await ask(callback.message.chat.id, "\n".join([
        bq(f"{E('plus')} {sc('Add channel or group')}"), "",
        f"{E('dot')} {sc('First add this bot as an admin in the channel or group.')}",
        f"{E('dot')} {sc('Then do ONE of these:')}",
        f"{E('dot2')} {sc('Forward any message from that channel here')}",
        f"{E('dot2')} {sc('Send its @username')}",
        f"{E('dot2')} {sc('Send its numeric ID, example -1001234567890')}",
        SEP,
        sc("Send /cancel to stop."),
    ]), fs_cancel_kb())
    await callback.answer()


async def fs_save(chat_id, title, url, kind):
    execute("INSERT OR REPLACE INTO force_chats(chat_id, title, url, kind, added_at) VALUES(?,?,?,?,?)",
            (str(chat_id), title[:60], url, kind, now_iso()))
    _JOINED.clear()     # everybody is re-checked against the new list
    await log_channel(f"{E('channel')} Force-sub chat added: {esc(title)} ({chat_id})")


async def fs_show(chat):
    await sweep(chat)
    await show_panel(chat, fs_text(), fs_kb())


@dp.message(AdminFlow.addchat)
async def fs_add_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat = message.chat.id
    await tidy(message)
    ref = None
    origin = getattr(message, "forward_origin", None)
    if origin is not None and getattr(origin, "chat", None) is not None:
        ref = origin.chat.id
    else:
        txt = (message.text or "").strip()
        if "t.me/+" in txt or "joinchat" in txt:
            ref = None
        elif txt.lstrip("-").isdigit():
            ref = int(txt)
        elif txt.startswith("@") or "t.me/" in txt:
            ref = "@" + txt.rstrip("/").split("/")[-1].lstrip("@")
    if ref is None:
        return await notice(chat, f"{E('warn')} {sc('Forward a message from the chat, or send its @username or numeric ID.')}", ttl=8)
    try:
        info = await bot.get_chat(ref)
        me_member = await bot.get_chat_member(info.id, (await bot.me()).id)
    except Exception as e:
        return await notice(chat, f"{E('err')} {sc('Cannot access that chat. Add the bot as an admin there first.')}\n"
                                  f"<code>{esc(str(e)[:120])}</code>", ttl=12)
    if me_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        return await notice(chat, f"{E('warn')} {sc('The bot is in that chat but is not an admin. Promote it, then try again.')}", ttl=10)
    if q_one("SELECT 1 FROM force_chats WHERE chat_id=?", (str(info.id),)):
        await state.clear()
        return await notice(chat, f"{E('warn')} {sc('That chat is already in the list.')}", ttl=6)
    title = info.title or info.username or str(info.id)
    kind = info.type
    url = f"https://t.me/{info.username}" if info.username else None
    if not url:
        try:
            url = (await bot.create_chat_invite_link(info.id, name="Force Sub")).invite_link
        except Exception:
            url = None
    if not url:
        await state.set_state(AdminFlow.chatlink)
        await state.update_data(chat_id=info.id, title=title, kind=kind)
        await sweep(chat)
        return await ask(chat, f"{E('link')} {sc('This chat is private. Send an invite link for it (https://t.me/+...).')}\n"
                               f"{sc('Send /cancel to stop.')}", fs_cancel_kb())
    await state.clear()
    await fs_save(info.id, title, url, kind)
    await fs_show(chat)
    await notice(chat, f"{E('ok')} {sc('Added')} <b>{esc(title)}</b>", ttl=6)


@dp.message(AdminFlow.chatlink)
async def fs_add_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id) or not message.text:
        return
    chat = message.chat.id
    await tidy(message)
    url = message.text.strip()
    if not url.startswith("https://t.me/") and not url.startswith("http://t.me/"):
        return await notice(chat, f"{E('warn')} {sc('Send a valid t.me invite link.')}", ttl=6)
    d = await state.get_data()
    await state.clear()
    await fs_save(d["chat_id"], d["title"], url, d["kind"])
    await fs_show(chat)
    await notice(chat, f"{E('ok')} {sc('Added')} <b>{esc(d['title'])}</b>", ttl=6)


@dp.callback_query(F.data.startswith("fsdel:"))
async def fs_remove(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    fid = int(callback.data.split(":")[1])
    execute("DELETE FROM force_chats WHERE id=?", (fid,))
    _JOINED.clear()
    await safe_edit(callback, fs_text(), fs_kb())
    await callback.answer("Removed.")


# ---- protect settings (no forward / no screenshots) ---------------------------------
def settings_text():
    force = get_setting("force_protect") == "1"
    dflt = get_setting("default_protect") == "1"
    return "\n".join([
        bq(f"{E('shield')} {sc('Protect settings')}"), "",
        row("lock", "Protect All Links", sc("On") if force else sc("Off")),
        row("shield", "Default For New Links", sc("On") if dflt else sc("Off")),
        row("time", "Default Auto-Delete", fmt_secs(int(get_setting("default_autodelete", "0")))),
        SEP,
        f"{E('info')} {sc('Protect = no forwarding and no saving. Telegram also blocks screenshots on apps that support it.')}",
        f"{E('bolt')} {sc('Protect All overrides every link. Each link can also be set on its own.')}",
    ])


def settings_kb():
    force = get_setting("force_protect") == "1"
    dflt = get_setting("default_protect") == "1"
    return admin_back(
        [btn("Protect All: On" if force else "Protect All: Off", "lock",
             style="success" if force else "danger", callback_data="setforce")],
        [btn("New Links: On" if dflt else "New Links: Off", "shield",
             style="success" if dflt else None, callback_data="setprot"),
         btn("Cycle Auto-Delete", "time", callback_data="setauto")])


@dp.callback_query(F.data == "setforce")
async def settings_force(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    set_setting("force_protect", "0" if get_setting("force_protect") == "1" else "1")
    await safe_edit(callback, settings_text(), settings_kb())
    await callback.answer("Updated.")


@dp.callback_query(F.data == "aset")
async def settings_view(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    await safe_edit(callback, settings_text(), settings_kb())
    await callback.answer()


@dp.callback_query(F.data == "setprot")
async def settings_prot(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    set_setting("default_protect", "0" if get_setting("default_protect") == "1" else "1")
    await safe_edit(callback, settings_text(), settings_kb())
    await callback.answer("Updated.")


@dp.callback_query(F.data == "setauto")
async def settings_auto(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    cur = int(get_setting("default_autodelete", "0"))
    nxt = AUTO_STEPS[(AUTO_STEPS.index(cur) + 1) % len(AUTO_STEPS)] if cur in AUTO_STEPS else 0
    set_setting("default_autodelete", nxt)
    await safe_edit(callback, settings_text(), settings_kb())
    await callback.answer(f"Auto-delete: {fmt_secs(nxt)}")


# ---- broadcast ------------------------------------------------------------------
def all_user_ids():
    return [r["user_id"] for r in q_all("SELECT user_id FROM users WHERE banned=0")]


async def run_broadcast(chat, ids, kind, src: Message):
    status = await bot.send_message(chat, f"{E('spin')} {sc('Sending to')} {len(ids)} {sc('users...')}")
    sent = failed = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, src.chat.id, src.message_id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    execute("INSERT INTO broadcasts(admin_id, kind, sent_count, failed_count, created_at) VALUES(?,?,?,?,?)",
            (chat, kind, sent, failed, now_iso()))
    await status.edit_text("\n".join([head("ok", "Broadcast Finished", " ❗️"), "",
                                      row("ok", "Delivered", sent), row("err", "Failed", failed)]))
    spawn(_delete_later(chat, status.message_id, 60))
    await log_channel(f"{E('channel')} Broadcast ({esc(kind)}) ✅ {sent} ❌ {failed}")


@dp.callback_query(F.data == "abc")
async def bc_prompt(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.clear()
    await state.set_state(AdminFlow.broadcast)
    await ask(callback.message.chat.id,
              f"{E('channel')} {sc('Send the broadcast for all users. Text, photo or any media works.')}\n{sc('Send /cancel to stop.')}")
    await callback.answer()


@dp.message(AdminFlow.broadcast)
async def bc_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat = message.chat.id
    await state.clear()
    await sweep(chat)
    await archive_copy(message)
    await run_broadcast(chat, all_user_ids(), "all users", message)
    await tidy(message)


# ---- stats / users / csv / backup / restore -----------------------------------------
@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    c = count
    top = q_all("SELECT title, views FROM links ORDER BY views DESC LIMIT 3")
    lines = [
        bq(f"{E('stats')} {sc('Statistics')}"), "",
        row("group", "Users", c("SELECT COUNT(*) FROM users")),
        row("ban", "Banned", c("SELECT COUNT(*) FROM users WHERE banned=1")),
        row("link", "Links", c("SELECT COUNT(*) FROM links")),
        row("radio", "Active Links", c("SELECT COUNT(*) FROM links WHERE active=1")),
        row("file", "Stored Files", c("SELECT COUNT(*) FROM link_files")),
        row("eye", "Total Opens", c("SELECT COUNT(*) FROM access_log")),
        row("user", "Unique Openers", c("SELECT COUNT(DISTINCT user_id) FROM access_log")),
    ]
    if top:
        lines += [SEP, f"{E('fire')} <b>{sc('Top links')}</b>"]
        for t in top:
            lines.append(f"{E('dot')} {esc(t['title'])} ➛ {t['views']}")
    await safe_edit(callback, "\n".join(lines), admin_back())
    await callback.answer()


@dp.callback_query(F.data == "ausers")
async def admin_users(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    rows = q_all("SELECT user_id, username, first_name FROM users ORDER BY joined_at DESC LIMIT 25")
    lines = [bq(f"{E('group')} {sc('Latest 25 users')}"), ""]
    for r in rows:
        label = f"@{r['username']}" if r["username"] else (r["first_name"] or "unknown")
        lines.append(f"{E('user')} {esc(label)} ➛ <code>{r['user_id']}</code>")
    lines += [SEP, sc("Ban with /ban @user or /ban id, unban with /unban.")]
    await safe_edit(callback, "\n".join(lines), admin_back())
    await callback.answer()


def make_csv(sql, headers, name):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in q_all(sql):
        w.writerow(list(r))
    return BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename=f"{name}_{datetime.now():%Y%m%d_%H%M}.csv")


@dp.callback_query(F.data == "acsv")
async def admin_csv(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    chat = callback.message.chat.id
    await bot.send_document(chat, make_csv(
        "SELECT l.id, l.title, l.token, (SELECT COUNT(*) FROM link_files f WHERE f.link_id=l.id), "
        "l.views, l.active, l.protect, l.autodelete, l.created_at FROM links l ORDER BY l.id",
        ["id", "title", "token", "files", "views", "active", "protect", "autodelete_secs", "created_at"], "links"),
        caption=f"{E('csv')} {sc('Links export')}")
    await bot.send_document(chat, make_csv(
        "SELECT user_id, username, first_name, banned, joined_at, last_seen FROM users ORDER BY joined_at",
        ["user_id", "username", "first_name", "banned", "joined_at", "last_seen"], "users"),
        caption=f"{E('csv')} {sc('Users export')}")
    await callback.answer()


@dp.callback_query(F.data == "abackup")
async def admin_backup(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    await callback.answer("Creating backup...")
    path = await backup_now("manual")
    await bot.send_document(callback.message.chat.id, BufferedInputFile(path.read_bytes(), filename=path.name),
                            caption=f"{E('backup')} {sc('Backup saved on host')}: <code>{esc(path)}</code>")


@dp.callback_query(F.data == "arestore")
async def admin_restore_prompt(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.clear()
    await state.set_state(AdminFlow.restore)
    await ask(callback.message.chat.id, f"{E('restore')} {sc('Send the .db backup file to restore.')}\n"
                                        f"{sc('Current data is backed up first.')}\n{sc('Send /cancel to stop.')}")
    await callback.answer()


@dp.message(AdminFlow.restore)
async def admin_restore_do(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    chat = message.chat.id
    if not message.document:
        await tidy(message)
        return await notice(chat, f"{E('warn')} {sc('Send the .db file as a document.')}", ttl=6)
    tmp = Path(tempfile.gettempdir()) / f"restore_{message.document.file_unique_id}.db"
    await bot.download(message.document, destination=tmp)
    try:
        with closing(sqlite3.connect(tmp)) as c:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "links", "link_files"} <= names:
            raise ValueError("missing tables")
    except Exception:
        tmp.unlink(missing_ok=True)
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('That is not a valid backup file.')}", ttl=8)
    await backup_now("pre-restore")
    shutil.copyfile(tmp, DB_PATH)
    tmp.unlink(missing_ok=True)
    init_db()
    await state.clear()
    await sweep(chat)
    await tidy(message)
    await log_channel(f"{E('restore')} Database restored by {message.from_user.id}")
    await notice(chat, f"{E('ok')} {sc('Database restored successfully.')}", ttl=10)


# ----------------------------------------------------------------------------
# Premium emoji: real start-up test + helper to read emoji IDs
# ----------------------------------------------------------------------------
async def setup_emoji():
    """Checks every custom emoji ID with Telegram and tests whether premium emoji really render in
    messages and on buttons. Anything that does not work is switched off cleanly."""
    global MSG_EMOJI_OK, BTN_ICON_OK
    notes = []
    if not EMOJI_IDS:
        MSG_EMOJI_OK = BTN_ICON_OK = False
        return "Premium emoji: disabled (USE_CUSTOM_EMOJI=0)"
    ids = sorted(set(EMOJI_IDS.values()))
    try:
        valid = set()
        for i in range(0, len(ids), 200):
            for s in await bot.get_custom_emoji_stickers(ids[i:i + 200]):
                valid.add(s.custom_emoji_id)
        for name in [n for n, c in EMOJI_IDS.items() if c not in valid]:
            EMOJI_IDS.pop(name)
            notes.append(f"invalid ID removed: {name}")
    except Exception as e:
        notes.append(f"ID check failed: {e}")
    if not EMOJI_IDS:
        MSG_EMOJI_OK = BTN_ICON_OK = False
        return "Premium emoji: no valid IDs left\n" + "\n".join(notes)
    name, cid = next(iter(EMOJI_IDS.items()))
    MSG_EMOJI_OK = BTN_ICON_OK = False
    try:
        m = await bot.send_message(OWNER_ID, f'<tg-emoji emoji-id="{cid}">{EMOJI.get(name, "•")}</tg-emoji>')
        MSG_EMOJI_OK = any(e.type == "custom_emoji" for e in (m.entities or []))
        await _delete(OWNER_ID, m.message_id)
    except Exception as e:
        notes.append(f"message test failed: {e}")
    if HAS_ICON:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="test", icon_custom_emoji_id=cid)]])
            m = await bot.send_message(OWNER_ID, "test", reply_markup=kb)
            BTN_ICON_OK = True
            await _delete(OWNER_ID, m.message_id)
        except Exception as e:
            notes.append(f"button test failed: {e}")
    else:
        notes.append("buttons: upgrade aiogram to >= 3.25")
    report = [f"Premium emoji in messages: {'ON' if MSG_EMOJI_OK else 'OFF'}",
              f"Premium emoji on buttons: {'ON' if BTN_ICON_OK else 'OFF'}"] + notes
    if not MSG_EMOJI_OK:
        report.append("Telegram only lets a bot show premium emoji in message text if it owns a Fragment username; "
                      "normal emoji are used instead.")
    if not BTN_ICON_OK:
        report.append("Button icons need the bot owner to have Telegram Premium.")
    return "\n".join(report)


def _emoji_ids_in(message: Message):
    ents = (message.entities or []) + (message.caption_entities or [])
    return [e.custom_emoji_id for e in ents if e.type == "custom_emoji"]


def _is_emoji_probe(message: Message):
    return bool(message.from_user and is_admin(message.from_user.id)
                and (message.sticker is not None or _emoji_ids_in(message)))


@dp.message(StateFilter(None), F.func(_is_emoji_probe))
async def emoji_id_helper(message: Message):
    """Admin: send any premium emoji or sticker to the bot and get its ID back."""
    lines = [bq(f"{E('info')} {sc('Emoji / sticker IDs')}"), ""]
    for cid in dict.fromkeys(_emoji_ids_in(message)):
        lines.append(f"{E('dot')} {sc('Emoji ID')}: <code>{cid}</code>")
    if message.sticker:
        s = message.sticker
        if s.custom_emoji_id:
            lines.append(f"{E('dot')} {sc('Emoji ID')}: <code>{s.custom_emoji_id}</code>")
        lines.append(f"{E('dot')} {sc('Sticker file_id')}: <code>{esc(s.file_id)}</code>")
    lines += [SEP, sc("Paste the Emoji ID into EMOJI_IDS in the code.")]
    await message.reply("\n".join(lines))


# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------
async def main():
    global BOT_USERNAME
    init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    # make sure the storage channel works before accepting uploads
    try:
        test = await bot.send_message(STORAGE_CHANNEL_ID, f"{E('ok')} Erebus File Store started")
        await _delete(STORAGE_CHANNEL_ID, test.message_id)
    except Exception as e:
        raise RuntimeError(f"Bot cannot post in STORAGE_CHANNEL_ID ({STORAGE_CHANNEL_ID}). "
                           f"Add the bot as an admin of that private channel. Error: {e}")
    spawn(backup_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    report = await setup_emoji()
    print(report)
    await notice(OWNER_ID, f"{E('info')} <code>{esc(report)}</code>", ttl=60)
    logging.info("File store bot running as @%s - Hosted By Erebus", BOT_USERNAME)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
