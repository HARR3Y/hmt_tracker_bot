# app.py - HMT Stock Bot with MongoDB + IST 12-hour stats
# Bot username: @hmt_tracker_bot

import os
import json
import requests
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import threading
from flask import Flask
from pymongo import MongoClient
from urllib.parse import quote_plus

# ---------- config ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

BASE_TELEGRAM = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BOT_USERNAME = "@hmt_tracker_bot"

# ---------- MongoDB setup ----------
client = MongoClient(MONGO_URI)
db = client["hmt_bot"]
users_col = db["users"]

# ---------- telegram helpers ----------
def send_message(chat_id, text):
    url = f"{BASE_TELEGRAM}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        return resp.ok
    except Exception as e:
        print("send_message error:", e)
        return False

# ---------- stock detection ----------
HEADERS = {"User-Agent": "Mozilla/5.0"}

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

# ---------- core check logic ----------
def check_all_watches():
    now_utc = datetime.now(timezone.utc)
    changed = False
    for user_doc in users_col.find({}):
        chat_id = str(user_doc["_id"])
        interval = int(user_doc.get("interval", 5))
        last_checked = user_doc.get("last_checked")
        should_check = False

        if not last_checked:
            should_check = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_checked)
                elapsed = (now_utc - last_dt).total_seconds()
                if elapsed >= interval * 60:
                    should_check = True
            except Exception:
                should_check = True

        if not should_check:
            continue

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

            w["last_status"] = new_status
            w["last_checked"] = now_utc.isoformat()

        users_col.update_one({"_id": user_doc["_id"]},
                             {"$set": {"watches": watches, "last_checked": now_utc.isoformat()}})
        changed = True
    return changed

# ---------- command handling ----------
def handle_command(chat_id, text):
    chat_id = str(chat_id)
    user_doc = users_col.find_one({"_id": chat_id})
    if not user_doc:
        user_doc = {
            "_id": chat_id,
            "watches": [],
            "interval": 5,
            "notify": True,
            "last_checked": None
        }
        users_col.insert_one(user_doc)

    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower()

    # --- help/start ---
    if cmd in ("/start", "/help"):
        send_message(chat_id, f"👋 Welcome to HMT Stock Bot {BOT_USERNAME}!\nCommands:\n/add <link>\n/list\n/remove <index>\n/update <index> <new_link>\n/interval <minutes>\n/notify on|off\n/stats")
        return

    # --- add ---
    if cmd == "/add":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /add <product_link>")
            return
        url = parts[1].strip()
        users_col.update_one({"_id": chat_id},
                             {"$push": {"watches": {"url": url, "last_status": "unknown", "last_checked": None}}})
        send_message(chat_id, f"✅ Added: {url}")
        return

    # --- list ---
    if cmd == "/list":
        watches = user_doc.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 No watches tracked.")
            return
        lines = [f"{i}. {w['url']} — status: {w.get('last_status','unknown')}" for i, w in enumerate(watches, 1)]
        send_message(chat_id, "📋 Your watches:\n" + "\n".join(lines))
        return

    # --- remove ---
    if cmd == "/remove":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove <index>")
            return
        try:
            idx = int(parts[1]) - 1
            watches = user_doc.get("watches", [])
            removed = watches.pop(idx)
            users_col.update_one({"_id": chat_id}, {"$set": {"watches": watches}})
            send_message(chat_id, f"🗑️ Removed: {removed['url']}")
        except:
            send_message(chat_id, "❌ Invalid index.")
        return

    # --- update ---
    if cmd == "/update":
        if len(parts) < 3:
            send_message(chat_id, "Usage: /update <index> <new_link>")
            return
        try:
            idx = int(parts[1]) - 1
            new_link = parts[2].strip()
            watches = user_doc.get("watches", [])
            watches[idx]["url"] = new_link
            watches[idx]["last_status"] = "unknown"
            watches[idx]["last_checked"] = None
            users_col.update_one({"_id": chat_id}, {"$set": {"watches": watches}})
            send_message(chat_id, f"🔄 Updated #{idx+1} -> {new_link}")
        except:
            send_message(chat_id, "❌ Invalid index or error.")
        return

    # --- interval ---
    if cmd == "/interval":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /interval <minutes>")
            return
        try:
            m = int(parts[1])
            users_col.update_one({"_id": chat_id}, {"$set": {"interval": max(1, m)}})
            send_message(chat_id, f"⏱ Interval set to {max(1,m)} minutes.")
        except:
            send_message(chat_id, "❌ Invalid number.")
        return

    # --- notify ---
    if cmd == "/notify":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /notify on|off")
            return
        arg = parts[1].lower()
        if arg in ("on", "true", "1"):
            users_col.update_one({"_id": chat_id}, {"$set": {"notify": True}})
            send_message(chat_id, "🔔 Notifications ON")
        elif arg in ("off", "false", "0"):
            users_col.update_one({"_id": chat_id}, {"$set": {"notify": False}})
            send_message(chat_id, "🔕 Notifications OFF")
        else:
            send_message(chat_id, "Usage: /notify on|off")
        return

    # --- stats ---
    if cmd == "/stats":
        watches = user_doc.get("watches", [])
        wcount = len(watches)
        last_checked_iso = user_doc.get("last_checked")
        if last_checked_iso:
            try:
                dt = datetime.fromisoformat(last_checked_iso)
                ist_dt = dt + timedelta(hours=5, minutes=30)
                last_checked_str = ist_dt.strftime("%d-%m-%Y %I:%M:%S %p")
            except:
                last_checked_str = last_checked_iso
        else:
            last_checked_str = "Never"

        send_message(chat_id, f"📊 Tracked: {wcount} watches\nInterval: {user_doc.get('interval',5)} min\nLast checked: {last_checked_str}")
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
app = Flask("keep_alive")

@app.route("/ping")
def ping():
    return "ok", 200

def run_flask():
    app.run(host="0.0.0.0", port=10000)

threading.Thread(target=run_flask, daemon=True).start()

# ---------- main loop ----------
if __name__ == "__main__":
    print(f"🤖 Bot {BOT_USERNAME} started (long polling mode)...")
    while True:
        # poll Telegram
        for u in get_updates():
            offset = u["update_id"]
            msg = u.get("message") or u.get("edited_message")
            if msg and "text" in msg:
                handle_command(msg["chat"]["id"], msg["text"])

        # check stock
        check_all_watches()

        # sleep 5 seconds to avoid tight loop
        time.sleep(5)
