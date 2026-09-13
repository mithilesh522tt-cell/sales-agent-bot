import os
from flask import Flask, request
import google.generativeai as genai
import psycopg2
from datetime import datetime

app = Flask(__name__)

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

business_info = """
Business Name: Sharma Saloon
Timing: 9 AM - 8 PM (Monday closed)
Services: Haircut - Rs 150, Beard - Rs 80, Hair Color - Rs 500
Address: Main Market, Sector 12
"""

system_prompt = f"""Tum ek friendly sales assistant ho jo customers ke sawaalon ka jawab deta hai.
Business ki details neeche di hain, isi ke aadhar par jawab do:

{business_info}

Rules:
- Hamesha polite aur helpful raho
- Agar customer price poochta hai, clearly bata do
- Booking ke liye poocho ki kaunsa time convenient hai
- Agar sawaal business se related nahi hai, politely bolo ki sirf business info mein help kar sakte ho
"""

model = genai.GenerativeModel(
    model_name="gemini-3.6-flash",
    system_instruction=system_prompt
)

customer_conversations = {}

# Database se connect karne ka function
def get_db_connection():
    conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
    return conn

# Database mein table banane ka function (agar pehle se nahi hai)
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
    conn.commit()
    cur.close()
    conn.close()

# Message ko database mein save karne ka function
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

@app.route("/")
def home():
    return "Agent chalu hai!"

@app.route("/chat")
def chat():
    user_message = request.args.get("message", "")
    if not user_message:
        return {"error": "message parameter chahiye"}
    chat_session = model.start_chat(history=[])
    response = chat_session.send_message(user_message)
    return {"reply": response.text}

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_message = request.form.get("Body", "")
    sender_number = request.form.get("From", "")

    if sender_number not in customer_conversations:
        customer_conversations[sender_number] = model.start_chat(history=[])

    chat_session = customer_conversations[sender_number]
    response = chat_session.send_message(incoming_message)
    reply_text = response.text

    # Customer ka message aur agent ka reply dono save karo
    save_message(sender_number, "customer", incoming_message)
    save_message(sender_number, "agent", reply_text)

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{reply_text}</Message>
</Response>"""
    return twiml_response, 200, {"Content-Type": "text/xml"}

# Dashboard ke liye - saari conversations dikhane wala route
@app.route("/dashboard")
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT customer_number, message_from, message_text, timestamp FROM messages ORDER BY timestamp DESC LIMIT 100")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>Sales Agent Dashboard</h2><table border='1' cellpadding='8'>"
    html += "<tr><th>Customer</th><th>From</th><th>Message</th><th>Time</th></tr>"
    for row in rows:
        html += f"<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td></tr>"
    html += "</table>"
    return html

# App start hote hi database table bana do
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
