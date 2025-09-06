import os
import requests
from bs4 import BeautifulSoup
from flask import Flask
from threading import Thread
from pymongo import MongoClient
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import pytz

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MONGO_URI = os.getenv("MONGO_URI")

# DB setup
client = MongoClient(MONGO_URI)
db = client["watch_tracker"]
collection = db["watches"]

# Flask keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive ✅"

# Telegram notify
def send_message(text: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text})
    except Exception as e:
        print(f"Error sending message: {e}")

# Convert UTC to India 12-hour format
def format_indian_time():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    return now.strftime("%I:%M %p %d-%b-%Y")

# Extract stock info from hmtwatches.store (stable UUID links)
def check_store_site(url: str):
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = soup.find("h1").get_text(strip=True)
        stock_btn = soup.find("button", {"type": "submit"})

        in_stock = stock_btn is not None and "Add to Cart" in stock_btn.get_text()
        return {"title": title, "in_stock": in_stock}
    except Exception as e:
        send_message(f"⚠️ Error checking product:\n{url}\nError: {e}")
        return None

# Main tracker
def tracker():
    watches = list(collection.find())
    for w in watches:
        result = check_store_site(w["url"])
        if result:
            status = "✅ In Stock" if result["in_stock"] else "❌ Out of Stock"
            send_message(
                f"⌚ {result['title']}\n"
                f"🔗 {w['url']}\n"
                f"📦 Status: {status}\n"
                f"🕒 Last checked: {format_indian_time()}"
            )

# Self-ping (to prevent Render sleep)
def self_ping():
    try:
        requests.get("https://your-app-name.onrender.com")
    except:
        pass

# Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(tracker, "interval", minutes=5)   # check every 5 min
scheduler.add_job(self_ping, "interval", minutes=10)  # ping every 10 min
scheduler.start()

# Run Flask in background
def run():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = Thread(target=run)
    t.start()

# Entry point
if __name__ == "__main__":
    keep_alive()
