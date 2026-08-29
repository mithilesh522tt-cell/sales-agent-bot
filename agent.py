import os
from anthropic import Anthropic

# API key environment variable se aayegi (isko hum secure tarike se set karenge baad mein)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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

def chat_with_agent(user_message, conversation_history=[]):
    conversation_history.append({"role": "user", "content": user_message})
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=conversation_history
    )
    
    reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": reply})
    return reply

# Test karne ke liye - terminal mein chat karo
if __name__ == "__main__":
    print("Agent chalu ho gaya! (exit likhkar band karo)\n")
    history = []
    while True:
        user_input = input("Customer: ")
        if user_input.lower() == "exit":
            break
        reply = chat_with_agent(user_input, history)
        print(f"Agent: {reply}\n")
