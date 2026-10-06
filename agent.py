import os
import re
import json
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

# Roman Hinglish ko Devanagari Hindi mein badalta hai (voice natural lagti hai)
def to_devanagari(text):
    try:
        model = genai.GenerativeModel(model_name="gemini-3.6-flash")
        prompt = (
            "Neeche diye Hinglish text ko Devanagari (Hindi) script mein likho, "
            "bilkul natural bolchal ki Hindi mein. English words ko bhi Devanagari mein "
            "likho jaise Hindi mein bole jaate hain. Sirf converted text do, koi explanation "
            "ya markdown mat do.\n\n" + text
        )
        resp = model.generate_content(prompt)
        out = resp.text.strip()
        return out if out else text
    except Exception as e:
        print("Devanagari conversion failed:", e)
        return text

# Gemini TTS se natural male Hindi voice (WAV bytes return karta hai)
def gemini_tts_wav(text):
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
    r = requests.post(url, headers=headers, json=payload, timeout=90)
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

# Voice banata hai: (audio_bytes, "wav" ya "mp3", hindi_text) return karta hai
def make_voice(clean_text):
    hindi_text = to_devanagari(clean_text)
    try:
        return gemini_tts_wav(hindi_text), "wav", hindi_text
    except Exception as e:
        print("Gemini TTS fail hua, edge-tts use kar rahe hain:", e)
        return edge_tts_mp3_bytes(hindi_text), "mp3", hindi_text

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
MAX_VIDEO_SCENES = 4      # kitne scenes stock VIDEO clip honge (baaki photos)
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
    queries = []
    try:
        model = genai.GenerativeModel(model_name="gemini-3.6-flash")
        txt = model.generate_content(prompt).text
        m = re.search(r"\[.*\]", txt, re.DOTALL)
        queries = [str(q).strip() for q in json.loads(m.group(0)) if str(q).strip()]
    except Exception as e:
        print("Scene queries fail:", e)
    while len(queries) < n:
        queries.append(FALLBACK_QUERIES[len(queries) % len(FALLBACK_QUERIES)])
    return queries[:n]

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
    stock_ok = bool(os.environ.get("PEXELS_API_KEY"))
    if not stock_ok:
        warnings.append("PEXELS_API_KEY set nahi hai, isliye stock photo/video ki jagah simple background use hua.")
    missing = 0
    for i, q in enumerate(queries):
        got = None
        if stock_ok:
            want_video = (i % 2 == 1) and video_count < MAX_VIDEO_SCENES
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

# ---- Final render ----
def render_video(media, durs, ass_path, font_dir, audio_path, out_path, total_dur):
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

    cmd += [
        "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", f"{audio_idx}:a",
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
        audio_bytes, ext, hindi_text = make_voice(roman_text)
        audio_path = os.path.join(work_dir, "voice." + ext)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        total_dur = audio_duration(audio_path)

        job["msg"] = "Step 2/4: Scenes plan ho rahe hain aur stock photo/video dhoondhe ja rahe hain..."
        groups, durs = plan_scenes(roman_text, total_dur)
        queries = get_scene_queries(groups, title)
        media = gather_media(queries, work_dir, warnings)

        job["msg"] = "Step 3/4: Hindi captions bana rahe hain..."
        family = ensure_font()
        ass_path = None
        if family:
            ass_path = os.path.join(work_dir, "captions.ass")
            build_ass(hindi_text, total_dur, family, ass_path)
        else:
            warnings.append("Hindi font download nahi hua, isliye captions nahi lage.")

        job["msg"] = "Step 4/4: Video render ho raha hai (zoom + transitions + captions)..."
        out_path = os.path.join(VIDEO_DIR, job_id + ".mp4")
        render_video(media, durs, ass_path, FONT_DIR, audio_path, out_path, total_dur)

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
# YOUTUBE DASHBOARD PAGES
# ---------------------------------------------------------------

@app.route("/dashboard/youtube", methods=["GET", "POST"])
def youtube_script():
    script_result = None
    book_name = ""
    video_format = "short"

    if request.method == "POST":
        book_name = request.form.get("book_name", "")
        video_format = request.form.get("format", "short")

        if video_format == "short":
            length_instruction = "Yeh ek YouTube SHORT (45-60 second) ke liye script hai. Bahut engaging hook se shuru karo, 3-4 main points crisp explain karo, aur ek strong ending line do."
        else:
            length_instruction = "Yeh ek LONG YouTube video (7-10 minute) ke liye detailed script hai. Achha hook, book ka background, 5-7 main lessons/concepts detail mein explain karo (apne shabdon mein, book se copy nahi), real-life examples do, aur ek achha conclusion do."

        prompt = f"""Tum ek YouTube content writer ho jo books explain karta hai Hindi mein (Hinglish style, jaise log bolte hain).

Book: {book_name}

{length_instruction}

Important: Book ke concepts/lessons apne shabdon mein samjhao, kahi se copy mat karo. Format yeh do:

TITLE: (catchy YouTube title)

HOOK: (pehli 2 lines jo curiosity banaye)

SCRIPT:
(poora script yaha)

DESCRIPTION: (YouTube video description, 2-3 lines)

TAGS: (5-8 relevant hashtags)
"""

        model = genai.GenerativeModel(model_name="gemini-3.6-flash")
        response = model.generate_content(prompt)
        script_result = response.text

    escaped_script = html_lib.escape(script_result) if script_result else ""
    escaped_book = html_lib.escape(book_name, quote=True)
    escaped_result = html_lib.escape(script_result) if script_result else ""

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
            video_part = "<p style='color:#a94442;margin-top:15px;'>Long video ka render free server pe bahut slow hai, isliye abhi video sirf Short format ke liye hai.</p>"
        action_block = video_part + f"""
        <form method="POST" action="/dashboard/youtube/audio" style="margin-top:5px;">
            <textarea name="script_text" style="display:none;">{escaped_script}</textarea>
            <button type="submit" style="background:#8e44ad;">🔊 Sirf Voiceover</button>
        </form>
        """

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial; background: #f0f2f5; padding: 20px; }}
            a.back {{ color: #007bff; text-decoration: none; font-size: 16px; }}
            label {{ font-weight: bold; display: block; margin-top: 15px; }}
            input[type=text], select {{ width: 100%; padding: 10px; margin-top: 5px; box-sizing: border-box; font-size: 16px; }}
            button {{ margin-top: 20px; padding: 12px; background: #28a745; color: white; border: none; border-radius: 5px; width: 100%; font-size: 16px; }}
            .result {{ background: white; padding: 15px; margin-top: 20px; border-radius: 8px; white-space: pre-wrap; font-size: 14px; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard">&larr; Back</a>
        <h2>📚 Book Script Generator</h2>
        <form method="POST">
            <label>Book ka naam</label>
            <input type="text" name="book_name" value="{escaped_book}" placeholder="jaise: Atomic Habits" required>

            <label>Video format</label>
            <select name="format">
                <option value="short" {"selected" if video_format=="short" else ""}>Short (45-60 sec, vertical)</option>
                <option value="long" {"selected" if video_format=="long" else ""}>Long (7-10 min, horizontal)</option>
            </select>

            <button type="submit">Script Generate Karo</button>
        </form>
        {"<div class='result'>" + escaped_result + "</div>" if script_result else ""}
        {action_block}
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

    audio_bytes, ext, _ = make_voice(clean_text)
    mimetype = "audio/wav" if ext == "wav" else "audio/mpeg"
    return send_file(
        BytesIO(audio_bytes),
        mimetype=mimetype,
        as_attachment=True,
        download_name="voiceover." + ext
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
        "start": time.time()
    }
    t = threading.Thread(
        target=run_video_job,
        args=(job_id, script_text, book_name),
        daemon=True
    )
    t.start()
    return redirect("/dashboard/youtube/video/" + job_id)

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
        <p>Yeh page apne aap refresh hota rahega. Ise band mat karna, free server pe isme 3-8 minute lag sakte hain.</p>
        """
    elif job["status"] == "done":
        body = f"""
        <h3>✅ Video ready!</h3>
        <video controls playsinline style="width:100%;max-width:420px;border-radius:8px;">
            <source src="/dashboard/youtube/video/{job_id}/file" type="video/mp4">
        </video>
        <a class="dl" href="/dashboard/youtube/video/{job_id}/file?dl=1">⬇️ Video Download Karo</a>
        <p>Banane mein {elapsed} second lage.</p>
        {note_html}
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

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
