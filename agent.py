import os
import google.generativeai as genai

# API key environment variable se aayegi
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Business ka data - abhi ke liye simple dictionary mein (baad mein file/database se aayega)
business_info = """
Business Name: Sharma Saloon
Timing: 9 AM - 8 PM (Monday closed)
Services: Haircut - Rs 150, Beard - Rs 80, Hair Color - Rs 500
Address: Main Market, Sector 12
"""

# System prompt - yeh agent ka "personality" aur instructions define karta hai
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

def chat_with_agent(chat_session, user_message):
    response = chat_session.send_message(user_message)
    return response.text

# Test karne ke liye - terminal mein chat karo
if __name__ == "__main__":
    print("Agent chalu ho gaya! (exit likhkar band karo)\n")
    chat_session = model.start_chat(history=[])
    while True:
        user_input = input("Customer: ")
        if user_input.lower() == "exit":
            break
        reply = chat_with_agent(chat_session, user_input)
        print(f"Agent: {reply}\n")
