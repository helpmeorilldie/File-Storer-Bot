<div align="center">

# 🗂️ ᴇʀᴇʙᴜs ꜰɪʟᴇ sᴛᴏʀᴇ ʙᴏᴛ

### *Share files through secure links, behind a join-to-unlock gate.*

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.25+-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Status](https://img.shields.io/badge/Status-Production_Ready-2ea44f?style=for-the-badge)

**Hosted By Erebus** · Owner **Haze** · [@Noxmirage](https://t.me/Noxmirage)

</div>

---

## ✨ What is this?

Upload any file, video, photo or text once. The bot stores it in a **private Telegram channel** and hands you a **shareable deep link**. When someone opens that link they must **join your channels and groups first**, and only then do the files arrive.

```
 Admin uploads files ──► Private storage channel ──► Deep link created
                                                          │
 User opens link ──► Join all required chats? ──► ❌ Join prompt
                                │ ✅ yes
                                ▼
                        Files delivered instantly
```

---

## 🚀 Features

### 👤 For users
| | |
|---|---|
| 🔒 **Join to unlock** | Opens the link right after they tap **I've Joined**. Nothing is lost |
| ⚡ **Instant delivery** | Every file in the link is sent at once |
| ✉️ **Message admin** | Support with text, photo or any file, replies come back in the bot |
| 🧼 **Clean chat** | Prompts delete themselves, one tidy main menu |

### 🛠 For admins
| | |
|---|---|
| 📤 **Multi-file links** | Send as many files as you want, tap **Done**, get one link |
| 📢 **Force Subscribe manager** | Add or remove **channels and groups** from the panel, no code edits |
| 🛡 **No Forward & No Screenshots** | Toggle per link, or **Protect All** with one switch |
| ⏱ **Auto-delete timer** | Files vanish from the user's chat after 30s up to 1h |
| 🔗 **Link control** | Revoke, re-activate, rename, delete, test-open, see open counts |
| 📣 **Broadcast** | Text, photo or any media to all users |
| 📊 **Statistics** | Users, links, opens, top links |
| 🚫 **Ban / Unban** | `/ban @user` · `/unban 123456` |
| 💾 **Backups** | Hourly database backups sent to the storage channel, one-tap restore |
| 📄 **CSV export** | Links and users |
| 🌟 **Premium emoji** | Auto-tested at start-up, falls back cleanly if unsupported |

---

## 🧭 Admin panel map

```
🛠 Admin Panel
 ├─ 📤 New Link ........... upload files, title, get link
 ├─ 🔗 My Links ........... open, protect, auto-delete, revoke, rename, delete
 ├─ 📣 Broadcast
 ├─ ✉️ Support Inbox
 ├─ 📊 Statistics
 ├─ 👤 Users
 ├─ 📢 Force Subscribe .... add / remove channels & groups
 ├─ 🛡 Protect Settings ... Protect All, new-link default, auto-delete default
 ├─ 📄 Export CSV
 └─ 💾 Backup / ♻️ Restore
```

---

## ⚙️ Setup

### 1️⃣ Create the storage channel
1. Make a **private channel** (this is your vault).
2. Add your bot as **admin** with permission to post.
3. Get its ID (looks like `-1001234567890`), for example by forwarding a post from it to `@userinfobot`.

### 2️⃣ Configure
Rename `.env.example` to `.env`:

```env
BOT_TOKEN=123456:ABC-your-bot-token
OWNER_ID=123456789
ADMIN_IDS=
STORAGE_CHANNEL_ID=-1001234567890
BACKUP_EVERY_HOURS=1
USE_CUSTOM_EMOJI=1
```

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | Token from @BotFather |
| `OWNER_ID` | Your Telegram user ID |
| `ADMIN_IDS` | Extra admins, comma separated (optional) |
| `STORAGE_CHANNEL_ID` | Private vault channel ID, bot must be admin |
| `REQUIRED_CHATS` | *Optional.* Only seeds the list on the very first start. Manage it in the panel afterwards |
| `BACKUP_EVERY_HOURS` | Auto-backup interval |
| `USE_CUSTOM_EMOJI` | `1` premium emoji, `0` normal emoji |

### 3️⃣ Install and run
```bash
pip install -r requirements.txt
python filestore_bot.py
```

---

## 📢 Adding force-subscribe chats

1. Add the bot as **admin** in the channel or group.
2. Open `/admin` → **Force Subscribe** → **Add Channel Or Group**.
3. Do **one** of these:
   - ↪️ Forward any message from that chat to the bot
   - 🔤 Send its `@username`
   - 🔢 Send its numeric ID
4. Private chats: the bot makes an invite link itself, or asks you for one.
5. Tap **Remove** next to a chat any time to take it off the list.

> 💡 The bot must be an **admin** in every force-sub chat, otherwise Telegram will not let it check who has joined.

---

## 🛡 Protect, explained

| Setting | What it does |
|---|---|
| **Per link** | Link page → `No Forward & SS` on or off |
| **New links default** | Protect Settings → `New Links` |
| **Protect All** | Protect Settings → `Protect All`. Overrides every link instantly |

When protection is on, files are sent with Telegram's *protect content* flag: **forwarding and saving are blocked**, and screenshots / screen recording are blocked on apps that support it (Android, iOS, desktop). Nobody can fully stop a photo taken with another phone.

---

## 🎮 Commands

| Command | Who | Action |
|---|---|---|
| `/start` | everyone | Main menu, or opens a link |
| `/admin` | admin | Admin panel |
| `/new` | admin | Start a new link |
| `/done` | admin | Finish uploading |
| `/ban` `/unban` | admin | Manage users |
| `/backup` `/restore` | admin | Database backup and restore |
| `/cancel` | everyone | Cancel the current step |

---

## 🗄 Project layout

```
📁 filestore/
 ├─ 🐍 filestore_bot.py      the whole bot
 ├─ 📄 requirements.txt
 ├─ 🔐 .env                  your secrets (never share)
 ├─ 📁 data/                 SQLite database
 └─ 📁 backups/              last 20 backups
```

---

## 🛟 Troubleshooting

| Problem | Fix |
|---|---|
| *Bot cannot post in STORAGE_CHANNEL_ID* | Make the bot an **admin** of the private channel |
| Users are stuck on "Join To Unlock" | Bot is not admin in a force-sub chat, or the chat ID is wrong |
| Files don't send | Do not delete messages from the storage channel |
| Premium emoji off | Needs Fragment username or owner Premium, normal emoji are used instead |
| Buttons have no colour | Upgrade: `pip install -U "aiogram>=3.25"` |

---

<div align="center">

**Built with 🖤 by Erebus**
Questions? [@Noxmirage](https://t.me/Noxmirage)

</div>
