import os
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from pymongo import MongoClient
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ---------------- CONFIG ---------------- #
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError("Missing BOT_TOKEN or MONGO_URI")

# Mongo setup
client = MongoClient(MONGO_URI)
db = client["hmt_tracker"]
users_col = db["users"]

# Flask app for keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive", 200

# Logging
logging.basicConfig(level=logging.INFO)

# ---------------- HELPERS ---------------- #
IST = pytz.timezone("Asia/Kolkata")

def now_ist():
    return datetime.now(IST).strftime("%I:%M:%S %p %d-%m-%Y")

def fetch_status(url: str) -> str:
    try:
        if ".in" in url:
            return "❌ Skipped (.in not supported)"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        if ".store" in url:
            button = soup.select_one("button.add-to-cart")
            if not button:
                return "❌ Page structure changed"
            return "✅ In Stock" if button.text.strip().lower() == "add to cart" else "❌ Out of Stock"

        return "❌ Unknown domain"
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return f"❌ Error: {e}"

def get_user(chat_id):
    user = users_col.find_one({"chat_id": chat_id})
    if not user:
        user = {
            "chat_id": chat_id,
            "links": [],
            "interval": 5,
            "notify": True,
            "last_checked": None
        }
        users_col.insert_one(user)
    return user

def update_user(chat_id, data):
    users_col.update_one({"chat_id": chat_id}, {"$set": data})

# ---------------- COMMANDS ---------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Welcome to HMT Stock Bot!\n\n"
        "Commands:\n"
        "/add <link>\n"
        "/list\n"
        "/remove <index>\n"
        "/update <index> <new_link>\n"
        "/interval <minutes>\n"
        "/notify on|off\n"
        "/stats"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not context.args:
        return await update.message.reply_text("❌ Usage: /add <product_link>")
    url = context.args[0]
    if ".in" in url:
        return await update.message.reply_text("❌ .in links are not supported. Use .store")
    user["links"].append(url)
    update_user(chat_id, {"links": user["links"]})
    await update.message.reply_text(f"✅ Added: {url}")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not user["links"]:
        return await update.message.reply_text("No links added yet.")
    text = "\n".join([f"{i+1}. {l}" for i, l in enumerate(user["links"])])
    await update.message.reply_text("🔗 Tracked Links:\n" + text)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("❌ Usage: /remove <index>")
    idx = int(context.args[0]) - 1
    if 0 <= idx < len(user["links"]):
        removed = user["links"].pop(idx)
        update_user(chat_id, {"links": user["links"]})
        await update.message.reply_text(f"🗑 Removed: {removed}")
    else:
        await update.message.reply_text("❌ Invalid index")

async def update_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if len(context.args) < 2 or not context.args[0].isdigit():
        return await update.message.reply_text("❌ Usage: /update <index> <new_link>")
    idx = int(context.args[0]) - 1
    new_link = context.args[1]
    if ".in" in new_link:
        return await update.message.reply_text("❌ .in links are not supported. Use .store")
    if 0 <= idx < len(user["links"]):
        user["links"][idx] = new_link
        update_user(chat_id, {"links": user["links"]})
        await update.message.reply_text(f"🔄 Updated index {idx+1}")
    else:
        await update.message.reply_text("❌ Invalid index")

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("❌ Usage: /interval <minutes>")
    minutes = int(context.args[0])
    update_user(chat_id, {"interval": minutes})
    await update.message.reply_text(f"⏱ Interval set to {minutes} minutes")

async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or context.args[0].lower() not in ["on", "off"]:
        return await update.message.reply_text("❌ Usage: /notify on|off")
    state = context.args[0].lower() == "on"
    update_user(chat_id, {"notify": state})
    await update.message.reply_text("🔔 Notifications " + ("enabled" if state else "disabled"))

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    text = (
        f"📊 Tracked: {len(user['links'])} watches\n"
        f"Interval: {user['interval']} min\n"
        f"Last checked: {user['last_checked'] or 'Never'}"
    )
    await update.message.reply_text(text)

# ---------------- BACKGROUND TASK ---------------- #
async def check_links(app):
    users = list(users_col.find({}))
    for user in users:
        chat_id = user["chat_id"]
        results = []
        for link in user["links"]:
            status = fetch_status(link)
            results.append(f"{link} → {status}")
        last_checked = now_ist()
        update_user(chat_id, {"last_checked": last_checked})
        if user["notify"]:
            text = "📢 Update:\n" + "\n".join(results) + f"\n⏰ {last_checked}"
            try:
                await app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logging.error(f"Failed to send message: {e}")

def run_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: app.create_task(check_links(app)), "interval", minutes=5)
    scheduler.start()

# ---------------- MAIN ---------------- #
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("list", list_links))
    application.add_handler(CommandHandler("remove", remove))
    application.add_handler(CommandHandler("update", update_link))
    application.add_handler(CommandHandler("interval", set_interval))
    application.add_handler(CommandHandler("notify", notify))
    application.add_handler(CommandHandler("stats", stats))

    # Scheduler
    run_scheduler(application)

    application.run_polling()

if __name__ == "__main__":
    main()
