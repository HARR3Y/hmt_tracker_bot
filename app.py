import os
import asyncio
import httpx
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import (
ApplicationBuilder,
CommandHandler,
ContextTypes,
MessageHandler,
filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# -----------------------
# CONFIG
# -----------------------
BOT_TOKEN = os.environ.get("8343686028:AAHWD1psflTNUxoUG7E9NeR1HxbUlCg_DbE")
WEBHOOK_URL = os.environ.get("https://hmt-tracker-bot.onrender.com/") # Example: https://<your-render-service>.onrender.com/
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 5)) # in minutes

if not BOT_TOKEN or not WEBHOOK_URL:
raise RuntimeError("Missing BOT_TOKEN or WEBHOOK_URL environment variable")

# -----------------------
# DATA STORAGE
# -----------------------
# For simplicity, using in-memory storage; can replace with DB if needed
products = [] # List of dicts: {"url": ..., "last_stock": ...}
notify_on = True

# -----------------------
# FLASK APP FOR RENDER
# -----------------------
flask_app = Flask(__name__)

# -----------------------
# TELEGRAM BOT APP
# -----------------------
app = ApplicationBuilder().token(BOT_TOKEN).build()

# -----------------------
# COMMANDS
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
"👋 Welcome to HMT Stock Bot @hmt_tracker_bot!\n"
"Commands:\n"
"/add <link>\n"
"/list\n"
"/remove <index>\n"
"/update <index> <new_link>\n"
"/interval <minutes>\n"
"/notify on|off\n"
"/stats"
)

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
if len(context.args) != 1:
await update.message.reply_text("Usage: /add <HMT .store product link>")
return
url = context.args[0]
if ".store" not in url:
await update.message.reply_text("Only .store HMT website links are supported!")
return
products.append({"url": url, "last_stock": None})
await update.message.reply_text(f"Added: {url}")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
if not products:
await update.message.reply_text("No products being tracked.")
return
msg = "\n".join([f"{i+1}. {p['url']}" for i, p in enumerate(products)])
await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
if len(context.args) != 1 or not context.args[0].isdigit():
await update.message.reply_text("Usage: /remove <index>")
return
index = int(context.args[0]) - 1
if index < 0 or index >= len(products):
await update.message.reply_text("Invalid index")
return
removed = products.pop(index)
await update.message.reply_text(f"Removed: {removed['url']}")

async def update_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
if len(context.args) != 2:
await update.message.reply_text("Usage: /update <index> <new_link>")
return
index, new_url = context.args
if not index.isdigit():
await update.message.reply_text("Invalid index")
return
index = int(index) - 1
if index < 0 or index >= len(products):
await update.message.reply_text("Invalid index")
return
if ".store" not in new_url:
await update.message.reply_text("Only .store HMT website links are supported!")
return
products[index]["url"] = new_url
products[index]["last_stock"] = None
await update.message.reply_text(f"Updated index {index+1} to {new_url}")

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
global CHECK_INTERVAL
if len(context.args) != 1 or not context.args[0].isdigit():
await update.message.reply_text("Usage: /interval <minutes>")
return
CHECK_INTERVAL = int(context.args[0])
scheduler.reschedule_job("stock_check", trigger="interval", minutes=CHECK_INTERVAL)
await update.message.reply_text(f"Check interval set to {CHECK_INTERVAL} minutes")

async def set_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
global notify_on
if len(context.args) != 1 or context.args[0].lower() not in ["on", "off"]:
await update.message.reply_text("Usage: /notify on|off")
return
notify_on = context.args[0].lower() == "on"
await update.message.reply_text(f"Notifications {'enabled' if notify_on else 'disabled'}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(f"Tracking {len(products)} products.\nNotify: {notify_on}\nInterval: {CHECK_INTERVAL} min")

# -----------------------
# STOCK CHECK FUNCTION
# -----------------------
async def check_stock():
if not products:
return
async with httpx.AsyncClient(timeout=10) as client:
for p in products:
try:
r = await client.get(p["url"])
in_stock = "Out of Stock" not in r.text
if p["last_stock"] is None:
p["last_stock"] = in_stock
elif in_stock != p["last_stock"]:
p["last_stock"] = in_stock
if notify_on:
msg = f"✅ Product {'in stock' if in_stock else 'out of stock'}:\n{p['url']}"
await app.bot.send_message(chat_id=YOUR_TELEGRAM_CHAT_ID, text=msg)
except Exception as e:
print(f"Error checking {p['url']}: {e}")

# -----------------------
# APSCHEDULER
# -----------------------
scheduler = AsyncIOScheduler()
scheduler.add_job(check_stock, "interval", minutes=CHECK_INTERVAL, id="stock_check")
scheduler.start()

# -----------------------
# WEBHOOK ROUTE
# -----------------------
@flask_app.route("/", methods=["POST"])
def webhook():
update = request.get_json(force=True)
asyncio.create_task(app.update_queue.put(update))
return "ok"

# -----------------------
# ADD HANDLERS
# -----------------------
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add))
app.add_handler(CommandHandler("list", list_links))
app.add_handler(CommandHandler("remove", remove))
app.add_handler(CommandHandler("update", update_link))
app.add_handler(CommandHandler("interval", set_interval))
app.add_handler(CommandHandler("notify", set_notify))
app.add_handler(CommandHandler("stats", stats))

# -----------------------
# MAIN
# -----------------------
async def set_webhook():
await app.bot.set_webhook(WEBHOOK_URL)

if __name__ == "__main__":
import asyncio
asyncio.run(set_webhook())
flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
