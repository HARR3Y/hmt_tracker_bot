# app.py - HMT Stock Bot with MongoDB + Render keep-alive
# Bot username: @hmt_tracker_bot

import os
import requests
import time
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from flask import Flask
import threading
from pymongo import MongoClient

# ---------- config ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
BOT_USERNAME = "@hmt_tracker_bot"

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

BASE_TELEGRAM = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------- MongoDB setup ----------
client = MongoClient(MONGO_URI)
db = client["hmt_bot"]
users_collection = db["users"]
state_collection = db["state"]  # for last_update_id

# ---------- helpers ----------
def send_message(chat_id, text):
    url = f"{BASE_TELEGRAM}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print("send_message error:", e)

def evaluate_stock(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return "unknown"
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ").lower()
        if any(m in text for m in ["out of stock", "sold out", "currently unavailable", "unavailable"]):
            return "out"
        if any(m in text for m in ["add to cart", "add to bag", "buy now", "in stock", "add to basket"]):
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

# ---------- core check ----------
def check_all_watches():
    now_iso = datetime.now(timezone.utc).isoformat()
    users = users_collection.find({})
    for u in users:
        interval = u.get("interval", 5)
        last_checked = u.get("last_checked")
        should_check = False
        if not last_checked:
            should_check = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_checked)
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed >= interval * 60:
                    should_check = True
            except:
                should_check = True

        if not should_check:
            continue

        updated_watches = []
        for w in u.get("watches", []):
            url = w.get("url")
            if not url:
                continue
            new_status = evaluate_stock(url)
            old_status = w.get("last_status", "unknown")

            if new_status == "in" and old_status != "in" and u.get("notify", True):
                title = page_title(url)
                msg = f"🚨 {title}\nAVAILABLE!\n{url}\nTracked by {BOT_USERNAME}"
                send_message(u["_id"], msg)

            w["last_status"] = new_status
            w["last_checked"] = now_iso
            updated_watches.append(w)

        users_collection.update_one(
            {"_id": u["_id"]},
            {"$set": {"watches": updated_watches, "last_checked": now_iso}}
        )

# ---------- commands ----------
def handle_command(chat_id, text):
    chat_id = str(chat_id)
    user = users_collection.find_one({"_id": chat_id})
    if not user:
        user = {"_id": chat_id, "watches": [], "interval": 5, "notify": True, "last_checked": None}
        users_collection.insert_one(user)

    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd in ("/start", "/help"):
        send_message(chat_id, f"👋 Welcome to HMT Stock Bot {BOT_USERNAME}!\nCommands:\n/add <link>\n/list\n/remove <index>\n/update <index> <new_link>\n/interval <minutes>\n/notify on|off\n/stats")
        return

    if cmd == "/add":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /add <product_link>")
            return
        url = parts[1].strip()
        user["watches"].append({"url": url, "last_status": "unknown", "last_checked": None})
        users_collection.update_one({"_id": chat_id}, {"$set": user})
        send_message(chat_id, f"✅ Added: {url}")
        return

    if cmd == "/list":
        watches = user.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 No watches tracked.")
            return
        lines = [f"{i}. {w['url']} — {w.get('last_status','unknown')}" for i, w in enumerate(watches, 1)]
        send_message(chat_id, "📋 Your watches:\n" + "\n".join(lines))
        return

    if cmd == "/remove":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove <index>")
            return
        try:
            idx = int(parts[1]) - 1
            removed = user["watches"].pop(idx)
            users_collection.update_one({"_id": chat_id}, {"$set": user})
            send_message(chat_id, f"🗑️ Removed: {removed['url']}")
        except:
            send_message(chat_id, "❌ Invalid index.")
        return

    if cmd == "/update":
        if len(parts) < 3:
            send_message(chat_id, "Usage: /update <index> <new_link>")
            return
        try:
            idx = int(parts[1]) - 1
            new_link = parts[2].strip()
            user["watches"][idx]["url"] = new_link
            user["watches"][idx]["last_status"] = "unknown"
            user["watches"][idx]["last_checked"] = None
            users_collection.update_one({"_id": chat_id}, {"$set": user})
            send_message(chat_id, f"🔄 Updated #{idx+1} -> {new_link}")
        except:
            send_message(chat_id, "❌ Invalid index or error.")
        return

    if cmd == "/interval":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /interval <minutes>")
            return
        try:
            m = int(parts[1])
            user["interval"] = max(1, m)
            users_collection.update_one({"_id": chat_id}, {"$set": user})
            send_message(chat_id, f"⏱ Interval set to {user['interval']} minutes.")
        except:
            send_message(chat_id, "❌ Invalid number.")
        return

    if cmd == "/notify":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /notify on|off")
            return
        arg = parts[1].lower()
        if arg in ("on", "true", "1"):
            user["notify"] = True
            send_message(chat_id, "🔔 Notifications ON")
        elif arg in ("off", "false", "0"):
            user["notify"] = False
            send_message(chat_id, "🔕 Notifications OFF")
        else:
            send_message(chat_id, "Usage: /notify on|off")
        users_collection.update_one({"_id": chat_id}, {"$set": user})
        return

    if cmd == "/stats":
        wcount = len(user.get("watches", []))
        send_message(chat_id, f"📊 Tracked: {wcount} watches\nInterval: {user.get('interval',5)} min\nLast checked: {user.get('last_checked')}")
        return

    send_message(chat_id, "❓ Unknown command. Send /help")

# ---------- Telegram polling ----------
def get_updates(offset=None):
    url = f"{BASE_TELEGRAM}/getUpdates"
    params = {"timeout": 30, "offset": offset}
    try:
        r = requests.get(url, params=params, timeout=35)
        return r.json().get("result", [])
    except Exception as e:
        print("get_updates error:", e)
        return []

# ---------- keep-alive ----------
keep_alive_app = Flask("keep_alive")

@keep_alive_app.route("/ping")
def ping():
    return "ok", 200

def run_keep_alive():
    port = int(os.environ.get("PORT", 8080))
    keep_alive_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_keep_alive, daemon=True).start()

# ---------- main loop ----------
if __name__ == "__main__":
    print(f"🤖 Bot {BOT_USERNAME} started...")
    offset = 0
    while True:
        updates = get_updates(offset + 1)
        for u in updates:
            offset = u["update_id"]
            msg = u.get("message") or u.get("edited_message")
            if msg and "text" in msg:
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                handle_command(chat_id, text)

        check_all_watches()
        try:
            requests.get(f"https://{os.environ.get('RENDER_EXTERNAL_URL')}/ping", timeout=5)
        except:
            pass
        time.sleep(5)
