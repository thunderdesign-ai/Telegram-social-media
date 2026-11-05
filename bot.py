import glob
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
# If ALLOWED_IDS is set, the bot is private. Leave empty for a public bot.
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_IDS", "").split(",") if x.strip()}
# Admins skip the rate limit.
ADMINS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "5"))  # downloads per user per hour
WORKERS = int(os.environ.get("WORKERS", "3"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "21300"))  # ~5h55m
MAX_BYTES = 49 * 1024 * 1024  # Telegram bot upload limit is 50 MB
URL_RE = re.compile(r"https?://\S+")
COOKIES = "cookies.txt" if os.path.exists("cookies.txt") else None
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

# Only these sites are accepted (keeps the bot from being used as a generic fetcher)
DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "facebook.com", "fb.watch", "reddit.com", "pinterest.com",
    "pin.it", "vimeo.com", "threads.net", "twitch.tv", "dailymotion.com",
)

_hits = {}
_lock = threading.Lock()


def allowed_site(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in DOMAINS)


def over_limit(user_id):
    if user_id in ADMINS:
        return False
    now = time.time()
    with _lock:
        recent = [t for t in _hits.get(user_id, []) if now - t < 3600]
        if len(recent) >= RATE_LIMIT:
            _hits[user_id] = recent
            return True
        recent.append(now)
        _hits[user_id] = recent
        return False


def tg(method, files=None, **data):
    try:
        r = requests.post(f"{API}/{method}", data=data, files=files, timeout=300)
        return r.json()
    except Exception as e:
        print("tg error:", e)
        return {}


def say(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")


def download(url, outdir, fmt):
    opts = {
        "outtmpl": os.path.join(outdir, "%(autonumber)s_%(id)s.%(ext)s"),
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": False,
        "playlistend": 10,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 200 * 1024 * 1024,
        "socket_timeout": 30,
    }
    if COOKIES:
        opts["cookiefile"] = COOKIES
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    return sorted(glob.glob(os.path.join(outdir, "*")))


def send_file(chat_id, path):
    ext = os.path.splitext(path)[1].lower()
    if os.path.getsize(path) > MAX_BYTES:
        return False
    method, field = "sendDocument", "document"
    if ext in IMG_EXT:
        method, field = "sendPhoto", "photo"
    elif ext in VID_EXT:
        method, field = "sendVideo", "video"
    with open(path, "rb") as f:
        res = tg(method, files={field: f}, chat_id=chat_id, supports_streaming="true")
    return bool(res.get("ok"))


def handle(update):
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    user_id = (msg.get("from") or {}).get("id")
    text = msg.get("text") or ""
    if not chat_id:
        return
    if ALLOWED and user_id not in ALLOWED:
        say(chat_id, "Sorry, this bot is private.")
        return
    if text.startswith("/start") or text.startswith("/help"):
        say(chat_id, "Send me a link from YouTube, Instagram, TikTok, X/Twitter, "
                     "Facebook, Reddit, Pinterest and more, and I'll download it.\n"
                     f"Limit: {RATE_LIMIT} downloads per hour, files up to 50 MB.")
        return
    urls = URL_RE.findall(text)
    if not urls:
        say(chat_id, "Please send a valid link.")
        return
    url = urls[0]
    if not allowed_site(url):
        say(chat_id, "That site isn't supported.")
        return
    if over_limit(user_id):
        say(chat_id, f"Limit reached ({RATE_LIMIT}/hour). Please try again later.")
        return
    say(chat_id, "Downloading...")
    tmp = tempfile.mkdtemp()
    try:
        try:
            files = download(url, tmp, "bv*[height<=720]+ba/b[height<=720]/b")
            if any(os.path.getsize(f) > MAX_BYTES for f in files):
                for f in files:
                    os.remove(f)
                files = download(url, tmp, "worst[ext=mp4]/worst")
        except Exception as e:
            say(chat_id, f"Failed: {str(e)[:300]}")
            return
        if not files:
            say(chat_id, "Nothing found to download.")
            return
        sent = sum(1 for f in files if send_file(chat_id, f))
        if sent == 0:
            say(chat_id, "File is too big for Telegram (50 MB limit) or upload failed.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def safe_handle(update):
    try:
        handle(update)
    except Exception as e:
        print("handle error:", e)


def main():
    tg("deleteWebhook")
    end = time.time() + RUN_SECONDS
    offset = None
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    print("Bot started")
    while time.time() < end:
        params = {"timeout": 30, "allowed_updates": json.dumps(["message"])}
        if offset:
            params["offset"] = offset
        try:
            r = requests.get(f"{API}/getUpdates", params=params, timeout=45).json()
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            pool.submit(safe_handle, u)
    pool.shutdown(wait=True)
    if offset:
        requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=30)
    print("Run finished")


if __name__ == "__main__":
    main()
