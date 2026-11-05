# ⚡ ThunderDL: Free Telegram Downloader Bot

A free Telegram bot that downloads videos, photos and music from social media. It runs **100% free on GitHub Actions**. You don't need a server, a credit card or a laptop that stays on.

Made by **[wondermayank](https://wondermayank.in)**

---

## ✨ Features

- 🎬 Download videos, Shorts, Reels, photos and pins
- 🎚 Quality picker: 360p, 480p, 720p and 1080p
- 🎵 MP3 audio only (`/audio link`) and 🔇 video only
- 🗜 Auto-compress big videos to fit Telegram's 50 MB bot limit
- 🖼 Pinterest images are sent directly, with no video or music buttons
- ⚡ Instant resend of links that were already downloaded
- 🔎 Inline mode: type `@yourbot link` in any chat
- 🔒 Optional "join my channel first" gate
- 📊 Admin commands: `/stats` and `/broadcast`
- 🚨 Errors are sent to the owner with an error code
- 🛡 Rate limit per user, so nobody can abuse your bot
- 🚫 18+ protection: blocks age-restricted and adult-keyword links, bans repeat offenders, and gives you a one-tap **Ban user** button

## ✅ Supported sites

| Works | Not supported |
|---|---|
| 🟢 YouTube (video, Shorts, MP3) | 🔴 Private or login-only posts |
| 🟢 Instagram (reels, posts, photos) | 🔴 Instagram Stories |
| 🟢 TikTok | 🔴 Netflix, Prime Video, Hotstar (DRM) |
| 🟢 X / Twitter | 🔴 Spotify, Apple Music (DRM) |
| 🟢 Facebook | 🔴 Live streams |
| 🟢 Pinterest (videos and images) | |
| 🟢 Reddit, Vimeo, Threads, Twitch, Dailymotion | |

> **YouTube note:** YouTube often blocks GitHub's servers. It usually works only after you add cookies (see [YouTube cookies](#-youtube-cookies-optional)). Other sites mostly work without anything extra.

---

## 🚀 How to host your own copy (step by step)

You need a **GitHub account** and a **Telegram account**. It takes about 15 minutes and you can do all of it from your phone.

### Step 1: Create your bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`, then choose a name and a username (it must end in `bot`).
3. BotFather gives you a **token** that looks like `123456789:ABC...`. Keep it private.
4. Turn on inline mode (optional but recommended):
   - Send `/setinline` and choose your bot.
   - Send `/setinlinefeedback`, choose your bot, then choose **Enabled** (100%).
5. Add the command menu: send `/setcommands`, choose your bot, then paste:
   ```
   start - Welcome and supported sites
   audio - Download audio (MP3) from a link
   ```

### Step 2: Get your own copy of this repo

1. Tap **Fork** at the top right of this page, then **Create fork**.
2. Make sure your fork is **Public**. Public repos get unlimited free GitHub Actions minutes.

### Step 3: Create a private gist (stores users and stats)

1. Go to **https://gist.github.com**.
2. Filename: `data.json`. Content: `{}`
3. Tap the arrow next to the green button and choose **Create secret gist**.
4. Open the gist and copy the long ID at the end of its URL. This is your `GIST_ID`.

> Why a gist? Your repo is public, so saving user IDs in it would expose them. A secret gist stays private.

### Step 4: Create a GitHub token for the gist

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**.
2. **Generate new token (classic)**, tick **only `gist`**, and set a long expiry.
3. Copy the token. GitHub shows it only once.

### Step 5: Add your secrets

In your fork: **Settings → Secrets and variables → Actions → Secrets → New repository secret**.

| Secret name | Value | Required |
|---|---|---|
| `BOT_TOKEN` | Token from BotFather | ✅ Yes |
| `GIST_ID` | ID of your secret gist | ✅ Yes |
| `GIST_TOKEN` | The gist token from Step 4 | ✅ Yes |
| `COOKIES_TXT` | YouTube cookies (see below) | Optional |

### Step 6: Add your own settings (variables)

In the same page, open the **Variables** tab → **New repository variable**.

| Variable name | Value | Notes |
|---|---|---|
| `ADMIN_IDS` | Your Telegram user ID | ✅ **Set this.** Get it from **@userinfobot**. Several admins: `111,222` |
| `CHANNEL_ID` | Your channel ID, for example `-1001234567890` | Set `0` to turn the join gate off |
| `CHANNEL_URL` | `https://t.me/yourchannel` | The join button link |
| `STORAGE_CHAT` | A chat or channel ID | Optional. Used by inline mode; defaults to `LOG_CHAT`, then the first admin |
| `LOG_CHAT` | Your moderation group ID, for example `-1001234567890` | Optional. Every download is copied here so you can spot 18+ content and ban users |

There are no built-in defaults: the bot reads these values only from your variables. With no `CHANNEL_ID` the join gate is off, and with no `LOG_CHAT` nothing is logged.

If you use the join gate, add your bot as an **admin** in that channel. Otherwise it can't check who joined, and it lets everyone in. If you use `LOG_CHAT`, add the bot to that group too.

### Step 7: Start the bot

1. Open the **Actions** tab. If asked, tap **I understand my workflows, go ahead and enable them**.
2. Open **Telegram Downloader Bot** → **Run workflow** → **Run workflow**.
3. Wait about 1 minute, then open your bot on Telegram and send `/start`.

Done. 🎉 The bot now runs for about 6 hours at a time and restarts by itself every 6 hours.

---

## 🍪 YouTube cookies (optional)

YouTube blocks GitHub's servers, so downloads need a signed-in session.

1. Create a **new throwaway Google account**. Don't use your main one.
2. On a computer, open Firefox in a **private window** and install the **Cookie-Editor** extension.
3. Log in to YouTube, then open `youtube.com/robots.txt` in the same tab.
4. In Cookie-Editor, tap **Export** and choose **Netscape** format. Copy the text.
5. Save it as the repo secret `COOKIES_TXT`.
6. Close the private window without logging out.

Cookies expire after some weeks. When YouTube starts failing again, repeat these steps. **Never share your cookies with anyone.**

---

## 🤖 Commands

| Command | Who | What it does |
|---|---|---|
| `/start`, `/help` | Everyone | Welcome message and supported sites |
| `/audio <link>` | Everyone | MP3 only |
| *(send any link)* | Everyone | Shows the quality buttons |
| `/stats` | Admin | Total users, downloads and cached links |
| `/broadcast <text>` | Admin | Message every user of the bot |
| `/ban <user_id>` | Admin | Ban a user |
| `/unban <user_id>` | Admin | Remove a ban and reset strikes |

---

## 🛡 Moderation and privacy

If you set `LOG_CHAT`, the bot sends a copy of every download to that group with the user's name, ID and the link. Use it to spot abuse and ban users with the **🚫 Ban user** button under each log message.

- **18+ blocking:** links that yt-dlp marks as age-restricted, or that contain adult keywords (in the link, title or uploader), are blocked. Each attempt is a strike. After 3 strikes (`STRIKE_LIMIT`) the user is banned automatically. This is a keyword check, so it can miss things and occasionally block harmless content. Review the log group and use `/unban` for mistakes.
- **Tell your users.** The `/start` message already says that downloads are logged for abuse prevention. Keep that line if you use `LOG_CHAT`, and don't use the logs for anything except moderation.
- **Don't forward or share** what ends up in your log group. Delete anything illegal and report it to Telegram.

---

## 🔄 Updating

1. Open `bot.py` in your repo and tap the pencil to edit.
2. Paste the new code and commit.
3. Open the **Actions** tab, cancel the running job and press **Run workflow** to apply it now.

## 🛠 Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't reply | Open the Actions tab. The "Telegram Downloader Bot" run must be green and running. If not, press **Run workflow**. |
| Run fails right away | Check that the `BOT_TOKEN` secret is correct with no extra spaces. |
| Join gate lets everyone in | Add the bot as admin in your channel and check `CHANNEL_ID`. |
| YouTube says "blocked" | Add or refresh `COOKIES_TXT`. |
| `/stats` shows 0 after a restart | Check `GIST_ID` and `GIST_TOKEN`. The token needs the `gist` scope. |
| Inline mode doesn't work | Do the `/setinline` and `/setinlinefeedback` steps in BotFather. |

## ⚠️ Limits and fair use

- Telegram bots can't upload files over **50 MB**, so bigger videos are compressed.
- Messages sent in the short gap between two runs are answered when the next run starts.
- GitHub Actions is meant for building and testing code. Check GitHub's terms before relying on it for a very busy bot.
- Only download content you own or have permission to save. You are responsible for how your bot is used.

---

## 👤 Creator and contact

Made with ❤️ by **wondermayank**

- 🌐 Website: [wondermayank.in](https://wondermayank.in)
- 🐙 GitHub: [github.com/wondermayank](https://github.com/wondermayank)
- 📸 Instagram: [@wondermayank](https://instagram.com/wondermayank)
- 📢 Telegram channel: [@wondermayank](https://t.me/wondermayank)
- 💬 Telegram contact: [@mindmaster_robot](https://t.me/mindmaster_robot)

If this helped you, give the repo a ⭐ and share it with friends.
