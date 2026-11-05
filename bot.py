import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
ADMINS = {int(x) for x in (os.environ.get("ADMIN_IDS") or "1215136695").split(",") if x.strip()}
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or "-1001947409796")
CHANNEL_URL = os.environ.get("CHANNEL_URL") or "https://t.me/wondermayank"
STORAGE_CHAT = int(os.environ.get("STORAGE_CHAT") or next(iter(ADMINS)))  # used by inline mode
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
METHOD = {"photo": "sendPhoto", "video": "sendVideo", "audio": "sendAudio", "document": "sendDocument"}

lock = threading.Lock()
data = {"users": {}, "downloads": 0, "cache": {}}
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
        for k, v in (("users", {}), ("downloads", 0), ("cache", {})):
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
    if uid in ADMINS:
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
    return sorted(glob.glob(os.path.join(outdir, "*"))), title, uploader


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
    try:
        files, title, uploader = download(url, tmp, mode)
    except Exception as e:
        return [], "", f"Failed: {str(e)[:300]}"
    if not files:
        return [], "", "Nothing found to download."
    ready = []
    for f in files:
        if os.path.getsize(f) > MAX_BYTES and kind_of(f) == "video":
            f = compress(f) or f
        if os.path.getsize(f) <= MAX_BYTES:
            ready.append(f)
    if not ready:
        return [], "", "File is too big for Telegram (50 MB) even after compression."
    caption = f"{title}\n👤 {uploader}\n🔗 {url}" if title else f"🔗 {url}"
    return ready, caption[:1000], None


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
def deliver(chat_id, uid, url, mode):
    c = cache_get(url, mode)
    if c:
        res = tg(METHOD[c["kind"]], chat_id=chat_id, caption=c["caption"], **{c["kind"]: c["file_id"]})
        if res.get("ok"):
            count_download()
            return
    if over_limit(uid):
        say(chat_id, f"Limit reached ({RATE_LIMIT}/hour). Please try again later.")
        return
    status = say(chat_id, "⏳ Downloading...")
    tmp = tempfile.mkdtemp()
    try:
        files, caption, err = prepare(url, mode, tmp)
        if err:
            say(chat_id, err)
            return
        sent = []
        for i, f in enumerate(files):
            r = send_file(chat_id, f, caption if i == 0 else "")
            if r:
                sent.append(r)
        if not sent:
            say(chat_id, "Upload failed. Please try again.")
            return
        count_download()
        if len(files) == 1:
            cache_put(url, mode, sent[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        mid = (status.get("result") or {}).get("message_id")
        if mid:
            tg("deleteMessage", chat_id=chat_id, message_id=mid)


def picker(chat_id, msg_id):
    say(chat_id, "Choose quality:", reply_to_message_id=msg_id, reply_markup={"inline_keyboard": [
        [{"text": "360p", "callback_data": "q:360"}, {"text": "720p", "callback_data": "q:720"},
         {"text": "1080p", "callback_data": "q:1080"}],
        [{"text": "🎵 Audio (MP3)", "callback_data": "q:audio"}]]})


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


HELP = ("Send me a link from YouTube, Instagram, TikTok, X, Facebook, Reddit, Pinterest and more.\n\n"
        "/audio <link> - MP3 only\n"
        f"Limit: {RATE_LIMIT} downloads per hour, files are compressed to fit 50 MB.")


def handle_message(msg):
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    uid = (msg.get("from") or {}).get("id")
    text = msg.get("text") or ""
    if not chat_id or not uid:
        return
    if chat.get("type") == "private":
        register(chat_id)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if cmd in ("/start", "/help"):
        say(chat_id, HELP)
        return
    if cmd == "/stats" and uid in ADMINS:
        day = time.time() - 86400
        new = sum(1 for t in data["users"].values() if t > day)
        say(chat_id, f"👥 Users: {len(data['users'])} (+{new} in 24h)\n"
                     f"⬇️ Downloads: {data['downloads']}\n🗂 Cached links: {len(data['cache'])}")
        return
    if cmd == "/broadcast" and uid in ADMINS:
        body = text.partition(" ")[2].strip()
        if body:
            broadcast(chat_id, body)
        else:
            say(chat_id, "Usage: /broadcast your message")
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
    if cmd == "/audio":
        deliver(chat_id, uid, url, "audio")
    else:
        picker(chat_id, msg["message_id"])


def handle_callback(cb):
    uid = cb["from"]["id"]
    d = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
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
    tg("deleteMessage", chat_id=chat_id, message_id=msg["message_id"])
    deliver(chat_id, uid, urls[0].rstrip(").,"), d[2:])


# ---------- Inline mode ----------
def handle_inline_query(q):
    urls = URL_RE.findall(q.get("query", ""))
    if not urls or not allowed_site(urls[0]):
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
    url = urls[0].rstrip(").,")
    if not is_joined(uid):
        tg("editMessageText", inline_message_id=imid, text="Please join " + CHANNEL_URL + " to use this bot.")
        return
    if over_limit(uid):
        tg("editMessageText", inline_message_id=imid, text=f"Limit reached ({RATE_LIMIT}/hour).")
        return
    tmp = tempfile.mkdtemp()
    try:
        files, caption, err = prepare(url, "720", tmp)
        if err:
            tg("editMessageText", inline_message_id=imid, text=err)
            return
        r = send_file(STORAGE_CHAT, files[0], caption)
        if not r:
            tg("editMessageText", inline_message_id=imid, text="Upload failed.")
            return
        count_download()
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
