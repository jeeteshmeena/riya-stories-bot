
import asyncio

async def run_progress(update):
    msg = await update.message.reply_text("🔎 Searching...")
    await asyncio.sleep(0.3)
    await msg.edit_text("🔎 Searching...\n██░░░░░░")
    await asyncio.sleep(0.3)
    await msg.edit_text("🔎 Searching...\n████░░░░")
    await asyncio.sleep(0.3)
    await msg.edit_text("🔎 Searching...\n████████")
    return msg
