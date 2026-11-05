import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
# All of these come from environment variables (GitHub repo Variables). No defaults on purpose.
ADMINS = {int(x) for x in (os.environ.get("ADMIN_IDS") or "").split(",") if x.strip()}
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or "0")  # 0 = join gate off
CHANNEL_URL = os.environ.get("CHANNEL_URL") or ""
LOG_CHAT = int(os.environ.get("LOG_CHAT") or "0")  # moderation log group (0 = off)
STORAGE_CHAT = int(os.environ.get("STORAGE_CHAT") or LOG_CHAT or (next(iter(ADMINS)) if ADMINS else 0))
STRIKE_LIMIT = int(os.environ.get("STRIKE_LIMIT") or "3")  # 18+ attempts before an automatic ban
GIST_ID = os.environ.get("GIST_ID", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT") or "5")  # downloads per user per hour
WORKERS = int(os.environ.get("WORKERS") or "3")
RUN_SECONDS = int(os.environ.get("RUN_SECONDS") or "21300")
MAX_BYTES = 49 * 1024 * 1024
URL_RE = re.compile(r"https?://\S+")
COOKIES = "cookies.txt" if os.path.exists("cookies.txt") else None
IMG = {".jpg", ".jpeg", ".png", ".webp"}
VID = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUD = {".mp3", ".m4a", ".ogg", ".opus"}
DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "facebook.com", "fb.watch", "reddit.com", "pinterest.com",
    "pin.it", "vimeo.com", "threads.net", "twitch.tv", "dailymotion.com",
)
FOOTER = "\n\n✨ Thank you for using ThunderDL by wondermayank.in"
WEBSITE = "https://wondermayank.in"
CONTACT = "https://t.me/mindmaster_robot"
MADE_BY = "https://t.me/wondermayank"
METHOD = {"photo": "sendPhoto", "video": "sendVideo", "audio": "sendAudio", "document": "sendDocument"}

lock = threading.Lock()
data = {"users": {}, "downloads": 0, "cache": {}, "banned": {}, "strikes": {}}
dirty = False
hits = {}
joined_cache = {}
BOT_USERNAME = ""


# ---------- Telegram helpers ----------
def tg(method, files=None, **kw):
    for k, v in list(kw.items()):
        if isinstance(v, (dict, list)):
            kw[k] = json.dumps(v)
    try:
        return requests.post(f"{API}/{method}", data=kw, files=files, timeout=300).json()
    except Exception as e:
        print("tg error:", method, e)
        return {}


def say(chat_id, text, **kw):
    return tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true", **kw)


# ---------- Persistent data (private GitHub Gist) ----------
def gh():
    return {"Authorization": f"Bearer {GIST_TOKEN}", "Accept": "application/vnd.github+json"}


def load_data():
    if not (GIST_ID and GIST_TOKEN):
        print("No GIST_ID/GIST_TOKEN: data will not persist")
        return
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=gh(), timeout=30).json()
        f = r["files"]["data.json"]
        text = f["content"] if not f.get("truncated") else requests.get(f["raw_url"], headers=gh(), timeout=30).text
        loaded = json.loads(text or "{}")
        data.update(loaded)
        for k, v in (("users", {}), ("downloads", 0), ("cache", {}), ("banned", {}), ("strikes", {})):
            data.setdefault(k, v)
        print("Loaded data:", len(data["users"]), "users")
    except Exception as e:
        print("load_data error:", e)


def save_data():
    global dirty
    if not (GIST_ID and GIST_TOKEN):
        return
    with lock:
        if not dirty:
            return
        body = json.dumps(data)
        dirty = False
    try:
        r = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=gh(), timeout=60,
                           json={"files": {"data.json": {"content": body}}})
        if r.status_code >= 300:
            raise RuntimeError(r.text[:200])
    except Exception as e:
        print("save_data error:", e)
        with lock:
            dirty = True


def persist_loop(stop):
    while not stop.wait(300):
        save_data()


def touch():
    global dirty
    dirty = True


def register(uid):
    with lock:
        if str(uid) not in data["users"]:
            data["users"][str(uid)] = int(time.time())
            touch()


def cache_get(url, mode):
    return data["cache"].get(f"{mode}|{url}")


def cache_put(url, mode, entry):
    with lock:
        c = data["cache"]
        c.pop(f"{mode}|{url}", None)
        c[f"{mode}|{url}"] = entry
        while len(c) > 1500:
            c.pop(next(iter(c)))
        touch()


def count_download():
    with lock:
        data["downloads"] += 1
        touch()


# ---------- Moderation ----------
ADULT_RE = re.compile(
    r"\b(porn\w*|xvideos|xnxx|xhamster|onlyfans|hentai|nsfw|xxx|sex[\s_-]?tape|erotic)\b", re.I)


def adult_reason(url, title="", uploader="", age=0):
    if age and age >= 18:
        return "age-restricted (18+)"
    if ADULT_RE.search(f"{url} {title} {uploader}"):
        return "adult keyword"
    return ""


def is_banned(uid):
    return str(uid) in data["banned"] and uid not in ADMINS


def ban(uid):
    if uid in ADMINS:
        return
    with lock:
        data["banned"][str(uid)] = int(time.time())
        touch()


def unban(uid):
    with lock:
        data["banned"].pop(str(uid), None)
        data["strikes"].pop(str(uid), None)
        touch()


def who(user):
    name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x) or "-"
    uname = f" @{user['username']}" if user.get("username") else ""
    return f"{name}{uname} ({user.get('id')})"


def ban_button(uid):
    return {"inline_keyboard": [[{"text": "🚫 Ban user", "callback_data": f"ban:{uid}"}]]}


def log_download(user, url, mode, items):
    """Copy of each download goes to the moderation group (users are told about this in /start)."""
    if not LOG_CHAT or not items:
        return
    cap = f"📥 {who(user)}\n🎚 {mode}\n🔗 {url}"[:1000]
    for i, it in enumerate(items[:10]):
        extra = {"caption": cap, "reply_markup": ban_button(user["id"])} if i == 0 else {}
        tg(METHOD[it["kind"]], chat_id=LOG_CHAT, **{it["kind"]: it["file_id"]}, **extra)


def flag(chat_id, user, url, reason):
    """18+ content: block it, add a strike, auto-ban after STRIKE_LIMIT, alert the owner."""
    uid = user["id"]
    with lock:
        n = data["strikes"].get(str(uid), 0) + 1
        data["strikes"][str(uid)] = n
        touch()
    banned_now = False
    if n >= STRIKE_LIMIT and uid not in ADMINS:
        ban(uid)
        banned_now = True
    if chat_id:
        if banned_now:
            say(chat_id, "🚫 You have been banned for repeated 18+ requests. "
                         "If this is a mistake, contact @mindmaster_robot.")
        else:
            say(chat_id, f"🚫 18+ content is not allowed. Warning {n}/{STRIKE_LIMIT}.")
    alert = (f"🚨 18+ blocked ({reason})\nUser: {who(user)}\nStrikes: {n}/{STRIKE_LIMIT}"
             f"{' (auto-banned)' if banned_now else ''}\nLink: {url}")
    if LOG_CHAT:
        say(LOG_CHAT, alert, reply_markup=ban_button(uid))
    else:
        for a in ADMINS:
            say(a, alert, reply_markup=ban_button(uid))


# ---------- Rules ----------
def allowed_site(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in DOMAINS)


def over_limit(uid):
    if uid in ADMINS:
        return False
    now = time.time()
    with lock:
        recent = [t for t in hits.get(uid, []) if now - t < 3600]
        if len(recent) >= RATE_LIMIT:
            hits[uid] = recent
            return True
        recent.append(now)
        hits[uid] = recent
        return False


def is_joined(uid):
    if uid in ADMINS or not CHANNEL_ID:  # CHANNEL_ID=0 turns the join gate off
        return True
    ts = joined_cache.get(uid)
    if ts and time.time() - ts < 600:
        return True
    res = tg("getChatMember", chat_id=CHANNEL_ID, user_id=uid)
    if not res.get("ok"):
        print("getChatMember failed (is the bot admin in the channel?):", res.get("description"))
        return True  # fail open so users are not locked out
    if res["result"]["status"] in ("member", "administrator", "creator"):
        joined_cache[uid] = time.time()
        return True
    return False


def gate(chat_id, uid):
    if is_joined(uid):
        return True
    say(chat_id, "Please join our channel to use this bot, then tap \"I've joined\".",
        reply_markup={"inline_keyboard": [
            [{"text": "📢 Join channel", "url": CHANNEL_URL}],
            [{"text": "✅ I've joined", "callback_data": "chk"}]]})
    return False


# ---------- Download / compress ----------
def kind_of(path):
    e = os.path.splitext(path)[1].lower()
    return "photo" if e in IMG else "video" if e in VID else "audio" if e in AUD else "document"


def download(url, outdir, mode):
    opts = {
        "outtmpl": os.path.join(outdir, "%(autonumber)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "playlistend": 10 if "instagram" in url else 3,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": 300 * 1024 * 1024,
        "socket_timeout": 30,
    }
    if COOKIES:
        opts["cookiefile"] = COOKIES
    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    elif mode == "mute":
        opts["format"] = "bv*[height<=720]/bv*/b"
        opts["merge_output_format"] = "mp4"
    else:
        h = int(mode)
        opts["format"] = f"bv*[height<={h}]+ba/b[height<={h}]/b"
        opts["merge_output_format"] = "mp4"
    with YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    first = info
    if info.get("entries"):
        first = next((e for e in info["entries"] if e), {})
    title = first.get("title") or info.get("title") or ""
    uploader = first.get("uploader") or info.get("uploader") or ""
    age = first.get("age_limit") or info.get("age_limit") or 0
    return sorted(glob.glob(os.path.join(outdir, "*"))), title, uploader, age


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def meta(html_text, prop):
    for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s["\']'):
        m = re.search(pat % prop, html_text)
        if m:
            return html.unescape(m.group(1))
    return ""


def page_fallback(url, outdir):
    """For image posts yt-dlp can't handle (Pinterest, Instagram photos...): grab the page's main image."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    text = r.text
    img = meta(text, "og:image")
    if not img:
        raise RuntimeError("no image found")
    candidates = [img]
    if "pinimg.com" in img:
        candidates.insert(0, re.sub(r"/\d+x(\d+)?/", "/originals/", img))
    for c in candidates:
        resp = requests.get(c, headers={"User-Agent": UA}, timeout=60)
        if resp.status_code == 200 and resp.content:
            ext = os.path.splitext(urlparse(c).path)[1] or ".jpg"
            path = os.path.join(outdir, "image" + ext)
            with open(path, "wb") as f:
                f.write(resp.content)
            return [path], meta(text, "og:title"), "", 0
    raise RuntimeError("image download failed")


last_report = {}


def new_code(raw):
    return "E" + hashlib.md5((raw + str(time.time())).encode()).hexdigest()[:5].upper()


def report_error(code, where, uid, url, raw):
    """Send the error details to the owner (same error is grouped for 2 minutes)."""
    key = raw[:80]
    now = time.time()
    with lock:
        if now - last_report.get(key, 0) < 120:
            return
        last_report[key] = now
    text = f"⚠️ ThunderDL error {code}\nWhere: {where}\nUser: {uid}\nLink: {url}\n\n{raw[:1500]}"
    for a in ADMINS:
        say(a, text)


def is_image_only(url):
    """True when the post is a plain image (so we skip the video/audio buttons)."""
    host = (urlparse(url).hostname or "").lower()
    if not ("pinterest" in host or host == "pin.it" or host.endswith(".pin.it") or "instagram.com" in host):
        return False
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}) as y:
            info = y.extract_info(url, download=False)
        first = info
        if info.get("entries"):
            first = next((e for e in info["entries"] if e), {})
        return not first.get("formats") and not first.get("url")
    except Exception as e:
        msg = str(e).lower()
        if "pinterest" in host or host.endswith("pin.it"):
            return True
        return "no video" in msg


def compress(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
        dur = float(out.stdout.strip())
        vb = int(44 * 1024 * 1024 * 8 / dur) - 96000
        if vb < 120000:
            return None
        dst = path.rsplit(".", 1)[0] + "_c.mp4"
        subprocess.run(["ffmpeg", "-y", "-i", path, "-vf", "scale=-2:'min(720,ih)'",
                        "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(vb),
                        "-maxrate", str(int(vb * 1.3)), "-bufsize", str(vb * 2),
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst],
                       capture_output=True, timeout=1200)
        if os.path.exists(dst) and os.path.getsize(dst) <= MAX_BYTES:
            return dst
    except Exception as e:
        print("compress error:", e)
    return None


def prepare(url, mode, tmp):
    """Returns (files, caption, error). error is None or {"user", "raw", "report"}."""
    host = (urlparse(url).hostname or "").lower()
    files, title, uploader, age = None, "", "", 0
    if mode == "image":
        try:
            files, title, uploader, age = page_fallback(url, tmp)
        except Exception as e:
            return [], "", {"user": "Couldn't fetch this image.", "raw": str(e), "report": True}
    else:
        try:
            files, title, uploader, age = download(url, tmp, mode)
        except Exception as e:
            raw = str(e)
            if mode not in ("audio", "mute") and "youtu" not in host:
                try:
                    files, title, uploader, age = page_fallback(url, tmp)
                except Exception as e2:
                    print("fallback failed:", e2)
            if not files:
                if "youtu" in host and ("Sign in" in raw or "bot" in raw):
                    return [], "", {"user": "YouTube is blocking this server right now. Please try again later.",
                                    "raw": raw, "report": True}
                return [], "", {"user": "Couldn't download this link. It may be private, removed or unsupported.",
                                "raw": raw, "report": True}
    if not files:
        return [], "", {"user": "Nothing found to download.", "raw": "no files", "report": False}
    reason = adult_reason(url, title, uploader, age)
    if reason:
        return [], "", {"user": "", "raw": reason, "report": False, "flag": reason}
    ready = []
    for f in files:
        if os.path.getsize(f) > MAX_BYTES and kind_of(f) == "video":
            f = compress(f) or f
        if os.path.getsize(f) <= MAX_BYTES:
            ready.append(f)
    if not ready:
        return [], "", {"user": "This file is too big for Telegram (50 MB) even after compression.",
                        "raw": "too big", "report": False}
    lines = [title] if title else []
    if uploader:
        lines.append(f"👤 {uploader}")
    lines.append(f"🔗 {url}")
    caption = "\n".join(lines)[:880] + FOOTER
    return ready, caption, None


def send_file(chat_id, path, caption):
    kind = kind_of(path)
    extra = {"supports_streaming": "true"} if kind == "video" else {}
    with open(path, "rb") as f:
        res = tg(METHOD[kind], files={kind: f}, chat_id=chat_id, caption=caption, **extra)
    if not res.get("ok"):
        return None
    m = res["result"]
    fid = m["photo"][-1]["file_id"] if kind == "photo" else m[kind]["file_id"]
    return {"kind": kind, "file_id": fid, "caption": caption}


# ---------- Core actions ----------
def fail(chat_id, user, url, err, where="download"):
    if err.get("flag"):
        flag(chat_id, user, url, err["flag"])
        return
    if err.get("report"):
        code = new_code(err["raw"])
        say(chat_id, f"❌ {err['user']}\nError code: {code}\nStill stuck? Contact @mindmaster_robot")
        report_error(code, where, user["id"], url, err["raw"])
    else:
        say(chat_id, f"❌ {err['user']}")


def deliver(chat_id, uid, url, mode, user=None):
    user = user or {"id": uid}
    c = cache_get(url, mode)
    if c:
        res = tg(METHOD[c["kind"]], chat_id=chat_id, caption=c["caption"], **{c["kind"]: c["file_id"]})
        if res.get("ok"):
            count_download()
            log_download(user, url, mode, [c])
            return
    if over_limit(uid):
        say(chat_id, f"Limit reached ({RATE_LIMIT}/hour). Please try again later.")
        return
    status = say(chat_id, "⏳ Downloading...")
    tmp = tempfile.mkdtemp()
    try:
        files, caption, err = prepare(url, mode, tmp)
        if err:
            fail(chat_id, user, url, err)
            return
        sent = []
        for i, f in enumerate(files):
            r = send_file(chat_id, f, caption if i == 0 else "")
            if r:
                sent.append(r)
        if not sent:
            fail(chat_id, user, url, {"user": "Upload to Telegram failed.", "raw": "send_file returned nothing",
                                      "report": True}, "upload")
            return
        count_download()
        log_download(user, url, mode, sent)
        if len(files) == 1:
            cache_put(url, mode, sent[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        mid = (status.get("result") or {}).get("message_id")
        if mid:
            tg("deleteMessage", chat_id=chat_id, message_id=mid)


def picker(chat_id, msg_id):
    say(chat_id, "Choose quality:", reply_to_message_id=msg_id, reply_markup={"inline_keyboard": [
        [{"text": "360p", "callback_data": "q:360"}, {"text": "480p", "callback_data": "q:480"},
         {"text": "720p", "callback_data": "q:720"}, {"text": "1080p", "callback_data": "q:1080"}],
        [{"text": "🎵 Audio (MP3)", "callback_data": "q:audio"},
         {"text": "🔇 Video only", "callback_data": "q:mute"}]]})


def broadcast(admin_chat, text):
    ok = fail = 0
    for uid in list(data["users"]):
        r = tg("sendMessage", chat_id=int(uid), text=text)
        if r.get("ok"):
            ok += 1
        else:
            fail += 1
            if r.get("error_code") == 403:
                with lock:
                    data["users"].pop(uid, None)
                    touch()
        time.sleep(0.05)
    say(admin_chat, f"Broadcast done: {ok} sent, {fail} failed.")


def start_text():
    return (
        "👋 <b>Welcome to ThunderDL!</b>\n"
        "Send me a link and I'll download it for you. Free, fast, no login.\n\n"
        "<b>Supported</b>\n"
        "🟢 YouTube: video, Shorts, MP3\n"
        "🟢 Instagram: reels, posts, photos\n"
        "🟢 TikTok\n"
        "🟢 X / Twitter\n"
        "🟢 Facebook\n"
        "🟢 Pinterest: videos and images\n"
        "🟢 Reddit\n"
        "🟢 Vimeo, Threads, Twitch, Dailymotion\n\n"
        "<b>Not supported</b>\n"
        "🔴 Private or login-only posts\n"
        "🔴 Instagram Stories\n"
        "🔴 Netflix, Prime Video, Hotstar (DRM)\n"
        "🔴 Spotify and Apple Music (DRM)\n"
        "🔴 Live streams\n\n"
        "🎵 Use <code>/audio link</code> for MP3 only.\n"
        f"📦 Limit: {RATE_LIMIT} downloads per hour, files up to 50 MB (bigger videos are compressed).\n"
        "🔒 Downloads are logged for abuse prevention. 18+ content is blocked and repeat attempts get banned.\n\n"
        "Made with ❤️ by @wondermayank"
    )


START_KEYBOARD = {"inline_keyboard": [
    [{"text": "🌐 Website", "url": WEBSITE}, {"text": "💬 Contact", "url": CONTACT}],
    [{"text": "👨‍💻 Made by @wondermayank", "url": MADE_BY}]]}


def handle_message(msg):
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    uid = (msg.get("from") or {}).get("id")
    text = msg.get("text") or ""
    if not chat_id or not uid:
        return
    if is_banned(uid):
        say(chat_id, "🚫 You are banned from using this bot. Contact @mindmaster_robot if this is a mistake.")
        return
    user = msg.get("from") or {"id": uid}
    if chat.get("type") == "private":
        register(chat_id)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        say(chat_id, start_text(), parse_mode="HTML", reply_markup=START_KEYBOARD)
        return
    if cmd == "/stats" and uid in ADMINS:
        day = time.time() - 86400
        new = sum(1 for t in data["users"].values() if t > day)
        say(chat_id, f"👥 Users: {len(data['users'])} (+{new} in 24h)\n"
                     f"⬇️ Downloads: {data['downloads']}\n🗂 Cached links: {len(data['cache'])}\n"
                     f"🚫 Banned: {len(data['banned'])}")
        return
    if cmd == "/broadcast" and uid in ADMINS:
        body = text.partition(" ")[2].strip()
        if body:
            broadcast(chat_id, body)
        else:
            say(chat_id, "Usage: /broadcast your message")
        return
    if cmd in ("/ban", "/unban") and uid in ADMINS:
        arg = text.partition(" ")[2].strip()
        if not arg.lstrip("-").isdigit():
            say(chat_id, f"Usage: {cmd} <user_id>")
            return
        if cmd == "/ban":
            ban(int(arg))
            say(chat_id, f"🚫 Banned {arg}")
        else:
            unban(int(arg))
            say(chat_id, f"✅ Unbanned {arg}")
        return
    urls = URL_RE.findall(text)
    if not urls:
        say(chat_id, "Please send a valid link.")
        return
    if not gate(chat_id, uid):
        return
    url = urls[0].rstrip(").,")
    if not allowed_site(url):
        say(chat_id, "That site isn't supported.")
        return
    reason = adult_reason(url)
    if reason:
        flag(chat_id, user, url, reason)
        return
    if cmd == "/audio":
        deliver(chat_id, uid, url, "audio", user)
    elif is_image_only(url):
        deliver(chat_id, uid, url, "image", user)
    else:
        picker(chat_id, msg["message_id"])


def handle_callback(cb):
    uid = cb["from"]["id"]
    d = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if d.startswith("ban:"):
        ok = uid in ADMINS and d[4:].lstrip("-").isdigit()
        if ok:
            ban(int(d[4:]))
        tg("answerCallbackQuery", callback_query_id=cb["id"], show_alert="true",
           text=f"🚫 Banned {d[4:]}" if ok else "Admins only.")
        return
    if is_banned(uid):
        tg("answerCallbackQuery", callback_query_id=cb["id"], text="You are banned.", show_alert="true")
        return
    if d == "chk":
        ok = is_joined(uid)
        tg("answerCallbackQuery", callback_query_id=cb["id"],
           text="Thanks! Now send a link." if ok else "You haven't joined yet.", show_alert="true" if not ok else "false")
        return
    tg("answerCallbackQuery", callback_query_id=cb["id"])
    if not d.startswith("q:") or not chat_id:
        return
    if not gate(chat_id, uid):
        return
    src = (msg.get("reply_to_message") or {}).get("text") or ""
    urls = URL_RE.findall(src)
    if not urls:
        say(chat_id, "That request expired. Please send the link again.")
        return
    mode = d[2:]
    if mode not in ("360", "480", "720", "1080", "audio", "mute"):
        return
    tg("deleteMessage", chat_id=chat_id, message_id=msg["message_id"])
    deliver(chat_id, uid, urls[0].rstrip(").,"), mode, cb["from"])


# ---------- Inline mode ----------
def handle_inline_query(q):
    urls = URL_RE.findall(q.get("query", ""))
    if is_banned(q["from"]["id"]) or not urls or not allowed_site(urls[0]):
        tg("answerInlineQuery", inline_query_id=q["id"], results=[], cache_time=5,
           button={"text": "Paste a video link", "start_parameter": "inline"})
        return
    url = urls[0].rstrip(").,")
    rid = hashlib.md5(url.encode()).hexdigest()
    c = cache_get(url, "720")
    if c and c["kind"] in ("video", "photo"):
        if c["kind"] == "video":
            res = {"type": "video", "id": rid, "video_file_id": c["file_id"], "title": "Send video",
                   "caption": c["caption"]}
        else:
            res = {"type": "photo", "id": rid, "photo_file_id": c["file_id"], "caption": c["caption"]}
    else:
        res = {"type": "article", "id": rid, "title": "⬇️ Download this link", "description": url[:80],
               "input_message_content": {"message_text": "⏳ Downloading... " + url, "disable_web_page_preview": True},
               "reply_markup": {"inline_keyboard": [[{"text": "Processing...", "callback_data": "noop"}]]}}
    tg("answerInlineQuery", inline_query_id=q["id"], results=[res], cache_time=5, is_personal="true")


def handle_chosen(ch):
    imid = ch.get("inline_message_id")
    urls = URL_RE.findall(ch.get("query", ""))
    if not imid or not urls:
        return
    uid = ch["from"]["id"]
    user = ch["from"]
    url = urls[0].rstrip(").,")
    if is_banned(uid):
        tg("editMessageText", inline_message_id=imid, text="🚫 You are banned from using this bot.")
        return
    reason = adult_reason(url)
    if reason:
        tg("editMessageText", inline_message_id=imid, text="🚫 18+ content is not allowed.")
        flag(None, user, url, reason)
        return
    if not is_joined(uid):
        tg("editMessageText", inline_message_id=imid, text="Please join " + CHANNEL_URL + " to use this bot.")
        return
    if over_limit(uid):
        tg("editMessageText", inline_message_id=imid, text=f"Limit reached ({RATE_LIMIT}/hour).")
        return
    tmp = tempfile.mkdtemp()
    try:
        files, caption, err = prepare(url, "720", tmp)
        if err and err.get("flag"):
            tg("editMessageText", inline_message_id=imid, text="🚫 18+ content is not allowed.")
            flag(None, user, url, err["flag"])
            return
        if err:
            code = new_code(err["raw"]) if err.get("report") else ""
            tg("editMessageText", inline_message_id=imid,
               text=f"❌ {err['user']}" + (f"\nError code: {code}" if code else ""))
            if code:
                report_error(code, "inline", uid, url, err["raw"])
            return
        r = send_file(STORAGE_CHAT, files[0], caption)
        if not r:
            tg("editMessageText", inline_message_id=imid, text="Upload failed.")
            return
        count_download()
        log_download(user, url, "720 (inline)", [r])
        cache_put(url, "720", r)
        tg("editMessageMedia", inline_message_id=imid,
           media={"type": r["kind"], "media": r["file_id"], "caption": caption})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def safe(fn, arg):
    try:
        fn(arg)
    except Exception as e:
        print("handler error:", fn.__name__, e)
        raw = traceback.format_exc()[-1200:]
        report_error(new_code(raw), fn.__name__, "-", "-", raw)


def dispatch(u, pool):
    if "message" in u:
        pool.submit(safe, handle_message, u["message"])
    elif "callback_query" in u:
        pool.submit(safe, handle_callback, u["callback_query"])
    elif "inline_query" in u:
        pool.submit(safe, handle_inline_query, u["inline_query"])
    elif "chosen_inline_result" in u:
        pool.submit(safe, handle_chosen, u["chosen_inline_result"])


def main():
    global BOT_USERNAME
    tg("deleteWebhook")
    BOT_USERNAME = (tg("getMe").get("result") or {}).get("username", "")
    load_data()
    stop = threading.Event()
    threading.Thread(target=persist_loop, args=(stop,), daemon=True).start()
    pool = ThreadPoolExecutor(max_workers=WORKERS + 2)
    end = time.time() + RUN_SECONDS
    offset = None
    print("Bot started as @" + BOT_USERNAME)
    allowed = ["message", "callback_query", "inline_query", "chosen_inline_result"]
    while time.time() < end:
        params = {"timeout": 30, "allowed_updates": json.dumps(allowed)}
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
            dispatch(u, pool)
    pool.shutdown(wait=True)
    stop.set()
    save_data()
    if offset:
        requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=30)
    print("Run finished")


if __name__ == "__main__":
    main()
