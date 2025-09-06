# app.py - HMT Stock Bot hosted on Render
# Bot username: @hmt_tracker_bot

import os
import json
import requests
import time
import threading
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from flask import Flask
from pymongo import MongoClient

# ---------- config ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

BASE_TELEGRAM = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}
BOT_USERNAME = "@hmt_tracker_bot"

# ---------- MongoDB setup ----------
client = MongoClient(MONGO_URI)
db = client["hmt_bot"]
users_col = db["users"]
meta_col = db["meta"]

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

# ---------- core logic ----------
def check_all_watches():
    now_iso = datetime.now(timezone.utc).isoformat()
    users = list(users_col.find({}))

    for u in users:
        chat_id = u["_id"]
        interval = int(u.get("interval", 5))
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

        watches = u.get("watches", [])
        for idx, w in enumerate(watches):
            url = w.get("url")
            if not url:
                continue
            new_status = evaluate_stock(url)
            old_status = w.get("last_status", "unknown")

            if new_status == "in" and old_status != "in" and u.get("notify", True):
                title = page_title(url)
                msg = f"🚨 {title}\nAVAILABLE!\n{url}\nTracked by {BOT_USERNAME}"
                send_message(chat_id, msg)

            watches[idx]["last_status"] = new_status
            watches[idx]["last_checked"] = now_iso

        users_col.update_one(
            {"_id": chat_id},
            {"$set": {"watches": watches, "last_checked": now_iso}},
            upsert=True,
        )

# ---------- command handling ----------
def handle_command(chat_id, text):
    user = users_col.find_one({"_id": chat_id})
    if not user:
        user = {"_id": chat_id, "watches": [], "interval": 5, "notify": True, "last_checked": None}
        users_col.insert_one(user)

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
        users_col.update_one({"_id": chat_id}, {"$set": {"watches": user["watches"]}})
        send_message(chat_id, f"✅ Added: {url}")
        return

    if cmd == "/list":
        watches = user.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 No watches tracked.")
            return
        lines = [f"{i}. {w['url']} — status: {w.get('last_status','unknown')}" for i, w in enumerate(watches, 1)]
        send_message(chat_id, "📋 Your watches:\n" + "\n".join(lines))
        return

    if cmd == "/remove":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove <index>")
            return
        try:
            idx = int(parts[1]) - 1
            removed = user["watches"].pop(idx)
            users_col.update_one({"_id": chat_id}, {"$set": {"watches": user["watches"]}})
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
            users_col.update_one({"_id": chat_id}, {"$set": {"watches": user["watches"]}})
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
            users_col.update_one({"_id": chat_id}, {"$set": {"interval": user['interval']}})
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
        users_col.update_one({"_id": chat_id}, {"$set": {"notify": user['notify']}})
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

# ---------- Flask keep-alive ----------
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "pong", 200

# ---------- main loop (background thread) ----------
def bot_loop():
    print(f"🤖 Bot {BOT_USERNAME} started (long polling mode)...")
    offset_doc = meta_col.find_one({"_id": "offset"}) or {"_id": "offset", "value": 0}
    offset = offset_doc["value"]

    while True:
        try:
            updates = get_updates(offset + 1)
            for u in updates:
                offset = u["update_id"]
                meta_col.update_one({"_id": "offset"}, {"$set": {"value": offset}}, upsert=True)
                msg = u.get("message") or u.get("edited_message")
                if msg and "text" in msg:
                    chat_id = msg["chat"]["id"]
                    text = msg["text"]
                    handle_command(chat_id, text)
        except Exception as e:
            print("bot_loop error:", e)

        try:
            check_all_watches()
        except Exception as e:
            print("check_all_watches error:", e)

        # Self-ping every loop to prevent Render sleep
        try:
            requests.get("https://kushagraonly.onrender.com/ping", timeout=5)
        except:
            pass

        time.sleep(5)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
