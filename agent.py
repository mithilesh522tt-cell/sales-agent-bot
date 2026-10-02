import os
import re
import html as html_lib
import asyncio
import edge_tts
from io import BytesIO
from flask import Flask, request, send_file
import google.generativeai as genai
import psycopg2

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

# Book summary/explanation script generator
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

    voiceover_block = ""
    if script_result:
        voiceover_block = f"""
        <form method="POST" action="/dashboard/youtube/audio" style="margin-top:15px;">
            <textarea name="script_text" style="display:none;">{escaped_script}</textarea>
            <button type="submit" style="background:#8e44ad;">🔊 Voiceover Banao (MP3)</button>
        </form>
        """

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial; background: #f0f2f5; padding: 20px; }}
            a.back {{ color: #007bff; text-decoration: none; font-size: 16px; }}
            label {{ font-weight: bold; display: block; margin-top: 15px; }}
            input, select {{ width: 100%; padding: 10px; margin-top: 5px; box-sizing: border-box; font-size: 16px; }}
            button {{ margin-top: 20px; padding: 12px; background: #28a745; color: white; border: none; border-radius: 5px; width: 100%; font-size: 16px; }}
            .result {{ background: white; padding: 15px; margin-top: 20px; border-radius: 8px; white-space: pre-wrap; font-size: 14px; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <a class="back" href="/dashboard">&larr; Back</a>
        <h2>📚 Book Script Generator</h2>
        <form method="POST">
            <label>Book ka naam</label>
            <input type="text" name="book_name" value="{book_name}" placeholder="jaise: Atomic Habits" required>

            <label>Video format</label>
            <select name="format">
                <option value="short" {"selected" if video_format=="short" else ""}>Short (45-60 sec)</option>
                <option value="long" {"selected" if video_format=="long" else ""}>Long (7-10 min)</option>
            </select>

            <button type="submit">Script Generate Karo</button>
        </form>
        {"<div class='result'>" + script_result + "</div>" if script_result else ""}
        {voiceover_block}
    </body>
    </html>
    """
    return html

# Voiceover (MP3) banane wala route
@app.route("/dashboard/youtube/audio", methods=["POST"])
def youtube_audio():
    script_text = request.form.get("script_text", "")
    clean_text = extract_voiceover_text(script_text)

    if not clean_text:
        return "Voiceover ke liye text nahi mila", 400

    audio_buffer = BytesIO()

    async def generate_audio():
        communicate = edge_tts.Communicate(
            clean_text,
            voice="hi-IN-MadhurNeural",
            rate="+0%",
            pitch="-5Hz"
        )
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

    asyncio.run(generate_audio())
    audio_buffer.seek(0)

    return send_file(
        audio_buffer,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name="voiceover.mp3"
    )

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
