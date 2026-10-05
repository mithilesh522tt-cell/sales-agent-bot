import os
import re
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
from PIL import Image, ImageDraw, ImageFont
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

# Voice banata hai: (audio_bytes, "wav" ya "mp3") return karta hai
def make_voice(clean_text):
    hindi_text = to_devanagari(clean_text)
    try:
        return gemini_tts_wav(hindi_text), "wav"
    except Exception as e:
        print("Gemini TTS fail hua, edge-tts use kar rahe hain:", e)
        return edge_tts_mp3_bytes(hindi_text), "mp3"

# ---------------------------------------------------------------
# VIDEO
# ---------------------------------------------------------------

PALETTE = [(18, 24, 56), (40, 18, 56), (14, 52, 60), (56, 28, 18), (22, 44, 28)]
ACCENT = (255, 196, 0)
VIDEO_DIR = "/tmp/videos"
video_jobs = {}

def ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()

def audio_duration(path):
    r = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise ValueError("Audio ki length nahi mili")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

def ascii_caption_text(text):
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"[^\x00-\x7F]+", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()

def split_chunks(text, max_chars):
    sentences = re.split(r"(?<=[.!?])\s+", text)
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
        while len(c) > max_chars * 1.6:
            cut = c.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            final.append(c[:cut].strip())
            c = c[cut:].strip()
        if c:
            final.append(c)
    return final

def wrap_lines(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines

def make_slide(path, chunk, title, idx, total, size, color):
    w, h = size
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    big = max(34, w // 14) if h > w else max(34, w // 24)
    small = max(22, w // 28) if h > w else max(22, w // 48)
    font = ImageFont.load_default(size=big)
    tfont = ImageFont.load_default(size=small)

    t = title[:40]
    tw = d.textlength(t, font=tfont)
    d.text(((w - tw) / 2, int(h * 0.06)), t, font=tfont, fill=ACCENT)
    d.rectangle([int(w * 0.1), int(h * 0.06) + small + 14, int(w * 0.9), int(h * 0.06) + small + 18], fill=ACCENT)

    lines = wrap_lines(d, chunk, font, int(w * 0.84))
    line_h = big + 14
    total_h = line_h * len(lines)
    y = (h - total_h) / 2
    for ln in lines:
        lw = d.textlength(ln, font=font)
        d.text(((w - lw) / 2, y), ln, font=font, fill=(255, 255, 255))
        y += line_h

    bar_y = h - int(h * 0.04)
    d.rectangle([0, bar_y, w, h], fill=(0, 0, 0))
    d.rectangle([0, bar_y, int(w * (idx + 1) / total), h], fill=ACCENT)
    img.save(path, "PNG")

def build_video(caption_text, title, audio_path, out_path, work_dir, vertical=True):
    os.makedirs(work_dir, exist_ok=True)
    size = (720, 1280) if vertical else (1280, 720)
    caption_text = ascii_caption_text(caption_text)
    if not caption_text:
        caption_text = title or "Book Summary"
    chunks = split_chunks(caption_text, 70 if vertical else 110)
    total_len = sum(len(c) for c in chunks)
    dur = audio_duration(audio_path)

    list_path = os.path.join(work_dir, "list.txt")
    last_img = None
    with open(list_path, "w") as f:
        for i, c in enumerate(chunks):
            img_path = os.path.join(work_dir, f"slide_{i:03d}.png")
            make_slide(img_path, c, title, i, len(chunks), size, PALETTE[i % len(PALETTE)])
            seg = max(1.0, dur * len(c) / total_len)
            f.write(f"file '{img_path}'\nduration {seg:.3f}\n")
            last_img = img_path
        f.write(f"file '{last_img}'\n")

    cmd = [
        ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", "12", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-movflags", "+faststart", out_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg fail: " + r.stderr[-400:])
    return out_path

def cleanup_old_videos():
    try:
        now = time.time()
        for name in os.listdir(VIDEO_DIR):
            p = os.path.join(VIDEO_DIR, name)
            if os.path.isfile(p) and now - os.path.getmtime(p) > 3600:
                os.remove(p)
    except Exception:
        pass

def run_video_job(job_id, script_text, title, vertical):
    job = video_jobs[job_id]
    work_dir = os.path.join(VIDEO_DIR, job_id + "_work")
    try:
        clean_text = extract_voiceover_text(script_text)
        if not clean_text:
            raise ValueError("Script se text nahi mila")

        job["msg"] = "Step 1/3: Voiceover ban raha hai (Hindi conversion + awaaz)..."
        audio_bytes, ext = make_voice(clean_text)

        os.makedirs(work_dir, exist_ok=True)
        audio_path = os.path.join(work_dir, "voice." + ext)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        job["msg"] = "Step 2/3: Slides aur video render ho raha hai..."
        out_path = os.path.join(VIDEO_DIR, job_id + ".mp4")
        build_video(clean_text, title, audio_path, out_path, work_dir, vertical)

        job["file"] = out_path
        job["status"] = "done"
        job["msg"] = "Video ready!"
    except Exception as e:
        print("Video job fail:", e)
        job["status"] = "error"
        job["msg"] = str(e)[:400]
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
        action_block = f"""
        <form method="POST" action="/dashboard/youtube/video" style="margin-top:15px;">
            <textarea name="script_text" style="display:none;">{escaped_script}</textarea>
            <input type="hidden" name="format" value="{video_format}">
            <input type="hidden" name="book_name" value="{escaped_book}">
            <button type="submit" style="background:#e67e22;">🎬 Video Banao (Voice + Captions)</button>
        </form>
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

    audio_bytes, ext = make_voice(clean_text)
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
    video_format = request.form.get("format", "short")
    book_name = request.form.get("book_name", "") or "Book Summary"
    title = ascii_caption_text(book_name) or "Book Summary"

    os.makedirs(VIDEO_DIR, exist_ok=True)
    cleanup_old_videos()

    job_id = uuid.uuid4().hex[:10]
    video_jobs[job_id] = {
        "status": "working",
        "msg": "Shuru ho raha hai...",
        "file": None,
        "start": time.time()
    }
    t = threading.Thread(
        target=run_video_job,
        args=(job_id, script_text, title, video_format == "short"),
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
    refresh = ""
    if job["status"] == "working":
        refresh = '<meta http-equiv="refresh" content="5">'
        body = f"""
        <h3>⏳ Video ban raha hai...</h3>
        <p>{html_lib.escape(job['msg'])}</p>
        <p>Time ho gaya: {elapsed} second</p>
        <p>Yeh page apne aap refresh hota rahega. Ise band mat karna, aur isme 2-3 minute lag sakte hain.</p>
        """
    elif job["status"] == "done":
        body = f"""
        <h3>✅ Video ready!</h3>
        <video controls playsinline style="width:100%;max-width:420px;border-radius:8px;">
            <source src="/dashboard/youtube/video/{job_id}/file" type="video/mp4">
        </video>
        <a class="dl" href="/dashboard/youtube/video/{job_id}/file?dl=1">⬇️ Video Download Karo</a>
        <p>Banane mein {elapsed} second lage.</p>
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
