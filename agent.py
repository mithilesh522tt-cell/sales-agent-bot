import os
from flask import Flask, request
import google.generativeai as genai

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

# Har customer ki chat history yaha store hogi (temporary - memory mein)
# Key: customer ka phone number, Value: uski conversation history
customer_conversations = {}

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
    sender_number = request.form.get("From", "")  # customer ka WhatsApp number
    
    # Agar is number ki purani history nahi hai, nayi shuru karo
    if sender_number not in customer_conversations:
        customer_conversations[sender_number] = model.start_chat(history=[])
    
    chat_session = customer_conversations[sender_number]
    response = chat_session.send_message(incoming_message)
    reply_text = response.text
    
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{reply_text}</Message>
</Response>"""
    return twiml_response, 200, {"Content-Type": "text/xml"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
