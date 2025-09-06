# app.py - HMT Stock Bot with MongoDB persistence
# Bot username: @hmt_tracker_bot

import os
import time
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from flask import Flask
import threading
from pymongo import MongoClient

# ---------- config ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN environment variable")

BASE_TELEGRAM = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0"}
BOT_USERNAME = "@hmt_tracker_bot"

MONGO_URI = os.environ.get("MONGO_URI")
MONGO_DB = os.environ.get("MONGO_DB", "hmtbot")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
users_col = db["users"]
state_col = db["state"]  # stores last update_id


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
    now_iso = datetime.now(timezone.utc).isoformat()
    changed = False

    for user in users_col.find():
        chat_id = str(user["_id"])
        interval = int(user.get("interval", 5))
        last_checked = user.get("last_checked")
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

        watches = user.get("watches", [])
        for w in watches:
            url = w.get("url")
            if not url:
                continue
            new_status = evaluate_stock(url)
            old_status = w.get("last_status", "unknown")

            if new_status == "in" and old_status != "in" and user.get("notify", True):
                title = page_title(url)
                msg = f"🚨 {title}\nAVAILABLE!\n{url}\nTracked by {BOT_USERNAME}"
                send_message(chat_id, msg)

            w["last_status"] = new_status
            w["last_checked"] = now_iso

        users_col.update_one({"_id": chat_id}, {"$set": {
            "watches": watches,
            "last_checked": now_iso
        }})
        changed = True

    return changed


# ---------- command handling ----------
def handle_command(chat_id, text):
    chat_id = str(chat_id)
    user = users_col.find_one({"_id": chat_id})
    if not user:
        user = {
            "_id": chat_id,
            "watches": [],
            "interval": 5,
            "notify": True,
            "last_checked": None
        }
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
        except Exception:
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
        except Exception:
            send_message(chat_id, "❌ Invalid index or error.")
        return

    if cmd == "/interval":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /interval <minutes>")
            return
        try:
            m = int(parts[1])
            user["interval"] = max(1, m)
            users_col.update_one({"_id": chat_id}, {"$set": {"interval": user["interval"]}})
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
            users_col.update_one({"_id": chat_id}, {"$set": {"notify": True}})
            send_message(chat_id, "🔔 Notifications ON")
        elif arg in ("off", "false", "0"):
            user["notify"] = False
            users_col.update_one({"_id": chat_id}, {"$set": {"notify": False}})
            send_message(chat_id, "🔕 Notifications OFF")
        else:
            send_message(chat_id, "Usage: /notify on|off")
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


# ---------- keep-alive endpoint ----------
keep_alive_app = Flask("keep_alive")

@keep_alive_app.route("/ping")
def ping():
    return "ok", 200

def run_keep_alive():
    keep_alive_app.run(host="0.0.0.0", port=8080)


threading.Thread(target=run_keep_alive, daemon=True).start()


# ---------- main loop ----------
if __name__ == "__main__":
    print(f"🤖 Bot {BOT_USERNAME} started with MongoDB persistence...")

    state = state_col.find_one({"_id": "global"}) or {"_id": "global", "last_update_id": 0}
    offset = state.get("last_update_id", 0)

    while True:
        # Poll Telegram messages
        updates = get_updates(offset + 1)
        for u in updates:
            offset = u["update_id"]
            state_col.update_one({"_id": "global"}, {"$set": {"last_update_id": offset}}, upsert=True)

            msg = u.get("message") or u.get("edited_message")
            if msg and "text" in msg:
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                try:
                    handle_command(chat_id, text)
                except Exception as e:
                    print("handle_command error:", e)

        # Check stock
        check_all_watches()

        # Self-ping every 5 minutes
        try:
            requests.get("https://kushagraonly.onrender.com/ping", timeout=5)
        except:
            pass

        time.sleep(5)
