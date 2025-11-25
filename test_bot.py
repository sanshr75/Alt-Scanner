# test_bot.py
import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # set this in your environment
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")      # set this in your environment

if not BOT_TOKEN or not CHAT_ID:
    print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")
    print("Set them, then run: python test_bot.py")
    exit(1)

text = (
    "✅ Test message from your scanner bot.\n\n"
    "This confirms your bot token and chat_id are valid."
)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text})
print(resp.status_code, resp.text)
