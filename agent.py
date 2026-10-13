import os
import re
import json
import hashlib
import time
import uuid
import base64
import shutil
import threading
import subprocess
import html as html_lib
import asyncio
import requests
import edge_tts
from io import BytesIO
from flask import Flask, request, send_file, redirect
import google.generativeai as genai
import psycopg2
from PIL import Image, ImageFont
import imageio_ffmpeg

app = Flask(__name__)

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

customer_conversations = {}

def get_db_connection():
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            customer_number TEXT,
            message_from TEXT,
            message_text TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS business (
            id SERIAL PRIMARY KEY,
            name TEXT,
            timing TEXT,
            services TEXT,
            address TEXT
        )
    """)
    cur.execute("SELECT COUNT(*) FROM business")
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute(
            "INSERT INTO business (name, timing, services, address) VALUES (%s, %s, %s, %s)",
            ("Sharma Saloon", "9 AM - 8 PM (Monday closed)", "Haircut - Rs 150, Beard - Rs 80, Hair Color - Rs 500", "Main Market, Sector 12")
        )
    conn.commit()
    cur.close()
    conn.close()

def get_business_info():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, timing, services, address FROM business ORDER BY id LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def save_message(customer_number, message_from, message_text):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (customer_number, message_from, message_text) VALUES (%s, %s, %s)",
        (customer_number, message_from, message_text)
    )
    conn.commit()
    cur.close()
    conn.close()

def build_system_prompt():
    biz = get_business_info()
    name, timing, services, address = biz
    return f"""Tum ek friendly sales assistant ho jo customers ke sawaalon ka jawab deta hai.
Business ki details neeche di hain, isi ke aadhar par jawab do:

Business Name: {name}
Timing: {timing}
Services: {services}
Address: {address}

Rules:
- Hamesha polite aur helpful raho
- Agar customer price poochta hai, clearly bata do
- Booking ke liye poocho ki kaunsa time convenient hai
- Agar sawaal business se related nahi hai, politely bolo ki sirf business info mein help kar sakte ho
"""

@app.route("/")
def home():
    return "Agent chalu hai!"

@app.route("/chat")
def chat():
    user_message = request.args.get("message", "")
    if not user_message:
        return {"error": "message parameter chahiye"}
    model = genai.GenerativeModel(model_name="gemini-3.6-flash", system_instruction=build_system_prompt())
    chat_session = model.start_chat(history=[])
    response = chat_session.send_message(user_message)
    return {"reply": response.text}

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_message = request.form.get("Body", "")
    sender_number = request.form.get("From", "")

    if sender_number not in customer_conversations:
        model = genai.GenerativeModel(model_name="gemini-3.6-flash", system_instruction=build_system_prompt())
        customer_conversations[sender_number] = model.start_chat(history=[])

    chat_session = customer_conversations[sender_number]
    response = chat_session.send_message(incoming_message)
    reply_text = response.text

    save_message(sender_number, "customer", incoming_message)
    save_message(sender_number, "agent", reply_text)

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{reply_text}</Message>
</Response>"""
    return twiml_response, 200, {"Content-Type": "text/xml"}

# Dashboard Home - cards/buttons yaha honge
@app.route("/dashboard")
def dashboard():
    html = """
    <html>
    <head>
        <style>
            body { font-family: Arial; background: #f0f2f5; padding: 20px; }
            h1 { color: #333; }
            .card {
                display: block;
                background: white;
                padding: 20px;
                margin: 15px 0;
                border-radius: 10px;
                text-decoration: none;
                color: #333;
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                font-size: 18px;
                font-weight: bold;
            }
            .card:active { background: #e8e8e8; }
        </style>
    </head>
    <body>
        <h1>Sales Agent Dashboard</h1>
        <a class="card" href="/dashboard/chats">💬 Conversations</a>
        <a class="card" href="/dashboard/business">⚙️ Business Settings</a>
        <a class="card" href="/dashboard/youtube">📚 Book Script Generator</a>
    </body>
    </html>
    """
    return html

# Conversations page
@app.route("/dashboard/chats")
def dashboard_chats():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT customer_number, message_from, message_text, timestamp FROM messages ORDER BY timestamp DESC LIMIT 100")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <html>
    <head>
        <style>
            body { font-family: Arial; background: #f0f2f5; padding: 20px; }
            a.back { color: #007bff; text-decoration: none; font-size: 16px; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; background: white; }
            th, td { border: 1px solid #ddd; padding: 10px; text-align: left; font-size: 14px; }
            th { background: #333; color: white; }
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard">&larr; Back</a>
        <h2>Conversations</h2>
        <table>
        <tr><th>Customer</th><th>From</th><th>Message</th><th>Time</th></tr>
    """
    for row in rows:
        html += f"<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td></tr>"
    html += "</table></body></html>"
    return html

# Business Settings page - dekho aur edit karo
@app.route("/dashboard/business", methods=["GET", "POST"])
def dashboard_business():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name")
        timing = request.form.get("timing")
        services = request.form.get("services")
        address = request.form.get("address")
        cur.execute("UPDATE business SET name=%s, timing=%s, services=%s, address=%s WHERE id=(SELECT id FROM business ORDER BY id LIMIT 1)",
                    (name, timing, services, address))
        conn.commit()

    cur.execute("SELECT name, timing, services, address FROM business ORDER BY id LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    name, timing, services, address = row

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial; background: #f0f2f5; padding: 20px; }}
            a.back {{ color: #007bff; text-decoration: none; font-size: 16px; }}
            label {{ font-weight: bold; display: block; margin-top: 15px; }}
            input, textarea {{ width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }}
            button {{ margin-top: 20px; padding: 12px; background: #28a745; color: white; border: none; border-radius: 5px; width: 100%; font-size: 16px; }}
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard">&larr; Back</a>
        <h2>Business Settings</h2>
        <form method="POST">
            <label>Business Name</label>
            <input type="text" name="name" value="{name}">

            <label>Timing</label>
            <input type="text" name="timing" value="{timing}">

            <label>Services (comma separated with price)</label>
            <textarea name="services" rows="4">{services}</textarea>

            <label>Address</label>
            <input type="text" name="address" value="{address}">

            <button type="submit">Save Changes</button>
        </form>
    </body>
    </html>
    """
    return html

# ---------------------------------------------------------------
# VOICE
# ---------------------------------------------------------------

def extract_voiceover_text(script_text):
    match = re.search(r"HOOK:(.*?)(DESCRIPTION:|TAGS:|$)", script_text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        text = script_text
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"#", "", text)
    text = re.sub(r"---", "", text)
    text = re.sub(r"SCRIPT:", "", text)
    text = re.sub(r"Point \d+:", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()
    return text

# Text ke liye models ki list: pehle main model (jaisa pehle tha), fail ho toh halka model
TEXT_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

def gemini_text(prompt):
    last_err = None
    for name in TEXT_MODELS:
        try:
            resp = genai.GenerativeModel(model_name=name).generate_content(prompt)
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:
            last_err = e
            print("Gemini text fail:", name, e)
    raise RuntimeError("Gemini se jawab nahi mila: " + str(last_err)[:200])

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# Roman Hinglish ko Devanagari Hindi mein badalta hai (voice natural lagti hai)
# (text, ok) return karta hai. ok=False matlab conversion nahi hua, Roman text hi wapas aaya.
def to_devanagari(text):
    prompt = (
        "Neeche diye Hinglish text ko Devanagari (Hindi) script mein likho, "
        "bilkul natural bolchal ki Hindi mein. English words ko bhi Devanagari mein "
        "likho jaise Hindi mein bole jaate hain. Sirf converted text do, koi explanation "
        "ya markdown mat do.\n\n" + text
    )
    try:
        out = gemini_text(prompt)
    except Exception as e:
        print("Devanagari conversion failed:", e)
        return text, False
    out = re.sub(r"```[a-zA-Z]*", "", out).strip()
    lines = out.splitlines()
    has_dev = [bool(DEVANAGARI_RE.search(l)) for l in lines]
    while lines and not has_dev[0] and any(has_dev):
        lines.pop(0)
        has_dev.pop(0)
    out = "\n".join(lines).strip()
    if len(DEVANAGARI_RE.findall(out)) < 10:
        print("Devanagari conversion: output Hindi script mein nahi aaya")
        return text, False
    return out, True

# Gemini TTS se natural male Hindi voice (WAV bytes return karta hai)
def gemini_tts_wav(text, timeout=90):
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    headers = {
        "x-goog-api-key": os.environ.get("GEMINI_API_KEY"),
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemini-3.8-flash-tts",
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{
                    "type": "speech_metadata",
                    "style": "natural Hindi book narrator, confident and engaging, slightly faster pace"
                }]
            }]
        }],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "Charon"}]}
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    audio_b64 = None
    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for c in step.get("content", []):
                if c.get("type") == "audio":
                    audio_b64 = c.get("data")
    if not audio_b64:
        raise ValueError("Audio response mein nahi mila")
    return base64.b64decode(audio_b64)

# Backup: edge-tts (agar Gemini TTS fail ho jaye) - mp3 bytes
def edge_tts_mp3_bytes(text):
    audio_buffer = BytesIO()

    async def generate_audio():
        communicate = edge_tts.Communicate(
            text,
            voice="hi-IN-MadhurNeural",
            rate="+10%"
        )
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

    asyncio.run(generate_audio())
    return audio_buffer.getvalue()

def describe_error(e):
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            return f"HTTP {resp.status_code}: {resp.text[:150]}".replace("\n", " ")
        except Exception:
            pass
    return str(e)[:150].replace("\n", " ")

voice_cache = {}
VOICE_CACHE_MAX = 5

# Voice banata hai: (audio_bytes, "wav" ya "mp3", hindi_text, note) return karta hai
# Voice wahi hai jo pehle thi (Gemini TTS, Charon). Fail hone par ek baar dobara try, phir backup voice.
# note mein likha aata hai agar kuch fail hua (status page pe dikhta hai).
def make_voice(clean_text):
    key = hashlib.sha1(clean_text.encode("utf-8")).hexdigest()
    if key in voice_cache:
        return voice_cache[key]

    notes = []
    hindi_text, conv_ok = to_devanagari(clean_text)
    if not conv_ok:
        notes.append("Hindi conversion fail hua, isliye voice Roman text se bani (quality kam ho sakti hai).")

    audio, ext, last_err = None, None, ""
    for attempt in range(2):
        try:
            audio, ext = gemini_tts_wav(hindi_text), "wav"
            break
        except Exception as e:
            last_err = describe_error(e)
            print("Gemini TTS fail hua:", last_err)
            if attempt == 0:
                time.sleep(6)
    if audio is None:
        notes.append(f"Gemini voice fail hua ({last_err}), isliye backup voice use hui.")
        audio, ext = edge_tts_mp3_bytes(hindi_text), "mp3"

    result = (audio, ext, hindi_text, " ".join(notes))
    if ext == "wav" and conv_ok:
        voice_cache[key] = result
        while len(voice_cache) > VOICE_CACHE_MAX:
            voice_cache.pop(next(iter(voice_cache)))
    return result

# ---------------------------------------------------------------
# VIDEO (stock photos + stock video + zoom + transitions + Hindi captions)
# ---------------------------------------------------------------

VIDEO_DIR = "/tmp/videos"
FONT_DIR = "/tmp/fonts"
FONT_FILE = os.path.join(FONT_DIR, "NotoSansDevanagari-Bold.ttf")
FONT_URLS = [
    "https://github.com/notofonts/devanagari/raw/main/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf",
    "https://github.com/openmaptiles/fonts/raw/master/noto-sans/NotoSansDevanagari-Bold.ttf",
]
W, H = 720, 1280          # Short (vertical) video size
FPS = 24
TRANS_DUR = 0.6           # transition ki length (second)
SCENE_SECONDS = 4         # ek photo/clip lagbhag itni der dikhega
MAX_VIDEO_SCENES = 2      # kitne scenes stock VIDEO clip honge (baaki photos). Har clip ~35 MB memory leti hai,
                          # free server pe 2 theek hai. "out of memory" aaye toh 1 ya 0 kar do, paid server pe 6 tak badha sakte ho.
TRANSITIONS = ["fade", "slideleft", "zoomin", "circleopen", "wipeleft", "dissolve", "slideup", "smoothright"]
FALLBACK_QUERIES = ["success business", "money coins", "city skyline", "person thinking",
                    "books library", "office desk", "growth chart", "sunrise mountain"]
BG_COLORS = [(18, 24, 56), (40, 18, 56), (14, 52, 60), (56, 28, 18), (22, 44, 28)]
video_jobs = {}

def ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()

def audio_duration(path):
    r = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise ValueError("Audio ki length nahi mili")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

def split_chunks(text, max_chars):
    sentences = re.split(r"(?<=[.!?\u0964])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    final = []
    for c in chunks:
        while len(c) > max_chars * 1.4:
            cut = c.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            final.append(c[:cut].strip())
            c = c[cut:].strip()
        if c:
            final.append(c)
    return final

# ---- Hindi font (ek baar download hota hai) ----
def ensure_font():
    os.makedirs(FONT_DIR, exist_ok=True)
    if not (os.path.exists(FONT_FILE) and os.path.getsize(FONT_FILE) > 50000):
        for url in FONT_URLS:
            try:
                r = requests.get(url, timeout=40)
                if r.status_code == 200 and len(r.content) > 50000:
                    with open(FONT_FILE, "wb") as f:
                        f.write(r.content)
                    break
            except Exception as e:
                print("Font download fail:", url, e)
    if os.path.exists(FONT_FILE) and os.path.getsize(FONT_FILE) > 50000:
        try:
            return ImageFont.truetype(FONT_FILE, 20).getname()[0]
        except Exception:
            return "Noto Sans Devanagari"
    return None

# ---- Captions (ASS subtitle file, Hindi) ----
def ass_time(t):
    cs = int(round(t * 100))
    h = cs // 360000
    m = (cs % 360000) // 6000
    s = (cs % 6000) / 100.0
    return f"{h}:{m:02d}:{s:05.2f}"

def build_ass(hindi_text, total_dur, font_family, path):
    chunks = split_chunks(hindi_text, 44)
    total_len = sum(len(c) for c in chunks) or 1
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font_family},{int(46 * W / 720)},&H00FFFFFF,&H00FFFFFF,&H80000000,&H00000000,1,0,0,0,100,100,0,0,3,{int(14 * W / 720)},0,2,{int(50 * W / 720)},{int(50 * W / 720)},{int(150 * W / 720)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = 0.0
    for c in chunks:
        d = total_dur * len(c) / total_len
        txt = c.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Cap,,0,0,0,,{{\\fad(120,80)}}{txt}")
        t += d
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# ---- Scenes + stock media ----
def plan_scenes(roman_text, total_dur):
    n = max(3, min(12, int(round(total_dur / SCENE_SECONDS))))
    total_chars = len(roman_text) or 1
    target = total_chars / n

    # sentences, aur lambe sentences ko comma pe todo, taaki har scene ~5 second ka bane
    units = []
    for sent in re.split(r"(?<=[.!?])\s+", roman_text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > target * 1.3:
            parts = [p.strip() for p in re.split(r"(?<=[,;:])\s+", sent) if p.strip()]
            units.extend(parts if parts else [sent])
        else:
            units.append(sent)
    if not units:
        units = [roman_text]

    groups, cur, cur_len = [], [], 0
    for u in units:
        cur.append(u)
        cur_len += len(u)
        if cur_len >= target * 0.9 and len(groups) < n - 1:
            groups.append(" ".join(cur))
            cur, cur_len = [], 0
    if cur:
        groups.append(" ".join(cur))

    lens = [max(len(g), 1) for g in groups]
    durs = [max(2.5, total_dur * l / sum(lens)) for l in lens]
    scale = total_dur / sum(durs)
    durs = [d * scale for d in durs]
    return groups, durs

def get_scene_queries(scene_texts, book_title):
    n = len(scene_texts)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(scene_texts))
    prompt = (
        f"Book: {book_title}\n"
        f"Neeche {n} scenes hain (YouTube book-summary video ke). Har scene ke liye stock photo/video "
        f"search karne ke liye ek English query do (2-4 words, koi concrete visual cheez, jaise "
        f"'money coins desk', 'city skyline night', 'person thinking window'). "
        f"Query mein kisi insaan ya brand ka naam mat rakho. "
        f"Output sirf ek JSON array of {n} strings ho, aur kuch nahi.\n\n{numbered}"
    )
    queries, note = [], ""
    try:
        txt = gemini_text(prompt)
        m = re.search(r"\[.*\]", txt, re.DOTALL)
        queries = [str(q).strip() for q in json.loads(m.group(0)) if str(q).strip()]
    except Exception as e:
        print("Scene queries fail:", e)
        note = "Scene ke hisaab se images dhoondhne wala step fail hua, isliye general stock images use hui."
    while len(queries) < n:
        queries.append(FALLBACK_QUERIES[len(queries) % len(FALLBACK_QUERIES)])
    return queries[:n], note

def pexels_get(url, params):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise ValueError("PEXELS_API_KEY set nahi hai")
    r = requests.get(url, headers={"Authorization": key}, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def download_file(url, dest, max_mb=25):
    with requests.get(url, stream=True, timeout=40) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    raise ValueError("Stock file bahut badi hai")
                f.write(chunk)

def fetch_stock_photo(query, used, dest):
    data = pexels_get("https://api.pexels.com/v1/search",
                      {"query": query, "orientation": "portrait", "per_page": 10})
    for p in data.get("photos", []):
        if ("p", p["id"]) in used:
            continue
        url = p["src"].get("portrait") or p["src"].get("large")
        download_file(url, dest, max_mb=8)
        used.add(("p", p["id"]))
        return True
    return False

def fetch_stock_video(query, used, dest):
    data = pexels_get("https://api.pexels.com/videos/search",
                      {"query": query, "orientation": "portrait", "per_page": 10})
    for v in data.get("videos", []):
        if ("v", v["id"]) in used:
            continue
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and (f.get("width") or 0) >= 540]
        if not files:
            continue
        files.sort(key=lambda f: f.get("width") or 9999)
        download_file(files[0]["link"], dest, max_mb=25)
        used.add(("v", v["id"]))
        return True
    return False

def prepare_photo(src, dest):
    img = Image.open(src).convert("RGB")
    tw, th = 810, 1440
    scale = max(tw / img.width, th / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left = (img.width - tw) // 2
    top = (img.height - th) // 2
    img.crop((left, top, left + tw, top + th)).save(dest, "JPEG", quality=88)

def make_fallback_bg(dest, color):
    tw, th = 810, 1440
    col = Image.new("RGB", (1, th))
    px = col.load()
    bottom = tuple(int(c * 0.45) for c in color)
    for y in range(th):
        t = y / (th - 1)
        px[0, y] = tuple(int(color[i] * (1 - t) + bottom[i] * t) for i in range(3))
    col.resize((tw, th)).save(dest, "JPEG", quality=90)

def gather_media(queries, work_dir, warnings):
    # har scene ke liye ("photo"/"video", file_path) return karta hai
    used, media, video_count = set(), [], 0
    n_scenes = len(queries)
    # video clips poore video mein barabar doori pe lagenge
    video_slots = set(int(round((k + 1) * n_scenes / (MAX_VIDEO_SCENES + 1))) for k in range(MAX_VIDEO_SCENES))
    stock_ok = bool(os.environ.get("PEXELS_API_KEY"))
    if not stock_ok:
        warnings.append("PEXELS_API_KEY set nahi hai, isliye stock photo/video ki jagah simple background use hua.")
    missing = 0
    for i, q in enumerate(queries):
        got = None
        if stock_ok:
            want_video = (i in video_slots) and video_count < MAX_VIDEO_SCENES
            order = ["video", "photo"] if want_video else ["photo", "video"]
            for kind in order:
                if kind == "video" and video_count >= MAX_VIDEO_SCENES:
                    continue
                try:
                    if kind == "photo":
                        raw = os.path.join(work_dir, f"raw_{i}.jpg")
                        if fetch_stock_photo(q, used, raw):
                            prep = os.path.join(work_dir, f"photo_{i}.jpg")
                            prepare_photo(raw, prep)
                            got = ("photo", prep)
                    else:
                        vp = os.path.join(work_dir, f"clip_{i}.mp4")
                        if fetch_stock_video(q, used, vp):
                            got = ("video", vp)
                            video_count += 1
                except Exception as e:
                    print("Stock fetch fail:", q, e)
                if got:
                    break
        if not got:
            bg = os.path.join(work_dir, f"bg_{i}.jpg")
            make_fallback_bg(bg, BG_COLORS[i % len(BG_COLORS)])
            got = ("photo", bg)
            if stock_ok:
                missing += 1
        media.append(got)
    if missing:
        warnings.append(f"{missing} scene ke liye stock media nahi mila, wahan simple background laga.")
    return media

# ---- Background music ----
MUSIC_ON = True           # background music chahiye toh True, nahi chahiye toh False
MUSIC_GAIN = 2.5          # music ki awaaz (bada number = tez music)
MUSIC_DIR = "/tmp/music"
REPO_MUSIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music.mp3")

def ambient_expr():
    # Am - F - C - G chords (har chord 4 second, 16 second ka loop) + halke pentatonic plucks
    chords = [
        [110.00, 164.81, 220.00, 261.63],
        [87.31, 130.81, 174.61, 220.00],
        [130.81, 196.00, 261.63, 329.63],
        [98.00, 146.83, 196.00, 246.94],
    ]
    parts = []
    for c, notes in enumerate(chords):
        p = f"mod(t-{4 * c}+0.5+16,16)"
        win = f"if(lt({p},5),pow(sin(PI*{p}/5),2),0)"
        tone = "+".join(f"(sin(2*PI*{f}*t)+0.25*sin(4*PI*{f}*t))" for f in notes)
        parts.append(f"{win}*({tone})")
    pad = "(" + "+".join(parts) + ")*0.045"
    semis = [0, 3, 5, 7, 10]
    step = "floor(t*2)"
    idx = f"mod({step}*3+floor({step}/8),5)"
    sel = "0"
    for i in range(len(semis) - 1, -1, -1):
        sel = f"if(eq({idx},{i}),{semis[i]},{sel})"
    freq = f"(440*pow(2,({sel})/12))"
    env = "exp(-5*mod(t,0.5))*min(1,mod(t,0.5)*80)"
    gate = f"if(eq(mod({step},3),2),0,1)"
    pluck = f"{gate}*{env}*sin(2*PI*{freq}*t)*0.03"
    return f"{pad}+{pluck}"

def generate_ambient_loop(dest):
    # 32 second banao, reverb lagao, beech ka 16 second lo => seamless loop
    tmp = dest + ".tmp.wav"
    cmd = [ffmpeg_exe(), "-y", "-f", "lavfi", "-i", f"aevalsrc='{ambient_expr()}':s=22050:d=32",
           "-af", "lowpass=f=2200,highpass=f=70,aecho=0.8:0.6:70|140|210:0.35|0.25|0.15,"
                  "atrim=start=16:end=32,asetpts=PTS-STARTPTS",
           "-ac", "1", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("music generate fail: " + r.stderr[-300:])
    os.replace(tmp, dest)

# (music_file_path, label) return karta hai. Pehle repo ka music.mp3, phir MUSIC_URL, phir auto-generated ambient.
def ensure_music(warnings):
    if os.path.exists(REPO_MUSIC):
        return REPO_MUSIC, "apna track (music.mp3)"
    os.makedirs(MUSIC_DIR, exist_ok=True)
    url = os.environ.get("MUSIC_URL", "").strip()
    if url:
        dest = os.path.join(MUSIC_DIR, "custom_music")
        try:
            if not (os.path.exists(dest) and os.path.getsize(dest) > 20000):
                download_file(url, dest, max_mb=20)
            return dest, "MUSIC_URL wala track"
        except Exception as e:
            print("MUSIC_URL download fail:", e)
            warnings.append("MUSIC_URL wala track download nahi hua, isliye auto ambient music lagi.")
    dest = os.path.join(MUSIC_DIR, "ambient_loop.wav")
    try:
        if not (os.path.exists(dest) and os.path.getsize(dest) > 20000):
            generate_ambient_loop(dest)
        return dest, "ambient music (auto-generated)"
    except Exception as e:
        print("Music fail:", e)
        warnings.append("Background music nahi ban payi, video bina music ke bani.")
        return None, "music nahi laga"

# ---- Final render ----
def render_video(media, durs, ass_path, font_dir, audio_path, out_path, total_dur, music_path=None):
    n = len(media)
    T = TRANS_DUR
    cmd = [ffmpeg_exe(), "-y"]
    filters = []
    zoom_in = True
    for i, (kind, path) in enumerate(media):
        L = durs[i] + (T if i < n - 1 else 0)
        frames = max(2, int(round(L * FPS)))
        if kind == "photo":
            cmd += ["-i", path]
            if zoom_in:
                z = f"1+0.12*on/{frames}"
            else:
                z = f"1.12-0.12*on/{frames}"
            zoom_in = not zoom_in
            filters.append(
                f"[{i}:v]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p,settb=1/{FPS}[v{i}]"
            )
        else:
            cmd += ["-stream_loop", "-1", "-t", f"{L:.3f}", "-i", path]
            filters.append(
                f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"fps={FPS},setsar=1,format=yuv420p,settb=1/{FPS}[v{i}]"
            )
    audio_idx = n
    cmd += ["-i", audio_path]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]

    cur = "v0"
    elapsed = 0.0
    for k in range(1, n):
        elapsed += durs[k - 1]
        tr = TRANSITIONS[(k - 1) % len(TRANSITIONS)]
        out = f"x{k}"
        filters.append(f"[{cur}][v{k}]xfade=transition={tr}:duration={T}:offset={elapsed:.3f}[{out}]")
        cur = out

    if ass_path:
        filters.append(f"[{cur}]subtitles=filename='{ass_path}':fontsdir='{font_dir}'[vout]")
    else:
        filters.append(f"[{cur}]format=yuv420p[vout]")

    if music_path:
        fade_start = max(0.0, total_dur - 2.0)
        filters.append(f"[{audio_idx}:a]aresample=44100,aformat=channel_layouts=mono,asplit=2[vo][sc]")
        filters.append(
            f"[{audio_idx + 1}:a]aresample=44100,aformat=channel_layouts=mono,volume={MUSIC_GAIN},"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start:.2f}:d=2[mu]"
        )
        # jab voice bol rahi ho tab music apne aap dheemi ho jaye
        filters.append("[mu][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
        filters.append(
            "[vo][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95:level=0,aformat=sample_rates=44100:channel_layouts=mono[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{audio_idx}:a"

    cmd += [
        "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "29", "-threads", "1", "-x264-params", "rc-lookahead=0:ref=1:bframes=0:sync-lookahead=0", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg fail: " + r.stderr[-500:])

def cleanup_old_videos():
    try:
        now = time.time()
        for name in os.listdir(VIDEO_DIR):
            p = os.path.join(VIDEO_DIR, name)
            if os.path.isfile(p) and now - os.path.getmtime(p) > 3600:
                os.remove(p)
    except Exception:
        pass

def run_video_job(job_id, script_text, title):
    job = video_jobs[job_id]
    work_dir = os.path.join(VIDEO_DIR, job_id + "_work")
    warnings = []
    try:
        roman_text = extract_voiceover_text(script_text)
        if not roman_text:
            raise ValueError("Script se text nahi mila")
        os.makedirs(work_dir, exist_ok=True)

        job["msg"] = "Step 1/4: Voiceover ban raha hai (Hindi + awaaz)..."
        audio_bytes, ext, hindi_text, voice_note = make_voice(roman_text)
        if voice_note:
            warnings.append(voice_note)
        job["voice"] = "Voice: Gemini (Charon) ✅" if ext == "wav" else "Voice: BACKUP voice ⚠️ (Gemini voice nahi chali)"
        audio_path = os.path.join(work_dir, "voice." + ext)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        total_dur = audio_duration(audio_path)

        job["msg"] = "Step 2/4: Scenes plan ho rahe hain aur stock photo/video dhoondhe ja rahe hain..."
        groups, durs = plan_scenes(roman_text, total_dur)
        queries, q_note = get_scene_queries(groups, title)
        if q_note:
            warnings.append(q_note)
        media = gather_media(queries, work_dir, warnings)

        job["msg"] = "Step 3/4: Hindi captions aur background music taiyar ho rahe hain..."
        family = ensure_font()
        ass_path = None
        if family:
            ass_path = os.path.join(work_dir, "captions.ass")
            build_ass(hindi_text, total_dur, family, ass_path)
        else:
            warnings.append("Hindi font download nahi hua, isliye captions nahi lage.")

        music_path, music_label = (None, "music band (MUSIC_ON = False)")
        if MUSIC_ON:
            music_path, music_label = ensure_music(warnings)
        job["music"] = "Music: " + music_label

        job["msg"] = "Step 4/4: Video render ho raha hai (zoom + transitions + captions + music)..."
        out_path = os.path.join(VIDEO_DIR, job_id + ".mp4")
        render_video(media, durs, ass_path, FONT_DIR, audio_path, out_path, total_dur, music_path)

        job["file"] = out_path
        job["status"] = "done"
        job["msg"] = "Video ready!"
        job["note"] = " ".join(warnings)
    except Exception as e:
        print("Video job fail:", e)
        job["status"] = "error"
        job["msg"] = str(e)[:500]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

# ---------------------------------------------------------------
# LONG VIDEO (book card + waveform + Hindi captions) - 16:9
# Short wale code ko yeh bilkul nahi chhedta. Voice wahi hai (Gemini Charon, backup edge-tts).
# Video chhote chhote hisson (parts) mein banti hai, phir jud jati hai, taaki free server ki memory na bhare.
# ---------------------------------------------------------------

LW, LH = 854, 480          # Long video ka size (480p). Server tez ho toh 1280, 720 kar sakte ho
LFPS = 10                  # frames per second (kam rakha hai taaki render tez ho)
LONG_PART_CHARS = 700      # ek voice part mein kam se kam itne Hindi characters
LONG_MAX_PART_CHARS = 4000 # ek voice request mein zyada se zyada itne characters (~4-5 minute ki awaaz)
LONG_VOICE_ENGINE = "gemini"  # "gemini" = Gemini Charon (roz sirf 10 requests free, awaaz natural). "edge" = Microsoft Edge free voice (limit nahi, par robotic)
EDGE_VOICE = "hi-IN-MadhurNeural"   # ya "hi-IN-SwaraNeural" (female)
EDGE_RATE = "-4%"          # thoda dheema = zyada natural (pehle backup +10% tha, isliye robotic lagta tha)
EDGE_PITCH = "-2Hz"
TTS_DAILY_LIMIT = 10       # Gemini TTS ki free limit: roz ki 10 requests (Render log se pata chala). Paid ho toh badha dena
LONG_CONVERT_CHARS = 4500  # Hinglish se Hindi conversion ek baar mein itne characters
LONG_BG_QUERY = "dark library bookshelf"
LONG_ALLOW_BACKUP = False   # False = Gemini voice fail hui toh video wahin ruk jati hai aur asli error dikhati hai (robotic video nahi banti). True = backup voice se video poori banao
LONG_TTS_GAP = 3            # do voice requests ke beech itne second ruko (limit se bachne ke liye)
GOLD = (212, 175, 55)

def long_font(size):
    from PIL import ImageFont as _IF
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
        if os.path.exists(p):
            try:
                return _IF.truetype(p, size)
            except Exception:
                pass
    try:
        return _IF.load_default(size=size)
    except Exception:
        return _IF.load_default()

def wrap_text(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def build_long_base(path, book_title, work_dir, warnings):
    from PIL import ImageDraw, ImageFilter, ImageEnhance
    bg = None
    if os.environ.get("PEXELS_API_KEY"):
        try:
            data = pexels_get("https://api.pexels.com/v1/search",
                              {"query": LONG_BG_QUERY, "orientation": "landscape", "per_page": 5})
            photos = data.get("photos", [])
            if photos:
                raw = os.path.join(work_dir, "bg_raw.jpg")
                download_file(photos[0]["src"].get("large2x") or photos[0]["src"]["large"], raw, max_mb=8)
                bg = Image.open(raw).convert("RGB")
        except Exception as e:
            print("Long bg fetch fail:", e)
    if bg is None:
        warnings.append("Background photo nahi mili, isliye simple dark background laga.")
        bg = Image.new("RGB", (LW, LH), (30, 20, 14))
    scale = max(LW / bg.width, LH / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
    left, top = (bg.width - LW) // 2, (bg.height - LH) // 2
    bg = bg.crop((left, top, left + LW, top + LH)).filter(ImageFilter.GaussianBlur(2))
    bg = ImageEnhance.Brightness(bg).enhance(0.38)

    cw, ch = 230, 330
    cx, cy = 36, (LH - ch) // 2
    shadow = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((cx + 6, cy + 8, cx + cw + 6, cy + ch + 8), 14, fill=(0, 0, 0, 170))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(8)))
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle((cx, cy, cx + cw, cy + ch), 14, fill=(26, 18, 12), outline=GOLD, width=4)
    d.rounded_rectangle((cx + 10, cy + 10, cx + cw - 10, cy + ch - 10), 8, outline=(120, 98, 30), width=1)

    f_small, f_big = long_font(15), long_font(30)
    brand = (CHANNEL_NAME or "BOOK SUMMARY").upper()
    bw = d.textlength(brand, font=f_small)
    d.text((cx + (cw - bw) / 2, cy + 26), brand, font=f_small, fill=GOLD)
    d.line((cx + 40, cy + 54, cx + cw - 40, cy + 54), fill=GOLD, width=2)
    lines = wrap_text(d, (book_title or "Book Summary").upper(), f_big, cw - 40)[:5]
    lh = 38
    y = cy + 70 + max(0, (5 - len(lines)) * lh // 2)
    for ln in lines:
        lw_ = d.textlength(ln, font=f_big)
        d.text((cx + (cw - lw_) / 2, y), ln, font=f_big, fill=(255, 255, 255))
        y += lh
    d.line((cx + 40, cy + ch - 62, cx + cw - 40, cy + ch - 62), fill=GOLD, width=2)
    sub = "BOOK SUMMARY IN HINDI"
    sw = d.textlength(sub, font=f_small)
    d.text((cx + (cw - sw) / 2, cy + ch - 46), sub, font=f_small, fill=GOLD)
    bg.convert("RGB").save(path, "PNG")

LONG_CAP_X = 300           # caption/waveform area left edge
LONG_WAVE_Y = 335

def build_long_ass(hindi_text, total_dur, font_family, path):
    chunks = split_chunks(hindi_text, 30)
    total_len = sum(len(c) for c in chunks) or 1
    mr = 36
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {LW}", f"PlayResY: {LH}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font_family},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,1,8,{LONG_CAP_X},{mr},125,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = 0.0
    for c in chunks:
        dur = total_dur * len(c) / total_len
        txt = c.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + dur)},Cap,,0,0,0,,{{\\fad(100,60)}}{txt}")
        t += dur
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def render_long_part(base_png, audio_path, ass_path, font_dir, out_path, dur):
    wave_w, wave_h = LW - LONG_CAP_X - 36, 100
    fc = (
        f"[0:v]format=yuv420p[bg];"
        f"[1:a]aresample=44100,aformat=channel_layouts=mono,asplit=2[a1][a2];"
        f"[a2]showfreqs=s={wave_w}x{wave_h}:mode=bar:fscale=log:ascale=sqrt:win_size=1024:rate={LFPS}:averaging=3:colors=white[wv];"
        f"[bg][wv]overlay={LONG_CAP_X}:{LONG_WAVE_Y}:format=auto[ov]"
    )
    if ass_path:
        fc += f";[ov]subtitles=filename='{ass_path}':fontsdir='{font_dir}'[vout]"
    else:
        fc += ";[ov]null[vout]"
    cmd = [ffmpeg_exe(), "-y", "-loop", "1", "-framerate", str(LFPS), "-i", base_png, "-i", audio_path,
           "-filter_complex_threads", "1", "-filter_complex", fc,
           "-map", "[vout]", "-map", "[a1]", "-t", f"{dur:.3f}",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-threads", "1",
           "-x264-params", "rc-lookahead=0:ref=1:bframes=0:sync-lookahead=0", "-pix_fmt", "yuv420p", "-r", str(LFPS),
           "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "1", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("part render fail: " + r.stderr[-400:])

def edge_natural_mp3(text, voice=None, rate=None, pitch=None):
    buf = BytesIO()

    async def go():
        comm = edge_tts.Communicate(text, voice=voice or EDGE_VOICE, rate=rate or EDGE_RATE, pitch=pitch or EDGE_PITCH)
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

    asyncio.run(go())
    data = buf.getvalue()
    if not data:
        raise RuntimeError("Edge se awaaz nahi aayi")
    return data

def long_voice_part(hindi_part):
    if LONG_VOICE_ENGINE == "edge":
        err = ""
        for attempt in range(3):
            try:
                return edge_natural_mp3(hindi_part), "mp3", ""
            except Exception as e:
                err = str(e)[:150]
                print("Long Edge voice fail:", err)
                time.sleep(3)
        return None, None, "Edge voice fail: " + err
    # Voice wahi: Gemini TTS (Charon). Rate-limit par ruk ruk ke 4 baar try. (audio_bytes, ext, error_text) return karta hai
    last_err = ""
    waits = [20, 45, 90]
    for attempt in range(4):
        try:
            return gemini_tts_wav(hindi_part, timeout=300), "wav", ""
        except Exception as e:
            last_err = describe_error(e)
            full = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    full = resp.text.lower()
                except Exception:
                    pass
            print("Long TTS fail (attempt %d): %s" % (attempt + 1, last_err))
            if "perday" in full.replace(" ", "") or "per day" in full or "daily" in full:
                last_err = "Gemini ki roz ki free limit khatam: " + last_err
                break
            if attempt < len(waits):
                wait = waits[attempt]
                m = re.search(r"retry in ([0-9.]+)s", full)
                if m:
                    wait = min(120, int(float(m.group(1))) + 3)
                time.sleep(wait)
    if LONG_ALLOW_BACKUP:
        return edge_natural_mp3(hindi_part), "mp3", last_err
    return None, None, last_err

def add_music_to_video(joined, music_path, out_path, total_dur):
    fade_start = max(0.0, total_dur - 3.0)
    fc = (
        "[0:a]aresample=44100,aformat=channel_layouts=mono,asplit=2[vo][sc];"
        f"[1:a]aresample=44100,aformat=channel_layouts=mono,volume={MUSIC_GAIN},"
        f"afade=t=in:st=0:d=2,afade=t=out:st={fade_start:.2f}:d=3[mu];"
        "[mu][sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
        "[vo][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:level=0,aformat=sample_rates=44100:channel_layouts=mono[aout]"
    )
    cmd = [ffmpeg_exe(), "-y", "-i", joined, "-stream_loop", "-1", "-i", music_path,
           "-filter_complex_threads", "1", "-filter_complex", fc,
           "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
           "-shortest", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("music mix fail: " + r.stderr[-300:])

def keep_awake_loop(stop_event):
    # Free Render server 15 minute bina visit ke so jata hai. Yeh har 4 minute mein apna hi page khol ke jagaye rakhta hai.
    url = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while not stop_event.wait(240):
        try:
            requests.get(url, timeout=15)
        except Exception:
            pass



# ---------------------------------------------------------------
# LONG PROJECTS: voice ke hisse (parts) database mein save hote hain.
# Gemini ki roz ki limit khatam ho jaye toh kal wahin se aage banao.
# Saare parts ban jayein tab ek final lambi video apne aap jud jati hai.
# ---------------------------------------------------------------
project_jobs = {}

def ensure_long_tables():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS long_projects (
            id TEXT PRIMARY KEY,
            title TEXT,
            script_text TEXT,
            hindi_parts TEXT,
            created TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS long_audio (
            project_id TEXT,
            idx INTEGER,
            ext TEXT,
            data BYTEA,
            dur REAL,
            PRIMARY KEY (project_id, idx)
        )
    """)
    cur.execute("ALTER TABLE long_projects ADD COLUMN IF NOT EXISTS outline TEXT")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS long_blobs (
            project_id TEXT,
            idx INTEGER,
            data BYTEA,
            PRIMARY KEY (project_id, idx)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def proj_create(title, script_text):
    ensure_long_tables()
    pid = uuid.uuid4().hex[:10]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO long_projects (id, title, script_text) VALUES (%s, %s, %s)", (pid, title, script_text))
    conn.commit()
    cur.close()
    conn.close()
    return pid

def proj_get(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT title, script_text, hindi_parts, outline FROM long_projects WHERE id = %s", (pid,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {"title": row[0] or "", "script": row[1] or "",
            "parts": json.loads(row[2]) if row[2] else None,
            "outline": json.loads(row[3]) if row[3] else None}

def proj_set_parts(pid, parts):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE long_projects SET hindi_parts = %s WHERE id = %s", (json.dumps(parts, ensure_ascii=False), pid))
    conn.commit()
    cur.close()
    conn.close()

def proj_set_outline(pid, outline, script_text=None):
    conn = get_db_connection()
    cur = conn.cursor()
    if script_text is None:
        cur.execute("UPDATE long_projects SET outline = %s WHERE id = %s", (json.dumps(outline, ensure_ascii=False), pid))
    else:
        cur.execute("UPDATE long_projects SET outline = %s, script_text = %s WHERE id = %s",
                    (json.dumps(outline, ensure_ascii=False), script_text, pid))
    conn.commit()
    cur.close()
    conn.close()

def proj_audio_info(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT idx, dur FROM long_audio WHERE project_id = %s", (pid,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {int(r[0]): float(r[1] or 0) for r in rows}

def proj_audio_save(pid, idx, ext, data, dur):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO long_audio (project_id, idx, ext, data, dur) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (project_id, idx) DO UPDATE SET ext = EXCLUDED.ext, data = EXCLUDED.data, dur = EXCLUDED.dur
    """, (pid, idx, ext, psycopg2.Binary(data), dur))
    conn.commit()
    cur.close()
    conn.close()

def proj_audio_load(pid, idx):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT ext, data FROM long_audio WHERE project_id = %s AND idx = %s", (pid, idx))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise RuntimeError(f"Voice part {idx} database mein nahi mili")
    return row[0], bytes(row[1])

def proj_blob_save(pid, idx, data):
    # idx 0 = background card image, idx 1.. = har part ki video (mp4)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO long_blobs (project_id, idx, data) VALUES (%s, %s, %s)
        ON CONFLICT (project_id, idx) DO UPDATE SET data = EXCLUDED.data
    """, (pid, idx, psycopg2.Binary(data)))
    conn.commit()
    cur.close()
    conn.close()

def proj_blob_info(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT idx FROM long_blobs WHERE project_id = %s", (pid,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {int(r[0]) for r in rows}

def proj_blob_load(pid, idx):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT data FROM long_blobs WHERE project_id = %s AND idx = %s", (pid, idx))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return bytes(row[0]) if row else None

def proj_list():
    ensure_long_tables()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.title, p.created, p.hindi_parts,
               (SELECT COUNT(*) FROM long_audio a WHERE a.project_id = p.id)
        FROM long_projects p ORDER BY p.created DESC LIMIT 15
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    out = []
    for r in rows:
        total = len(json.loads(r[3])) if r[3] else 0
        out.append({"id": r[0], "title": r[1] or "", "created": r[2], "total": total, "done": int(r[4])})
    return out

def compress_for_store(audio, ext, work_dir):
    # Voice ko chhota karke DB mein rakhte hain (5 minute ~ 3.6 MB). Returns (bytes, ext, duration)
    src = os.path.join(work_dir, "in." + ext)
    out = os.path.join(work_dir, "out.m4a")
    with open(src, "wb") as f:
        f.write(audio)
    r = subprocess.run([ffmpeg_exe(), "-y", "-i", src, "-ac", "1", "-ar", "44100", "-c:a", "aac", "-b:a", "96k", out],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(out):
        with open(out, "rb") as f:
            data = f.read()
        return data, "m4a", audio_duration(out)
    return audio, ext, audio_duration(src)

# ---------------- Part-wise Long: har 5 minute ka part alag likha jata hai, par sab ek dusre se jude rehte hain ----------------
PART_MINUTES = 5
WORDS_PER_MIN = 125

def seo_rules_text(hashtag_rule):
    return f"""SEO ke rules (YouTube search aur click ke liye):
- TITLE: 60 characters ke andar. Shuru mein main keyword (book ka naam + 'Book Summary in Hindi' ya 'Hindi Mein'), saath mein ek number ya curiosity. Jhooth wala clickbait nahi, jo video mein hai wahi promise karo.
- DESCRIPTION: pehli 2 lines (150 characters ke andar) mein book ka naam, 'Book Summary in Hindi' aur hook ho. Phir 3-4 lines mein video ka summary, ek line mein "{CHANNEL_NAME}" ke naam ke saath channel ki pehchaan. Phir ek line: Like, Share, Comment aur Subscribe karna mat bhoolna. End mein ek line Devanagari Hindi mein (jaise 'बुक समरी हिंदी में'). Author ka naam tabhi likho jab pakka pata ho.
- HASHTAGS: {hashtag_rule}
- TAGS: 12-15 search keywords, comma se alag, Hinglish aur English mix, 3-4 tags Devanagari Hindi mein, kul 400 characters ke andar.
- PINNED COMMENT: 1-2 line ka ek specific sawaal jo book ke twist se juda ho. Aakhir mein 'Agla raaz kaunsi book ka chahiye?' jaisi line jodo.
- THUMBNAIL TEXT: 3-5 words, badi akshar mein dikhne layak, curiosity wale. Koi emoji nahi."""

def generate_outline(book, minutes, n):
    prompt = f"""Tum ek top YouTube scriptwriter aur SEO expert ho jo books ko Hindi mein suspense ke saath explain karta hai.
Book: {book}
Ek {minutes} minute ki lambi video ka plan banao. Video {n} parts mein banegi, har part lagbhag {PART_MINUTES} minute ka.

Plan ke rules:
- Poori video ek hi kahani ki tarah chale. Har part pichhle part se juda ho aur agle part ki taraf le jaye.
- Shuru mein ek bada OPEN LOOP khule, aur end mein ek chonkane wala TWIST uska jawab de. Twist book ke asli idea se nikle.
- Har part ke end mein agle part ke liye cliffhanger ho.
- Book ke asli ideas hi use karo. Jhoothe statistics, nakli quotes ya banaye hue kisse nahi.

{seo_rules_text("3 se 5 hashtags, sabse zaroori pehle (YouTube pehle 3 title ke upar dikhata hai).")}

Output ka format bilkul yahi rakho (headings ke naam mat badalna, har cheez ek hi line mein):

TITLE: (title)
OPEN LOOP: (ek vakya jo shuru mein curiosity banayega)
TWIST: (ek-do vakya, end ka bada palat)
""" + "".join(f"PART {k}: (is part ka naam | is part mein kya samjhana hai | end ka cliffhanger)\n" for k in range(1, n + 1)) + """DESCRIPTION: (description)
HASHTAGS: (hashtags)
TAGS: (comma se alag keywords)
PINNED COMMENT: (comment)
THUMBNAIL TEXT: (text)
"""
    text = gemini_text(prompt).replace("**", "")
    def one(label):
        m = re.search(r"(?im)^\s*" + label + r"\s*:\s*(.+)$", text)
        return m.group(1).strip() if m else ""
    briefs = {}
    for m in re.finditer(r"(?im)^\s*PART\s*(\d+)\s*:\s*(.+)$", text):
        briefs[int(m.group(1))] = m.group(2).strip()
    briefs_list = [briefs.get(k) or "Book ka agla bada idea samjhao aur agle part ke liye curiosity chhodo." for k in range(1, n + 1)]
    seo_text = re.sub(r"(?im)^\s*(OPEN LOOP|TWIST|PART\s*\d+)\s*:.*$", "", text)
    seo_text = re.sub(r"\n{3,}", "\n\n", seo_text).strip()
    return {"open_loop": one("OPEN LOOP"), "twist": one("TWIST"), "briefs": briefs_list, "seo_text": seo_text}

def part_target_words(minutes, n, k):
    mins = PART_MINUTES if k < n else max(2, minutes - PART_MINUTES * (n - 1))
    return mins * WORDS_PER_MIN

def write_part_script(proj, k, parts):
    o = proj["outline"]
    n, minutes, book = o["n"], o["minutes"], o["book"]
    words = part_target_words(minutes, n, k)
    plan = "\n".join(f"PART {j}: {b}" for j, b in enumerate(o["briefs"], 1))
    cta = (
        "Ending mein yeh chaaron cheezein bolna zaroori hai, natural bolchal mein, 2-3 chhote vakyon mein: "
        "(1) video pasand aaye toh LIKE karo, (2) kisi ek dost ko SHARE karo jise yeh sunna chahiye, "
        "(3) COMMENT mein ek specific sawaal ka jawab likho (sawaal book ke topic se juda ho), "
        f"(4) '{CHANNEL_NAME}' channel ko subscribe karne ki baat bolo."
    )
    if n == 1:
        position = (f"Shuru mein HOOK (kaan khade karne wali pehli line), phir OPEN LOOP, phir ek chhoti line mein channel ka naam \"{CHANNEL_NAME}\". "
                    f"End se pehle bada TWIST (open loop ka poora jawab), phir ek line ka takeaway, phir call to action. {cta}")
    elif k == 1:
        position = (f"Shuru mein HOOK (kaan khade karne wali pehli line, channel ka naam nahi), phir OPEN LOOP, phir ek chhoti line mein channel ka naam \"{CHANNEL_NAME}\" "
                    "(jaise koi dost raaz khol raha ho), phir is part ke points. End mein cliffhanger jo agle part ki taraf le jaye. Is part mein like/subscribe ki baat mat karo.")
    elif k < n:
        position = ("Seedha pichhle part ki baat se jodkar shuru karo (jaise 'ab tak aapne dekha... lekin asli baat ab shuru hoti hai'). "
                    "Koi naya intro, hook ya channel ka naam mat lo. End mein cliffhanger jo agle part ki taraf le jaye. Like/subscribe ki baat mat karo.")
    else:
        position = (f"Seedha pichhle part ki baat se jodkar shuru karo, koi naya intro nahi. End se pehle bada TWIST (open loop ka poora jawab), "
                    f"phir ek line ka takeaway, phir call to action. {cta} Aakhri line mein agle raaz ka ishaara do, jaise 'Agla raaz isse bhi bada hai.'")
    prev_tail = ""
    if k > 1 and parts[k - 2]:
        prev_tail = parts[k - 2][-400:]
    prompt = f"""Tum ek top YouTube scriptwriter ho. Hindi (Devanagari) mein likho.
Book: {book}
Yeh {minutes} minute ki video {n} parts mein ban rahi hai. Abhi sirf PART {k}/{n} likho: kam se kam {words} words, aur {int(words * 1.1)} tak (lagbhag {words // WORDS_PER_MIN} minute ki awaaz).

Poori video ka plan (sab parts ek kahani ki tarah jude hain):
OPEN LOOP: {o['open_loop']}
TWIST: {o['twist']}
{plan}

Is part ka kaam: {o['briefs'][k - 1]}
{position}
""" + (f'\nPichhla part yahan khatam hua (sirf jodne ke liye, ise dobara mat likhna): "{prev_tail}"\n' if prev_tail else "") + """
Script likhne ke rules:
- Devanagari Hindi mein likho. English shabd bhi Devanagari mein likho (जैसे माइंडसेट, हैबिट).
- Chhote, tez vakya (zyada se zyada 12 words). Har vakya ke end mein purn viram (।), sawaal ya exclamation lagao.
- Seedhi doston jaisi bolchal ki bhasha, jaise koi dost raaz bata raha ho.
- Koi emoji, bullet list, markdown, heading, part ka number ya brackets mein stage directions nahi. Sirf wahi text likho jo bola jayega.
- Book ke concepts apne shabdon mein samjhao. Jhoothe statistics, nakli quotes ya banaye hue kisse nahi.
- Har point ke end mein ek alag cliffhanger line, ek hi line repeat mat karo. Ek point mein aam zindagi ka relatable scene dikhao (kisi asli insaan ka naam nahi).
- Rhythm: chhote tez vakya, beech mein 2-3 words ka jhatka, phir ek shaant vakya.
"""
    text = gemini_text(prompt)
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("**", "").replace("#", "").strip()
    text = re.sub(r"(?im)^\s*(PART|भाग)\s*\d+.*$", "", text).strip()
    if len(text) < 300:
        raise RuntimeError("Part ki script bahut chhoti aayi, dobara try karo")
    return text

def run_project_outline(pid):
    pj = project_jobs[pid]
    try:
        proj = proj_get(pid)
        o = proj["outline"]
        pj["msg"] = "Video ka plan ban raha hai..."
        res = generate_outline(o["book"], o["minutes"], o["n"])
        o.update({"open_loop": res["open_loop"], "twist": res["twist"], "briefs": res["briefs"]})
        proj_set_outline(pid, o, res["seo_text"])
        proj_set_parts(pid, [""] * o["n"])
        pj["status"] = "idle"
        pj["msg"] = "Plan ban gaya."
    except Exception as e:
        print("Project outline fail:", e)
        pj["status"] = "error"
        pj["msg"] = "Plan nahi ban paya: " + str(e)[:300]
        return
    start_project_voice(pid)


def start_project_voice(pid):
    pj = project_jobs.setdefault(pid, {"status": "idle", "msg": "", "warn": ""})
    if pj["status"] in ("prepare", "voice", "render"):
        return
    pj["status"] = "voice"
    pj["msg"] = "Voice shuru ho rahi hai..."
    threading.Thread(target=run_project_voice, args=(pid,), daemon=True).start()

def ensure_project_base(pid, title, work_dir, warnings):
    # Background + book card ek baar banta hai aur DB mein save hota hai, taaki saare parts ek jaise dikhein
    base_png = os.path.join(work_dir, "base.png")
    data = proj_blob_load(pid, 0)
    if data:
        with open(base_png, "wb") as f:
            f.write(data)
    else:
        build_long_base(base_png, title, work_dir, warnings)
        with open(base_png, "rb") as f:
            proj_blob_save(pid, 0, f.read())
    return base_png

def render_project_part(pid, i, part_text, family, base_png, work_dir):
    ext, data = proj_audio_load(pid, i)
    apath = os.path.join(work_dir, f"a{i}.{ext}")
    with open(apath, "wb") as f:
        f.write(data)
    dur = audio_duration(apath)
    ass_path = None
    if family:
        ass_path = os.path.join(work_dir, f"c{i}.ass")
        build_long_ass(part_text, dur, family, ass_path)
    ppath = os.path.join(work_dir, f"p{i}.mp4")
    render_long_part(base_png, apath, ass_path, FONT_DIR, ppath, dur)
    with open(ppath, "rb") as f:
        proj_blob_save(pid, i, f.read())
    for tmp in (apath, ass_path, ppath):
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return dur

def run_project_voice(pid):
    pj = project_jobs[pid]
    stop_event = threading.Event()
    threading.Thread(target=keep_awake_loop, args=(stop_event,), daemon=True).start()
    work_dir = os.path.join(VIDEO_DIR, pid + "_vw")
    all_done = False
    try:
        os.makedirs(work_dir, exist_ok=True)
        proj = proj_get(pid)
        parts = proj["parts"] or []
        n = len(parts)
        have = proj_audio_info(pid)
        vids = proj_blob_info(pid)
        family = ensure_font()
        warnings = []
        if not family:
            warnings.append("Hindi font download nahi hua, isliye captions nahi lage.")
        base_png = ensure_project_base(pid, proj["title"], work_dir, warnings)
        if warnings:
            pj["warn"] = " ".join(warnings)
        for i in range(1, n + 1):
            if not parts[i - 1]:
                pj["msg"] = f"Part {i}/{n}: script likhi ja rahi hai..."
                try:
                    parts[i - 1] = write_part_script(proj, i, parts)
                except Exception as e:
                    pj["status"] = "paused"
                    pj["msg"] = f"Part {i}/{n} ki script nahi ban payi (Gemini text limit ho sakti hai): {str(e)[:200]}. Thodi der baad ya kal 'Baaki parts banao' dabao."
                    return
                proj_set_parts(pid, parts)
            part = parts[i - 1]
            if i not in have:
                pj["msg"] = f"Part {i}/{n}: voice ban rahi hai..."
                audio, ext, err = long_voice_part(part)
                if audio is None:
                    pj["status"] = "paused"
                    if "roz ki free limit" in err:
                        pj["msg"] = f"Gemini ki roz ki free limit khatam ho gayi. {len(have)}/{n} parts bane hain, baaki kal 'Baaki parts banao' dabake banao."
                    else:
                        pj["msg"] = "Voice nahi bani: " + err
                    return
                data, ext2, dur = compress_for_store(audio, ext, work_dir)
                proj_audio_save(pid, i, ext2, data, dur)
                have[i] = dur
                if LONG_VOICE_ENGINE != "edge":
                    time.sleep(LONG_TTS_GAP)
            if i not in vids:
                pj["msg"] = f"Part {i}/{n}: video ban raha hai..."
                render_project_part(pid, i, part, family, base_png, work_dir)
                vids.add(i)
        all_done = True
        pj["status"] = "idle"
        pj["msg"] = "Saare parts ban gaye."
    except Exception as e:
        print("Project voice fail:", e)
        pj["status"] = "error"
        pj["msg"] = str(e)[:400]
    finally:
        stop_event.set()
        shutil.rmtree(work_dir, ignore_errors=True)
    if all_done:
        start_project_render(pid)

def start_project_render(pid):
    pj = project_jobs.setdefault(pid, {"status": "idle", "msg": "", "warn": ""})
    if pj["status"] in ("prepare", "voice", "render"):
        return
    proj = proj_get(pid)
    if not proj:
        return
    pj["status"] = "render"
    pj["msg"] = "Video render shuru ho rahi hai..."
    video_jobs[pid] = {
        "status": "working", "msg": "Shuru ho raha hai...", "file": None, "note": "",
        "voice": "", "music": "", "script_text": proj["script"], "start": time.time()
    }
    threading.Thread(target=run_project_render, args=(pid,), daemon=True).start()

def run_project_render(pid):
    job = video_jobs[pid]
    pj = project_jobs[pid]
    work_dir = os.path.join(VIDEO_DIR, pid + "_work")
    warnings = []
    stop_event = threading.Event()
    threading.Thread(target=keep_awake_loop, args=(stop_event,), daemon=True).start()
    try:
        proj = proj_get(pid)
        parts = proj["parts"] or []
        n = len(parts)
        have = proj_audio_info(pid)
        missing = [i for i in range(1, n + 1) if i not in have]
        if missing:
            raise ValueError(f"Voice ke parts poore nahi hain (baaki: {len(missing)}).")
        os.makedirs(work_dir, exist_ok=True)
        vids = proj_blob_info(pid)
        family, base_png = None, None
        if any(i not in vids for i in range(1, n + 1)):
            family = ensure_font()
            base_png = ensure_project_base(pid, proj["title"], work_dir, warnings)
        job["voice"] = "Voice: " + ("Edge " + EDGE_VOICE if LONG_VOICE_ENGINE == "edge" else "Gemini (Charon)")

        part_files, total_dur = [], 0.0
        for i, part in enumerate(parts, 1):
            job["msg"] = f"Step 1/2: Part {i}/{n} taiyar kiya ja raha hai..."
            pj["msg"] = job["msg"]
            if i not in vids:
                render_project_part(pid, i, part, family, base_png, work_dir)
            ppath = os.path.join(work_dir, f"p{i}.mp4")
            with open(ppath, "wb") as f:
                f.write(proj_blob_load(pid, i))
            part_files.append(ppath)
            total_dur += have[i]

        job["msg"] = "Step 2/2: Saare parts jodke final video ban rahi hai..."
        pj["msg"] = job["msg"]
        list_path = os.path.join(work_dir, "list.txt")
        with open(list_path, "w") as f:
            for p in part_files:
                f.write(f"file '{p}'\n")
        joined = os.path.join(work_dir, "joined.mp4")
        r = subprocess.run([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                            "-c", "copy", "-movflags", "+faststart", joined], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("parts join fail: " + r.stderr[-300:])

        os.makedirs(VIDEO_DIR, exist_ok=True)
        out_path = os.path.join(VIDEO_DIR, pid + ".mp4")
        music_label = "music band (MUSIC_ON = False)"
        done_music = False
        if MUSIC_ON:
            music_path, music_label = ensure_music(warnings)
            if music_path:
                try:
                    job["msg"] = "Step 2/2: Background music mix ho rahi hai..."
                    add_music_to_video(joined, music_path, out_path, total_dur)
                    done_music = True
                except Exception as e:
                    print("Project music fail:", e)
                    warnings.append("Music mix nahi hui, video bina music ke bani.")
                    music_label = "music nahi laga"
        if not done_music:
            shutil.move(joined, out_path)
        job["music"] = "Music: " + music_label
        job["file"] = out_path
        job["status"] = "done"
        job["msg"] = "Video ready!"
        job["note"] = " ".join(warnings)
        pj["status"] = "idle"
        pj["msg"] = "Video ready!"
    except Exception as e:
        print("Project render fail:", e)
        job["status"] = "error"
        job["msg"] = str(e)[:500]
        pj["status"] = "error"
        pj["msg"] = str(e)[:400]
    finally:
        stop_event.set()
        shutil.rmtree(work_dir, ignore_errors=True)

# ---------------------------------------------------------------
# SCRIPT + SEO
# ---------------------------------------------------------------

# Channel ka naam jab decide ho jaye yahan likh dena (khali rahega toh video mein bas "channel" bola jayega)
CHANNEL_NAME = "Book Secrets Hindi"

def build_script_prompt(book_name, video_format, minutes=35):
    minutes = max(2, min(45, int(minutes)))
    target_words = minutes * 125
    max_words = int(target_words * 1.1)
    n_ch = max(2, min(12, round(minutes / 4)))
    ch_words = int(target_words * 0.8 / n_ch)
    if CHANNEL_NAME:
        subscribe_line = f"'{CHANNEL_NAME}' channel ko subscribe karne ki baat bolo"
    else:
        subscribe_line = "channel ko subscribe karne ki baat bolo"

    cta_rule = (
        "Ending mein yeh chaaron cheezein bolna zaroori hai, natural bolchal mein, 2-3 chhote vakyon mein: "
        "(1) video pasand aaye toh LIKE karo, (2) kisi ek dost ko SHARE karo jise yeh sunna chahiye, "
        "(3) COMMENT mein ek specific sawaal ka jawab likho (sawaal book ke topic se juda ho), "
        f"(4) {subscribe_line}."
    )

    if video_format == "short":
        length_instruction = (
            "Yeh ek YouTube SHORT hai, voiceover lagbhag 45-55 second ka (kul 115-135 words, ending ke saath). Structure yeh rakho:\n"
            "1) HOOK: pehli line sirf 8-14 words ki ho, jo seedha chonka de ya curiosity gap bana de "
            "(koi shocking sach, ulta sawaal, ya 'aap galat soch rahe ho' wali baat). "
            "Namaste, welcome ya book ka intro bilkul nahi.\n"
            "2) OPEN LOOP: hook ke turant baad ek promise karo ki end mein ek aisi baat bataoge jo sab badal degi "
            "(jaise 'Aur teesri baat sabse zyada khatarnak hai, wahin tak ruko').\n"
            "3) THREE POINTS: har point ek chhota jhatka ho (contrast, galat dhaarna todna, ya ek real-life chhota scene). "
            "Har point ke end mein agle point ke liye curiosity chhodo.\n"
            "4) TWIST: end se pehle ek palat do, jaise 'Lekin asli sachchai yeh hai ki...', jo ab tak ki soch ko ulta kar de. "
            "Open loop ka jawab yahin do.\n"
            "5) ENDING: ek line ka takeaway, phir call to action. " + cta_rule
        )
        hashtag_rule = "3 se 5 hashtags, sabse zaroori pehle (YouTube pehle 3 title ke upar dikhata hai), aur aakhri hashtag #Shorts ho."
    else:
        length_instruction = (
            f"Yeh ek LONG YouTube video hai. Voiceover lagbhag {minutes} minute ka ho, yaani kam se kam {target_words} words aur {max_words} words tak. "
            "Script chhoti mat karna, poori lambi likhna, aur saare chapters ek hi baar mein likhna. Structure yeh rakho:\n"
            "1) HOOK: pehle 15 second mein chonkane wala sach ya sawaal, aur promise ki end mein ek bada twist milega.\n"
            "2) OPEN LOOP: hook ke baad batao ki end mein ek aisi baat khulegi jo sab badal degi.\n"
            f"3) {n_ch} CHAPTERS: har chapter kam se kam {ch_words} words ka. Har chapter mein ek relatable scene ya chhoti kahani, "
            "book ka ek bada idea apne shabdon mein, ek real-life example, aur chapter ke end mein ek naya cliffhanger.\n"
            "4) Beech mein ek mini twist ho, taaki sunne wala aadhe video mein chhod ke na jaye. "
            "Har 2-3 chapters ke baad ek chhoti line: 'agar ab tak pasand aa raha hai toh like kar do'.\n"
            "5) Aakhir se pehle bada TWIST jo poori video ki soch palat de, aur shuru ke open loop ka poora jawab de.\n"
            "6) ENDING: ek line ka takeaway, phir call to action. " + cta_rule
        )
        hashtag_rule = "3 se 5 hashtags, sabse zaroori pehle (YouTube pehle 3 title ke upar dikhata hai)."

    return f"""Tum ek top YouTube scriptwriter aur SEO expert ho jo books ko Hindi (Hinglish, Roman letters) mein suspense ke saath explain karta hai. Tumhari script ka kaam yeh hai ki dekhne wala beech mein video chhodke na jaye, aur video YouTube search mein dikhe.

Book: {book_name}

{length_instruction}

Script likhne ke rules:
- Chhote, tez vakya likho (zyada se zyada 12 words). Har vakya ke end mein full stop, sawaal ya exclamation lagao, kyunki awaaz wahin ruk-ruk ke bolegi.
- Seedhi doston jaisi bolchal ki bhasha, jaise koi dost raaz bata raha ho.
- Koi emoji, bullet list, markdown, ya brackets mein stage directions mat likho. Sirf wahi text likho jo bola jayega.
- Book ke concepts apne shabdon mein samjhao. Book ke asli ideas hi use karo. Jhoothe statistics, nakli quotes ya banaye hue kisse mat likho.
- HOOK mein jo lines likho unhe SCRIPT mein dobara mat likhna, SCRIPT wahin se aage badhe.
- CHANNEL KA NAAM: "{CHANNEL_NAME}" bilkul 2 baar bolo. Pehli baar open loop ke baad ek chhoti line mein, jaise koi dost raaz khol raha ho: "{CHANNEL_NAME} par hum yahi chhupe raaz kholte hain." Doosri baar ending mein subscribe ke saath. HOOK mein channel ka naam mat lo.
- HOOK ki taakat: pehli line aisi ho ki kaan khade ho jayein. Ya toh ek shocking sach, ya ek ulta sawaal, ya ek chhota dramatic scene. Seedha 'aap' se baat karo, jaise aapki hi baat ho rahi hai.
- SUSPENSE: har point ke end mein ek alag cliffhanger line do, jaise "Lekin yahan kahani palat jaati hai." ya "Par asli khel abhi baaki hai." Ek hi line baar baar repeat mat karo. Beech mein ek baar sunne wale ko rok do ek sawaal se, jaise "Ab sochiye, aap hote toh kya karte?"
- SCENE: ek point mein aam zindagi ka relatable scene dikhao, jaise "Raat ke do baje, aap phone scroll kar rahe ho." Isse sunne wale ko lage ki yeh uski hi kahani hai. Kisi asli insaan ya jhoothe aankdon ka naam mat banao.
- RHYTHM: chhote tez vakya, beech mein 2-3 words ka jhatka, jaise "Sach? Bilkul ulta." Phir ek shaant vakya. Isi tarah sunne wale ka dil upar neeche hota rahe.
- TWIST: end se pehle aisa palat ho ki sunne wale ko lage "arre, yeh toh maine socha hi nahi tha." Twist book ke asli idea se nikle, aur shuru ke open loop ka poora jawab de.
- ENDING: ek line ka takeaway jo yaad reh jaye. Phir call to action. Aakhri line mein agle raaz ka ishaara do, jaise "Agla raaz isse bhi bada hai." Taaki log aur videos dekhne ruke.
- BHASHA: aasaan Hinglish, bhaari shabd nahi, kyunki awaaz saaf bolni chahiye.

SEO ke rules (YouTube search aur click ke liye):
- TITLE: 60 characters ke andar. Shuru mein main keyword (book ka naam + 'Book Summary in Hindi' ya 'Hindi Mein'), saath mein ek number ya curiosity (jaise '3 Raaz', 'Sach Jo Koi Nahi Batata'). Jhooth wala clickbait nahi, jo video mein hai wahi promise karo.
- DESCRIPTION: pehli 2 lines (150 characters ke andar) mein book ka naam, 'Book Summary in Hindi' aur hook ho, kyunki search mein wahi dikhti hain. Phir 3-4 lines mein video ka summary, ek line mein "{CHANNEL_NAME}" ke naam ke saath channel ki pehchaan (jaise 'Roz ek book ka raaz Hindi mein'). Phir ek line: Like, Share, Comment aur Subscribe karna mat bhoolna. End mein ek line Devanagari Hindi mein (jaise 'बुक समरी हिंदी में'). Author ka naam tabhi likho jab pakka pata ho.
- HASHTAGS: {hashtag_rule}
- TAGS: 12-15 search keywords, comma se alag, Hinglish aur English mix. Inme book ka naam + 'summary in hindi', 'book summary hindi', 'book secrets hindi', 'best self help books hindi' jaise tags ho, aur 3-4 tags Devanagari Hindi mein. Kul 400 characters ke andar.
- PINNED COMMENT: 1-2 line ka ek specific sawaal jo book ke twist se juda ho, taaki log apna jawab likhne ko majboor ho jayein. Aakhir mein 'Agla raaz kaunsi book ka chahiye?' jaisi line jodo.
- THUMBNAIL TEXT: 3-5 words, badi akshar mein dikhne layak, hook se mile hue, curiosity wale. Koi emoji nahi.

Format bilkul yahi rakho, isi order mein (headings ke naam mat badalna):

TITLE: (title)

HOOK: (pehli 1-2 chonkane wali lines, jo video ke shuru mein bolenge)

SCRIPT:
(hook ke baad ka poora script: open loop, points, twist, ending aur call to action)

DESCRIPTION: (description)

HASHTAGS: (hashtags)

TAGS: (comma se alag keywords)

PINNED COMMENT: (comment)

THUMBNAIL TEXT: (text)
"""

def parse_sections(text):
    # Gemini ke output ko TITLE/DESCRIPTION/TAGS jaise hisson mein todta hai
    clean = text.replace("**", "")
    clean = re.sub(r"(?m)^\s*-{3,}\s*$", "", clean)
    pattern = r"(?im)^\s*(TITLE|HOOK|SCRIPT|DESCRIPTION|HASHTAGS|TAGS|PINNED COMMENT|THUMBNAIL TEXT)\s*:\s*"
    matches = list(re.finditer(pattern, clean))
    sections = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(clean)
        sections[m.group(1).upper()] = clean[m.end():end].strip()
    return sections

SEO_CSS = """
            .seo { background: white; padding: 15px; margin-top: 20px; border-radius: 8px; }
            .seo-item { margin-top: 14px; }
            .seo-text { background: #f6f6f6; padding: 10px; border-radius: 6px; margin-top: 6px; white-space: pre-wrap; font-size: 14px; word-break: break-word; }
            button.copy { margin-top: 8px; padding: 8px; background: #3498db; color: white; border: none; border-radius: 5px; width: 100%; font-size: 14px; }
"""

UPLOAD_SETTINGS = (
    "Category: Education\n"
    "Language: Hindi\n"
    "Audience: No, it's not made for kids\n"
    "Comments: Allow all\n"
    "Altered/synthetic content: Studio ka sawaal padho aur sach jawab do (realistic asli insaan jaisa AI content ho toh Yes)\n"
    "Visibility: pehle Unlisted/Private pe check karo, phir Public"
)

COPY_JS = """
<script>
function copyText(id, btn) {
  var el = document.getElementById(id);
  var txt = el.innerText;
  function done() {
    btn.innerText = '✅ Copy ho gaya';
    setTimeout(function() { btn.innerText = '📋 Copy'; }, 1500);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(txt).then(done);
  } else {
    var r = document.createRange();
    r.selectNodeContents(el);
    var s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
    document.execCommand('copy');
    done();
  }
}
</script>
"""

def build_seo_cards(sections):
    if not sections.get("TITLE") and not sections.get("DESCRIPTION"):
        return ""
    title = sections.get("TITLE", "").replace("\n", " ").strip()
    desc = sections.get("DESCRIPTION", "").strip()
    hashtags = sections.get("HASHTAGS", "").replace("\n", " ").strip()
    full_desc = (desc + "\n\n" + hashtags).strip()
    tags = sections.get("TAGS", "").replace("\n", " ").strip()
    pinned = sections.get("PINNED COMMENT", "").strip()
    thumb = sections.get("THUMBNAIL TEXT", "").strip()

    items = [
        ("Title", title, f"{len(title)} characters (60 ke andar best)"),
        ("Description (hashtags ke saath)", full_desc, ""),
        ("Tags", tags, f"{len(tags)} characters (500 ke andar rakho)"),
        ("Pinned comment", pinned, ""),
        ("Thumbnail text", thumb, ""),
    ]
    out = "<div class='seo'><h3>📈 YouTube SEO Details</h3>"
    for i, (label, value, hint) in enumerate(items):
        if not value:
            continue
        hint_html = (" <small>" + html_lib.escape(hint) + "</small>") if hint else ""
        out += (
            "<div class='seo-item'><b>" + html_lib.escape(label) + "</b>" + hint_html +
            "<div class='seo-text' id='seo" + str(i) + "'>" + html_lib.escape(value) + "</div>"
            "<button class='copy' type='button' onclick=\"copyText('seo" + str(i) + "', this)\">📋 Copy</button></div>"
        )
    out += (
        "<div class='seo-item'><b>⚙️ YouTube Studio upload settings</b>"
        "<div class='seo-text'>" + html_lib.escape(UPLOAD_SETTINGS) + "</div></div>"
    )
    out += "</div>" + COPY_JS
    return out

# ---------------------------------------------------------------
# YOUTUBE DASHBOARD PAGES
# ---------------------------------------------------------------

@app.route("/dashboard/youtube", methods=["GET", "POST"])
def youtube_script():
    script_result = None
    error_msg = ""
    book_name = ""
    video_format = "short"
    minutes = 35

    if request.method == "POST":
        book_name = request.form.get("book_name", "")
        video_format = request.form.get("format", "short")
        try:
            raw_min = request.form.get("minutes", "").strip()
            minutes = int(float(raw_min)) if raw_min else 35
        except Exception:
            minutes = 35
        minutes = max(2, min(45, minutes))

        prompt = build_script_prompt(book_name, video_format, minutes)
        try:
            script_result = gemini_text(prompt)
        except Exception as e:
            error_msg = "Script nahi ban payi: " + str(e)[:300]

    escaped_script = html_lib.escape(script_result) if script_result else ""
    escaped_book = html_lib.escape(book_name, quote=True)
    escaped_result = html_lib.escape(script_result) if script_result else ""
    seo_block = build_seo_cards(parse_sections(script_result)) if script_result else ""
    error_html = f"<p style='background:#f8d7da;padding:10px;border-radius:6px;margin-top:20px;'>⚠️ {html_lib.escape(error_msg)}</p>" if error_msg else ""

    action_block = ""
    if script_result:
        if video_format == "short":
            video_part = f"""
        <form method="POST" action="/dashboard/youtube/video" style="margin-top:15px;">
            <textarea name="script_text" style="display:none;">{escaped_script}</textarea>
            <input type="hidden" name="book_name" value="{escaped_book}">
            <button type="submit" style="background:#e67e22;">🎬 Video Banao (Stock + Zoom + Hindi Captions)</button>
        </form>"""
        else:
            video_part = "<p style='color:#666;font-size:14px;margin-top:15px;'>Long video ke liye upar wala <b>🧩 Part-wise Long Project</b> button use karo.</p>"
        action_block = video_part + f"""
        <form method="POST" action="/dashboard/youtube/audio" style="margin-top:5px;">
            <textarea name="script_text" style="display:none;">{escaped_script}</textarea>
            <button type="submit" style="background:#8e44ad;">🔊 Sirf Voiceover</button>
        </form>
        """

    html = f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial; background: #f0f2f5; padding: 20px; }}
            a.back {{ color: #007bff; text-decoration: none; font-size: 16px; }}
            label {{ font-weight: bold; display: block; margin-top: 15px; }}
            input[type=text], select {{ width: 100%; padding: 10px; margin-top: 5px; box-sizing: border-box; font-size: 16px; }}
            button {{ margin-top: 20px; padding: 12px; background: #28a745; color: white; border: none; border-radius: 5px; width: 100%; font-size: 16px; }}
            .result {{ background: white; padding: 15px; margin-top: 20px; border-radius: 8px; white-space: pre-wrap; font-size: 14px; line-height: 1.6; }}
            {SEO_CSS}
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard">&larr; Back</a> &nbsp;|&nbsp; <a class="back" href="/dashboard/longprojects">📂 Mere Long Projects</a>
        <h2>📚 Book Script Generator</h2>
        <form method="POST">
            <label>Book ka naam</label>
            <input type="text" name="book_name" value="{escaped_book}" placeholder="jaise: Atomic Habits" required>

            <label>Video format</label>
            <select name="format">
                <option value="short" {"selected" if video_format=="short" else ""}>Short (45-60 sec, vertical)</option>
                <option value="long" {"selected" if video_format=="long" else ""}>Long (aap minute chuno, horizontal)</option>
            </select>

            <div id="minwrap" style="display:{'block' if video_format=='long' else 'none'};">
                <label>Video kitne minute ki chahiye? (2 se 45)</label>
                <input type="number" name="minutes" min="2" max="45" value="{minutes}" placeholder="jaise: 10"
                       style="width:100%;padding:12px;margin-top:6px;border:1px solid #ccc;border-radius:5px;font-size:16px;box-sizing:border-box;">
            </div>
            <script>
                document.querySelector('select[name=format]').addEventListener('change', function() {{
                    document.getElementById('minwrap').style.display = (this.value === 'long') ? 'block' : 'none'; document.getElementById('partbtn').style.display = (this.value === 'long') ? 'block' : 'none';
                }});
            </script>

            <button type="submit">Script Generate Karo</button>
            <button type="submit" id="partbtn" formaction="/dashboard/youtube/longparts"
                    style="background:#8e44ad;display:{'block' if video_format=='long' else 'none'};">🧩 Part-wise Long Project (5-5 minute ke jude hue parts)</button>
        </form>
        {error_html}
        {"<div class='result'>" + escaped_result + "</div>" if script_result else ""}
        {action_block}
        {seo_block}
    </body>
    </html>
    """
    return html

# Sirf voiceover download
@app.route("/dashboard/youtube/audio", methods=["POST"])
def youtube_audio():
    script_text = request.form.get("script_text", "")
    clean_text = extract_voiceover_text(script_text)

    if not clean_text:
        return "Voiceover ke liye text nahi mila", 400

    audio_bytes, ext, _, _ = make_voice(clean_text)
    mimetype = "audio/wav" if ext == "wav" else "audio/mpeg"
    file_name = "voiceover.wav" if ext == "wav" else "voiceover_BACKUP_voice.mp3"
    return send_file(
        BytesIO(audio_bytes),
        mimetype=mimetype,
        as_attachment=True,
        download_name=file_name
    )

# Video job shuru karo (background mein chalega)
@app.route("/dashboard/youtube/video", methods=["POST"])
def youtube_video_start():
    script_text = request.form.get("script_text", "")
    book_name = (request.form.get("book_name", "") or "Book Summary").strip()[:60]

    os.makedirs(VIDEO_DIR, exist_ok=True)
    cleanup_old_videos()

    job_id = uuid.uuid4().hex[:10]
    video_jobs[job_id] = {
        "status": "working",
        "msg": "Shuru ho raha hai...",
        "file": None,
        "note": "",
        "voice": "",
        "music": "",
        "script_text": script_text,
        "start": time.time()
    }
    t = threading.Thread(
        target=run_video_job,
        args=(job_id, script_text, book_name),
        daemon=True
    )
    t.start()
    return redirect("/dashboard/youtube/video/" + job_id)




@app.route("/dashboard/youtube/longparts", methods=["POST"])
def youtube_long_parts_start():
    book_name = (request.form.get("book_name", "") or "").strip()[:60]
    if not book_name:
        return redirect("/dashboard/youtube")
    try:
        raw_min = request.form.get("minutes", "").strip()
        minutes = int(float(raw_min)) if raw_min else 35
    except Exception:
        minutes = 35
    minutes = max(2, min(45, minutes))
    n = max(1, -(-minutes // PART_MINUTES))
    try:
        pid = proj_create(book_name, "")
        proj_set_outline(pid, {"partwise": True, "book": book_name, "minutes": minutes, "n": n})
    except Exception as e:
        return "Project nahi ban paya (database error): " + html_lib.escape(str(e)[:300]), 500
    project_jobs[pid] = {"status": "prepare", "msg": "Video ka plan ban raha hai...", "warn": ""}
    threading.Thread(target=run_project_outline, args=(pid,), daemon=True).start()
    return redirect("/dashboard/longproject/" + pid)


# Video status page (apne aap refresh hota hai)
@app.route("/dashboard/youtube/video/<job_id>")
def youtube_video_status(job_id):
    job = video_jobs.get(job_id)
    if not job:
        return ("Yeh video job nahi mila (server restart ho gaya hoga). "
                "<a href='/dashboard/youtube'>Dobara try karo</a>"), 404

    elapsed = int(time.time() - job["start"])
    note = job.get("note", "")
    note_html = f"<p style='background:#fff3cd;padding:10px;border-radius:6px;'>⚠️ {html_lib.escape(note)}</p>" if note else ""
    refresh = ""
    if job["status"] == "working":
        refresh = '<meta http-equiv="refresh" content="5">'
        body = f"""
        <h3>⏳ Video ban raha hai...</h3>
        <p>{html_lib.escape(job['msg'])}</p>
        <p>Time ho gaya: {elapsed} second</p>
        <p>Yeh page apne aap refresh hota rahega. Ise band mat karna aur phone ki screen on rakhna. Short mein 3-8 minute lagte hain, Long mein bahut zyada (har 1 minute ki video par kai minute).</p>
        """
    elif job["status"] == "done":
        voice_line = html_lib.escape(job.get("voice", ""))
        music_line = html_lib.escape(job.get("music", ""))
        seo_html = build_seo_cards(parse_sections(job.get("script_text", "")))
        body = f"""
        <h3>✅ Video ready!</h3>
        <video controls playsinline style="width:100%;max-width:420px;border-radius:8px;">
            <source src="/dashboard/youtube/video/{job_id}/file" type="video/mp4">
        </video>
        <a class="dl" href="/dashboard/youtube/video/{job_id}/file?dl=1">⬇️ Video Download Karo</a>
        <p>Banane mein {elapsed} second lage.</p>
        <p>🎙️ {voice_line}<br>🎵 {music_line}</p>
        {note_html}
        {seo_html}
        """
    else:
        body = f"""
        <h3>❌ Video nahi ban paya</h3>
        <p style="background:white;padding:10px;border-radius:6px;word-break:break-word;">{html_lib.escape(job['msg'])}</p>
        <p>Is error ka screenshot bhej do, hum fix kar denge.</p>
        """

    return f"""
    <html>
    <head>
        {refresh}
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial; background: #f0f2f5; padding: 20px; }}
            a.back {{ color: #007bff; text-decoration: none; font-size: 16px; }}
            a.dl {{ display: block; margin-top: 15px; padding: 12px; background: #28a745; color: white; text-align: center; border-radius: 5px; text-decoration: none; font-size: 16px; }}
            {SEO_CSS}
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard/youtube">&larr; Script Generator</a>
        {body}
    </body>
    </html>
    """

@app.route("/dashboard/youtube/video/<job_id>/file")
def youtube_video_file(job_id):
    job = video_jobs.get(job_id)
    if not job or job["status"] != "done" or not job["file"] or not os.path.exists(job["file"]):
        return "Video file nahi mili", 404
    as_attachment = request.args.get("dl") == "1"
    return send_file(
        job["file"],
        mimetype="video/mp4",
        as_attachment=as_attachment,
        download_name="book_video.mp4",
        conditional=True
    )

# ---------------- Long Projects: pages ----------------
PROJECT_CSS = """
body { font-family: Arial; background: #f0f2f5; padding: 20px; max-width: 640px; margin: auto; }
a.back { color: #007bff; text-decoration: none; font-size: 16px; }
.card { background: white; padding: 15px; border-radius: 8px; margin: 12px 0; }
button { width: 100%; padding: 14px; border: none; border-radius: 6px; color: white; font-size: 16px; margin-top: 10px; background: #28a745; }
.part { padding: 6px 0; border-bottom: 1px solid #eee; font-size: 15px; }
.msg { background: #fff3cd; padding: 10px; border-radius: 6px; word-break: break-word; }
"""

def _fmt_dur(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"

@app.route("/dashboard/longprojects")
def long_projects_list():
    try:
        items = proj_list()
    except Exception as e:
        return "Projects nahi khul paye: " + html_lib.escape(str(e)[:200])
    rows = ""
    for it in items:
        rows += (f"<div class='card'><a href='/dashboard/longproject/{it['id']}'><b>{html_lib.escape(it['title'] or 'Bina naam')}</b></a>"
                 f"<br>Voice parts: {it['done']}/{it['total']}</div>")
    if not rows:
        rows = "<p>Abhi koi Long project nahi hai.</p>"
    return f"""<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>{PROJECT_CSS}</style></head>
    <body><a class="back" href="/dashboard/youtube">&larr; Script Generator</a><h2>📂 Mere Long Projects</h2>{rows}</body></html>"""

@app.route("/dashboard/longproject/<pid>")
def long_project_page(pid):
    proj = proj_get(pid)
    if not proj:
        return "Project nahi mila. <a href='/dashboard/longprojects'>Projects dekho</a>", 404
    pj = project_jobs.get(pid, {"status": "idle", "msg": "", "warn": ""})
    parts = proj["parts"]
    have = proj_audio_info(pid) if parts else {}
    vids = {i for i in proj_blob_info(pid) if i > 0} if parts else set()
    n = len(parts) if parts else 0
    running = pj["status"] in ("prepare", "voice", "render")
    refresh = '<meta http-equiv="refresh" content="5">' if running else ""

    rows = ""
    for i in range(1, n + 1):
        vtxt = (f" &nbsp;🎞️ <a href='/dashboard/longproject/{pid}/part/{i}'>▶ dekho</a>" if i in vids else (" &nbsp;⏳ video baaki" if i in have else ""))
        ptxt = parts[i - 1]
        stxt = "📝 script ✅ " if ptxt else "📝 script baaki "
        det = f"<details><summary style='font-size:13px;color:#555;'>script padho</summary><div style='font-size:14px;white-space:pre-wrap;'>{html_lib.escape(ptxt)}</div></details>" if ptxt else ""
        rows += f"<div class='part'>Part {i}: {stxt}" + (f"✅ voice {_fmt_dur(have[i])}" if i in have else "⏳ voice baaki") + vtxt + det + "</div>"
    total_done = sum(have.values())
    status_html = ""
    if pj.get("msg"):
        status_html = f"<div class='msg'>{html_lib.escape(pj['msg'])}</div>"
    if pj.get("warn"):
        status_html += f"<div class='msg' style='margin-top:8px;'>⚠️ {html_lib.escape(pj['warn'])}</div>"

    buttons = ""
    vjob = video_jobs.get(pid)
    if not parts and not running:
        buttons = f"<form method='POST' action='/dashboard/longproject/{pid}/prepare'><button style='background:#6c757d;'>🔄 Taiyari dobara karo</button></form>"
    elif not running:
        if len(have) < n or len(vids) < n or any(not p for p in parts):
            buttons += f"<form method='POST' action='/dashboard/longproject/{pid}/voice'><button style='background:#8e44ad;'>🎙️ Baaki parts banao (voice + video)</button></form>"
        else:
            buttons += f"<form method='POST' action='/dashboard/longproject/{pid}/render'><button style='background:#c0392b;'>🎬 Final video banao ({_fmt_dur(total_done)})</button></form>"
    if vjob:
        buttons += f"<a href='/dashboard/youtube/video/{pid}'><button type='button' style='background:#007bff;'>▶️ Video status / download</button></a>"

    limit_info = ""
    if n and LONG_VOICE_ENGINE != "edge":
        limit_info = (f"<p style='color:#666;font-size:14px;'>Is video ko {n} voice requests chahiye. "
                      f"Gemini free limit: {TTS_DAILY_LIMIT} requests/din (kal subah reset).</p>")
    seo_html = build_seo_cards(parse_sections(proj["script"]))
    return f"""<html><head>{refresh}<meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{PROJECT_CSS}{SEO_CSS}</style></head><body>
    <a class="back" href="/dashboard/longprojects">&larr; Mere Long Projects</a>
    <h2>🎬 {html_lib.escape(proj['title'] or 'Long project')}</h2>
    <div class="card"><b>Voice: {len(have)}/{n} &nbsp; Video parts: {len(vids)}/{n}</b> &nbsp; ({_fmt_dur(total_done)} ban chuki)
    {limit_info}{rows}</div>
    {status_html}
    {buttons}
    {seo_html}
    </body></html>"""

@app.route("/dashboard/longproject/<pid>/part/<int:i>")
def long_project_part(pid, i):
    data = proj_blob_load(pid, i) if i > 0 else None
    if not data:
        return "Yeh part abhi nahi bana", 404
    return send_file(BytesIO(data), mimetype="video/mp4", conditional=True, download_name=f"part{i}.mp4")

@app.route("/dashboard/longproject/<pid>/voice", methods=["POST"])
def long_project_voice(pid):
    if proj_get(pid):
        start_project_voice(pid)
    return redirect("/dashboard/longproject/" + pid)

@app.route("/dashboard/longproject/<pid>/render", methods=["POST"])
def long_project_render(pid):
    if proj_get(pid):
        start_project_render(pid)
    return redirect("/dashboard/longproject/" + pid)

@app.route("/dashboard/longproject/<pid>/prepare", methods=["POST"])
def long_project_prepare(pid):
    if proj_get(pid):
        pj = project_jobs.setdefault(pid, {"status": "idle", "msg": "", "warn": ""})
        if pj["status"] not in ("prepare", "voice", "render"):
            pj["status"] = "prepare"
            pj["msg"] = "Taiyari shuru..."
            threading.Thread(target=run_project_outline, args=(pid,), daemon=True).start()
    return redirect("/dashboard/longproject/" + pid)


init_db()
try:
    ensure_long_tables()
except Exception as e:
    print('Long tables setup fail:', e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
