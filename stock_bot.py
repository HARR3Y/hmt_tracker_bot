import os
import json
import requests
from bs4 import BeautifulSoup

DATA_FILE = "data.json"
TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"

# -------------------- Data helpers --------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"last_update_id": 0, "users": {}}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# -------------------- Telegram helpers --------------------
def send_message(chat_id, text):
    url = BASE_URL + "sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print(f"Failed to send message: {e}")

def get_updates(offset=None):
    url = BASE_URL + "getUpdates"
    params = {"timeout": 5, "offset": offset}
    try:
        resp = requests.get(url, params=params, timeout=10)
        return resp.json().get("result", [])
    except Exception as e:
        print(f"Failed to fetch updates: {e}")
        return []

# -------------------- Stock check --------------------
def is_in_stock(url):
    try:
        resp = requests.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        # check for "Add to cart" or similar button
        button = soup.find("button", string=lambda t: t and "add" in t.lower())
        return button is not None
    except Exception as e:
        print(f"Error checking stock for {url}: {e}")
        return False

def check_all_watches(data):
    for user_id, info in data["users"].items():
        watches = info.get("watches", [])
        for w in watches:
            url = w["url"]
            in_stock = is_in_stock(url)
            if in_stock and not w.get("notified", False):
                send_message(user_id, f"✅ In stock: {url}")
                w["notified"] = True
            elif not in_stock:
                w["notified"] = False

# -------------------- Command handling --------------------
def handle_command(chat_id, text, data):
    user = data["users"].setdefault(str(chat_id), {"watches": []})
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd == "/start":
        send_message(chat_id, "👋 Welcome! Use /add <link> to track a watch.")
    elif cmd == "/add":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /add <product link>")
        else:
            url = parts[1].strip()
            user["watches"].append({"url": url, "notified": False})
            send_message(chat_id, f"Added watch: {url}")
    elif cmd == "/remove":
        if len(parts) < 2:
            send_message(chat_id, "Usage: /remove <index>")
        else:
            try:
                idx = int(parts[1].strip()) - 1
                removed = user["watches"].pop(idx)
                send_message(chat_id, f"Removed: {removed['url']}")
            except Exception:
                send_message(chat_id, "Invalid index.")
    elif cmd == "/list":
        if not user["watches"]:
            send_message(chat_id, "No watches being tracked.")
        else:
            msg = "\n".join([f"{i+1}. {w['url']}" for i, w in enumerate(user["watches"])])
            send_message(chat_id, "Your watches:\n" + msg)
    elif cmd == "/update":
        user["watches"] = []
        send_message(chat_id, "Cleared all watches. Use /add to add new ones.")
    else:
        send_message(chat_id, "Unknown command.")

# -------------------- Main --------------------
def main():
    data = load_data()
    offset = data.get("last_update_id", 0)

    updates = get_updates(offset=offset + 1)
    for update in updates:
        data["last_update_id"] = update["update_id"]
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"]["text"]
            print(f"Message from {chat_id}: {text}")
            handle_command(chat_id, text, data)

    # Check stock for all users
    check_all_watches(data)

    # Save state
    save_data(data)

if __name__ == "__main__":
    main()
