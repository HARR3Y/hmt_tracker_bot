# app.py - Telegram Bot with Flask self-ping for Render
# Bot username: @hmt_tracker_bot

import os
import requests
import time
import threading
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from flask import Flask
from pymongo import MongoClient
from urllib.parse import quote_plus

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
BOT_USERNAME = "@hmt_tracker_bot"

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

BASE_TELEGRAM = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------- MONGODB ----------
client = MongoClient(MONGO_URI)
db = client.hmt_tracker_bot
users_col = db.users

# ---------- FLASK APP ----------
app = Flask("keep_alive")  # Must be named `app` for Gunicorn

@app.route("/ping")
def ping():
    return "ok", 200

# ---------- TELEGRAM HELPERS ----------
def send_message(chat_id, text):
    url = f"{BASE_TELEGRAM}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        return resp.ok
    except Exception as e:
        print("send_message error:", e)
        return False

# ---------- STOCK CHECK ----------
def evaluate_stock(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return "unknown"
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ").lower()
        out_markers = ["out of stock", "sold out", "currently unavailable", "unavailable"]
        in_markers = ["add to cart", "add to bag", "buy now", "in stock", "add to basket"]
        if any(m in text for m in out_markers):
            return "out"
        if any(m in text for m in in_markers):
            return "in"
        return "unknown"
    except Exception as e:
        print("evaluate error for", url, e)
        return "unknown"

def page_title(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
    except:
        pass
    return url

def format_ist(dt_utc):
    ist = dt_utc + timedelta(hours=5, minutes=30)
    return ist.strftime("%d-%m-%Y %I:%M:%S %p")

# ---------- BOT LOGIC ----------
def check_all_watches():
    now_utc = datetime.now(timezone.utc)
    for user_doc in users_col.find({}):
        chat_id = str(user_doc["_id"])
        watches = user_doc.get("watches", [])
        for w in watches:
            url = w.get("url")
            if not url:
                continue
            new_status = evaluate_stock(url)
            old_status = w.get("last_status", "unknown")

            if new_status == "in" and old_status != "in" and user_doc.get("notify", True):
                title = page_title(url)
                msg = f"🚨 {title}\nAVAILABLE!\n{url}\nTracked by {BOT_USERNAME}"
                send_message(chat_id, msg)

            if new_status == "unknown" and user_doc.get("notify", True):
                msg = f"⚠️ Could not track the watch!\nCheck: {url}\nPossible site changes."
                send_message(chat_id, msg)

            w["last_status"] = new_status
            w["last_checked"] = now_utc.isoformat()

        users_col.update_one(
            {"_id": user_doc["_id"]},
            {"$set": {"watches": watches, "last_checked": now_utc.isoformat()}}
        )

def get_updates(offset=None):
    try:
        url = f"{BASE_TELEGRAM}/getUpdates"
        params = {"timeout": 30, "offset": offset}
        r = requests.get(url, params=params, timeout=35)
        return r.json().get("result", [])
    except Exception as e:
        print("get_updates error:", e)
        return []

def handle_command(chat_id, text):
    chat_id = str(chat_id)
    user_doc = users_col.find_one({"_id": chat_id})
    if not user_doc:
        user_doc = {"_id": chat_id, "watches": [], "interval": 5, "notify": True, "last_checked": None}
        users_col.insert_one(user_doc)

    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd in ("/start", "/help"):
        send_message(chat_id, f"👋 Welcome to HMT Stock Bot {BOT_USERNAME}!\nCommands:\n/add <link>\n/list\n/remove <index>\n/update <index> <new_link>\n/interval <minutes>\n/notify on|off\n/stats")
        return

    if cmd == "/add" and len(parts) > 1:
        url = parts[1].strip()
        user_doc["watches"].append({"url": url, "last_status": "unknown", "last_checked": None})
        users_col.update_one({"_id": chat_id}, {"$set": {"watches": user_doc["watches"]}})
        send_message(chat_id, f"✅ Added: {url}")
        return

    if cmd == "/list":
        watches = user_doc.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 No watches tracked.")
            return
        lines = []
        for i, w in enumerate(watches, 1):
            lc = w.get("last_checked")
            if lc:
                lc = format_ist(datetime.fromisoformat(lc))
            else:
                lc = "Never"
            lines.append(f"{i}. {w['url']} — status: {w.get('last_status','unknown')} — Last checked: {lc}")
        send_message(chat_id, "📋 Your watches:\n" + "\n".join(lines))
        return

    if cmd == "/stats":
        wcount = len(user_doc.get("watches", []))
        last_checked = user_doc.get("last_checked")
        if last_checked:
            last_checked = format_ist(datetime.fromisoformat(last_checked))
        else:
            last_checked = "Never"
        send_message(chat_id, f"📊 Tracked: {wcount} watches\nInterval: {user_doc.get('interval',5)} min\nLast checked: {last_checked}")
        return

# ---------- BACKGROUND THREAD ----------
def bot_loop():
    offset = None
    while True:
        updates = get_updates(offset)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message")
            if msg and "text" in msg:
                handle_command(msg["chat"]["id"], msg["text"])
        check_all_watches()
        time.sleep(5)

threading.Thread(target=bot_loop, daemon=True).start()

# ---------- RUN FLASK ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
