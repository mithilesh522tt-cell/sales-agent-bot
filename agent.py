import os
from flask import Flask, request, jsonify
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
    model_name="gemini-2.0-flash",
    system_instruction=system_prompt
)

# Home page - test karne ke liye, browser mein khul jayega
@app.route("/")
def home():
    return "Agent chalu hai! /chat?message=haircut ka price kya hai use karke test karo"

# Chat endpoint - yahan se customer ka message bhejenge
@app.route("/chat")
def chat():
    user_message = request.args.get("message", "")
    if not user_message:
        return jsonify({"error": "message parameter chahiye"})
    
    chat_session = model.start_chat(history=[])
    response = chat_session.send_message(user_message)
    return jsonify({"reply": response.text})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
