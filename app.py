import os
import asyncio
import logging
from telegram import Update
from telegram.ext import (
ApplicationBuilder,
CommandHandler,
ContextTypes,
CallbackContext,
JobQueue,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import httpx

# Enable logging
logging.basicConfig(
format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8443))

if not BOT_TOKEN or not WEBHOOK_URL:
raise RuntimeError("Missing BOT_TOKEN or WEBHOOK_URL environment variable")

# Global data storage
links = []
notifications_on = True
check_interval = 5 # in minutes

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
"👋 Welcome to HMT Stock Bot!\n"
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
if context.args:
url = context.args[0]
if ".store/product/" not in url:
await update.message.reply_text("❌ Only HMT .store product links are allowed.")
return
links.append(url)
await update.message.reply_text(f"✅ Link added:\n{url}")
else:
await update.message.reply_text("❌ Usage: /add <link>")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
if links:
text = "\n".join([f"{i+1}. {link}" for i, link in enumerate(links)])
else:
text = "No links added yet."
await update.message.reply_text(text)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
if context.args:
try:
index = int(context.args[0]) - 1
removed = links.pop(index)
await update.message.reply_text(f"✅ Removed link:\n{removed}")
except (IndexError, ValueError):
await update.message.reply_text("❌ Invalid index.")
else:
await update.message.reply_text("❌ Usage: /remove <index>")

async def update_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
if len(context.args) >= 2:
try:
index = int(context.args[0]) - 1
new_link = context.args[1]
if ".store/product/" not in new_link:
await update.message.reply_text("❌ Only HMT .store product links are allowed.")
return
links[index] = new_link
await update.message.reply_text(f"✅ Updated link:\n{new_link}")
except (IndexError, ValueError):
await update.message.reply_text("❌ Invalid index.")
else:
await update.message.reply_text("❌ Usage: /update <index> <new_link>")

async def interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
global check_interval
if context.args:
try:
minutes = int(context.args[0])
check_interval = minutes
await update.message.reply_text(f"✅ Interval updated to {minutes} minutes.")
except ValueError:
await update.message.reply_text("❌ Invalid number.")
else:
await update.message.reply_text("❌ Usage: /interval <minutes>")

async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
global notifications_on
if context.args:
arg = context.args[0].lower()
if arg == "on":
notifications_on = True
await update.message.reply_text("✅ Notifications turned ON.")
elif arg == "off":
notifications_on = False
await update.message.reply_text("✅ Notifications turned OFF.")
else:
await update.message.reply_text("❌ Usage: /notify on|off")
else:
await update.message.reply_text("❌ Usage: /notify on|off")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(f"Links tracked: {len(links)}\nNotifications: {'ON' if notifications_on else 'OFF'}")

# Link checking function
async def check_links(app: ApplicationBuilder):
if not links or not notifications_on:
return
async with httpx.AsyncClient() as client:
for link in links:
try:
resp = await client.get(link, timeout=10)
if resp.status_code == 200:
# You can add real stock checking logic here if needed
logger.info(f"✅ Link accessible: {link}")
else:
logger.warning(f"⚠️ Link returned status {resp.status_code}: {link}")
except Exception as e:
logger.error(f"❌ Error checking {link}: {e}")

# Scheduler setup
scheduler = AsyncIOScheduler()
scheduler.add_job(
lambda: asyncio.create_task(check_links(app)),
trigger=IntervalTrigger(minutes=check_interval),
id="link_checker",
replace_existing=True
)
scheduler.start()

# Main function
def main():
global app
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Add command handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add))
app.add_handler(CommandHandler("list", list_links))
app.add_handler(CommandHandler("remove", remove))
app.add_handler(CommandHandler("update", update_link))
app.add_handler(CommandHandler("interval", interval))
app.add_handler(CommandHandler("notify", notify))
app.add_handler(CommandHandler("stats", stats))

# Set webhook
app.run_webhook(
listen="0.0.0.0",
port=PORT,
url_path=BOT_TOKEN,
webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
)

if __name__ == "__main__":
main()
