import os
import threading
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
from flask import Flask
from pymongo import MongoClient
import time

# -----------------------------
# Config
# -----------------------------
BOT_USERNAME = "kushagraonly"  # not strictly needed for polling, but kept for clarity
TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client["hmt_tracker"]
users_col = db["users"]

# Flask app for self-ping
app = Flask(__name__)


@app.route("/")
def home():
    return "✅ HMT Tracker Bot is running!"


@app.route("/ping")
def ping():
    return "pong"


# -----------------------------
# Helpers
# -----------------------------
def send_message(chat_id, text):
    url = f"{TELEGRAM_API}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"⚠️ Error sending message: {e}")


def get_status(url):
    """Fetch stock status from hmtwatches.store"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return "error"

        soup = BeautifulSoup(resp.text, "html.parser")

        # Example: look for "Add to Cart" button or "Out of Stock" text
        if soup.find(string=lambda t: "Out of stock" in t or "OUT OF STOCK" in t):
            return "out"
        elif soup.find(string=lambda t: "Add to cart" in t or "BUY NOW" in t):
            return "in"
        else:
            return "unknown"
    except Exception as e:
        print(f"⚠️ Error fetching {url}: {e}")
        return "error"


def format_time(dt):
    """Convert UTC datetime to IST in 12-hour format"""
    ist = pytz.timezone("Asia/Kolkata")
    local_time = dt.astimezone(ist)
    return local_time.strftime("%I:%M %p %d-%b-%Y")


# -----------------------------
# Bot Logic
# -----------------------------
def handle_update(update):
    if "message" not in update:
        return

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()

    user = users_col.find_one({"chat_id": chat_id})
    if not user:
        user = {"chat_id": chat_id, "watches": [], "interval": 5}
        users_col.insert_one(user)

    if text == "/start":
        send_message(chat_id, "👋 Welcome to HMT Tracker Bot!\nSend me a product link from hmtwatches.store to track stock.")

    elif text.startswith("http"):
        if ".store" not in text:
            send_message(chat_id, "❌ Only links from hmtwatches.store are supported.")
            return
        watches = user.get("watches", [])
        watches.append({"url": text, "last_status": "unknown"})
        users_col.update_one({"chat_id": chat_id}, {"$set": {"watches": watches}})
        send_message(chat_id, f"✅ Added watch to tracking:\n{text}")

    elif text == "/status":
        watches = user.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 You are not tracking any watches.")
        else:
            lines = []
            for w in watches:
                lines.append(f"🔗 {w['url']}\nStatus: {w['last_status']}")
            now = datetime.utcnow()
            lines.append(f"\n⏱ Last checked: {format_time(now)}")
            send_message(chat_id, "\n\n".join(lines))

    else:
        send_message(chat_id, "❓ Unknown command. Use /start or send me a product link.")


def poll_updates():
    print("🤖 Bot polling started...")
    offset = None
    while True:
        try:
            resp = requests.get(f"{TELEGRAM_API}/getUpdates", params={"timeout": 30, "offset": offset})
            data = resp.json()
            if "result" in data:
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    handle_update(update)
        except Exception as e:
            print(f"⚠️ Polling error: {e}")
        time.sleep(1)


def check_watches():
    print("🔍 Checking watches...")
    for user in users_col.find():
        chat_id = user["chat_id"]
        for watch in user.get("watches", []):
            url = watch["url"]
            status = get_status(url)
            if status == "error":
                send_message(chat_id, f"⚠️ Problem fetching {url}. The product page may have changed.")
            elif status != watch["last_status"]:
                if status == "in":
                    send_message(chat_id, f"✅ In Stock!\n{url}")
                elif status == "out":
                    send_message(chat_id, f"❌ Out of Stock!\n{url}")
                users_col.update_one(
                    {"chat_id": chat_id, "watches.url": url},
                    {"$set": {"watches.$.last_status": status}},
                )


def schedule_checker():
    while True:
        check_watches()
        time.sleep(300)  # every 5 minutes


# -----------------------------
# Start Both Bot + Flask
# -----------------------------
def run_all():
    # Start Flask in thread
    def run_flask():
        app.run(host="0.0.0.0", port=8080)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start scheduler in thread
    scheduler_thread = threading.Thread(target=schedule_checker)
    scheduler_thread.daemon = True
    scheduler_thread.start()

    # Run bot polling in main thread
    poll_updates()


if __name__ == "__main__":
    run_all()
