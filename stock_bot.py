#!/usr/bin/env python3
"""
HMT Stock Bot
- Runs in scheduled GitHub Actions runs
- Reads/writes data.json (persistent storage)
- Reads Telegram messages (getUpdates), handles commands
- Checks product pages for “in stock” vs “out of stock”
- Sends Telegram messages when a product becomes AVAILABLE (state change)
"""

import os
import json
import time
import requests
import subprocess
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ---------------- CONFIG ----------------
DATA_FILE = "data.json"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
# default check interval if user didn't set one (minutes)
DEFAULT_INTERVAL_MIN = 5
# user-agent for requests
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/115.0 Safari/537.36"}
# ---------------- END CONFIG -------------

if not TELEGRAM_TOKEN:
    print("ERROR: TELEGRAM_TOKEN environment variable not set. Exiting.")
    raise SystemExit(1)


# ---------- Utilities for data persistence ----------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"last_update_id": 0, "users": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- Telegram helpers ----------
def send_message(chat_id, text):
    url = f"{TELEGRAM_API}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    return resp.ok


def get_updates(offset=None, timeout=10):
    url = f"{TELEGRAM_API}/getUpdates"
    params = {"timeout": timeout, "limit": 100}
    if offset:
        params["offset"] = offset
    resp = requests.get(url, params=params, timeout=timeout + 5)
    if resp.status_code == 200:
        return resp.json().get("result", [])
    return []


# ---------- Command handling ----------
def handle_command(chat_id, text, data):
    text = (text or "").strip()
    parts = text.split()
    if not parts:
        send_message(chat_id, "❌ Empty message.")
        return True

    cmd = parts[0].lower()
    user = data["users"].get(str(chat_id))

    # ensure user entry exists on first interaction
    if user is None:
        data["users"][str(chat_id)] = {
            "watches": [],
            "interval": DEFAULT_INTERVAL_MIN,
            "notify": True,
            "last_checked": None
        }
        user = data["users"][str(chat_id)]

    changed = False

    if cmd == "/start" or cmd == "/help":
        send_message(chat_id,
                     "👋 Welcome to HMT Stock Bot!\n\n"
                     "Commands:\n"
                     "/add <product_link> - Add a product to track\n"
                     "/list - Show tracked products\n"
                     "/remove <index> - Remove a product by its index\n"
                     "/update <index> <new_link> - Replace a product link\n"
                     "/interval <minutes> - Set check interval (minutes)\n"
                     "/notify on|off - Turn notifications on or off\n"
                     "/stats - Show stats for your account\n"
                     "/help - Show this help")
        return False

    if cmd == "/add":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /add <product_link>")
            return False
        link = parts[1].strip()
        if not is_valid_url(link):
            send_message(chat_id, "❌ That doesn't look like a valid URL.")
            return False
        user["watches"].append({
            "url": link,
            "last_status": "unknown",
            "last_checked": None
        })
        changed = True
        send_message(chat_id, f"✅ Added and will monitor: {link}")
        return changed

    if cmd == "/list":
        watches = user.get("watches", [])
        if not watches:
            send_message(chat_id, "📭 You are not tracking any products.")
            return False
        msg_lines = ["📋 You are tracking:"]
        for i, w in enumerate(watches, start=1):
            status = w.get("last_status", "unknown")
            last = w.get("last_checked") or "never"
            msg_lines.append(f"{i}. {w['url']} — status: {status} — last check: {last}")
        send_message(chat_id, "\n".join(msg_lines))
        return False

    if cmd == "/remove":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove <index>")
            return False
        try:
            idx = int(parts[1]) - 1
            removed = user["watches"].pop(idx)
            changed = True
            send_message(chat_id, f"🗑️ Removed: {removed['url']}")
        except Exception:
            send_message(chat_id, "❌ Invalid index.")
        return changed

    if cmd == "/update":
        if len(parts) < 3:
            send_message(chat_id, "Usage: /update <index> <new_link>")
            return False
        try:
            idx = int(parts[1]) - 1
            new_link = parts[2].strip()
            if not is_valid_url(new_link):
                send_message(chat_id, "❌ That doesn't look like a valid URL.")
                return False
            user["watches"][idx]["url"] = new_link
            user["watches"][idx]["last_status"] = "unknown"
            user["watches"][idx]["last_checked"] = None
            changed = True
            send_message(chat_id, f"🔄 Updated watch #{idx+1} -> {new_link}")
        except Exception:
            send_message(chat_id, "❌ Invalid index or error.")
        return changed

    if cmd == "/interval":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /interval <minutes>   (min 1)")
            return False
        try:
            m = int(parts[1])
            if m < 1:
                raise ValueError
            user["interval"] = m
            changed = True
            send_message(chat_id, f"⏱ Check interval set to {m} minute(s).")
        except Exception:
            send_message(chat_id, "❌ Invalid number.")
        return changed

    if cmd == "/notify":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /notify on|off")
            return False
        arg = parts[1].lower()
        if arg in ("on", "true", "1"):
            user["notify"] = True
            changed = True
            send_message(chat_id, "🔔 Notifications: ON")
        elif arg in ("off", "false", "0"):
            user["notify"] = False
            changed = True
            send_message(chat_id, "🔕 Notifications: OFF")
        else:
            send_message(chat_id, "Usage: /notify on|off")
        return changed

    if cmd == "/stats":
        wcount = len(user.get("watches", []))
        interval = user.get("interval", DEFAULT_INTERVAL_MIN)
        last_checked = user.get("last_checked") or "never"
        send_message(chat_id, f"📊 Tracked: {wcount} watches\nInterval: {interval} min\nLast checked: {last_checked}")
        return False

    send_message(chat_id, "❓ Unknown command. Send /help for instructions.")
    return False


def is_valid_url(u):
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and p.netloc != ""
    except Exception:
        return False


# ---------- Stock checking ----------
def evaluate_stock(url):
    """Return 'in', 'out', or 'unknown'."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return "unknown"
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=" ").lower()

        out_markers = ["out of stock", "sold out", "currently unavailable", "unavailable"]
        in_markers = ["add to cart", "add to bag", "add to basket", "buy now", "in stock"]

        if any(m in text for m in out_markers):
            return "out"
        if any(m in text for m in in_markers):
            return "in"
        # fallback: check presence of 'add to cart' button attribute or button text
        # But default to unknown to avoid false positives
        return "unknown"
    except Exception as e:
        print("Error fetching", url, "->", e)
        return "unknown"


# ---------- Git helper (commit only if data changed) ----------
def commit_and_push_if_needed(old_text):
    # check if file changed
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        new_text = f.read()
    if new_text == old_text:
        return False

    # configure git user for the commit
    try:
        subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "add", DATA_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "Update data.json (bot)"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Committed and pushed data.json changes.")
        return True
    except subprocess.CalledProcessError as e:
        print("Git commit/push failed:", e)
        return False


# ---------- Main run ----------
def main():
    data = load_data()
    old_data_text = json.dumps(data, indent=2, ensure_ascii=False)

    # 1) Process Telegram updates (commands)
    offset = data.get("last_update_id", 0) + 1
    updates = get_updates(offset=offset, timeout=5)
    max_update_id = data.get("last_update_id", 0)
    data_changed = False

    for u in updates:
        uid = u.get("update_id")
        if uid is None:
            continue
        if uid > max_update_id:
            max_update_id = uid

        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "")
        if not chat_id or not text:
            continue
        print(f"Processing message from {chat_id}: {text}")
        changed = handle_command(chat_id, text, data)
        if changed:
            data_changed = True

    # update last_update_id to max we processed
    if max_update_id and max_update_id > data.get("last_update_id", 0):
        data["last_update_id"] = max_update_id

    # 2) Stock checking per user (respect user's interval)
    now_ts = datetime.now(timezone.utc).isoformat()
    for chat_id, user in data.get("users", {}).items():
        try:
            interval = int(user.get("interval", DEFAULT_INTERVAL_MIN))
        except Exception:
            interval = DEFAULT_INTERVAL_MIN

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
            except Exception:
                should_check = True

        if not should_check:
            continue

        watches = user.get("watches", [])
        for w in watches:
            url = w.get("url")
            if not url:
                continue
            try:
                new_status = evaluate_stock(url)
            except Exception:
                new_status = "unknown"

            old_status = w.get("last_status", "unknown")

            # notify only on state change to 'in'
            if new_status == "in" and old_status != "in" and user.get("notify", True):
                title = url
                # try to extract page title
                try:
                    r = requests.get(url, headers=HEADERS, timeout=15)
                    soup = BeautifulSoup(r.text, "html.parser")
                    t = soup.title.string.strip() if soup.title and soup.title.string else None
                    if t:
                        title = t
                except Exception:
                    pass
                msg = f"🚨 {title}\nAVAILABLE!\n{url}"
                send_message(int(chat_id), msg)

            # update watch status and last checked time
            w["last_status"] = new_status
            w["last_checked"] = now_ts

        # update user's last_checked time
        user["last_checked"] = now_ts
        data_changed = True

    # 3) Save data and commit if needed
    save_data(data)
    commit_and_push_if_needed(old_data_text)
    print("Run complete.")


if __name__ == "__main__":
    main()
