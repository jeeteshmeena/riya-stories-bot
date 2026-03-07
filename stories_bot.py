
import asyncio
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import BOT_TOKEN
from database import init_db, search_story_ai
from progress_bar import run_progress
from filters_text import is_valid_query
from language_system import get_language_reply

async def start(update, context):
    await update.message.reply_text("🚀 Riya Bot v10 Quantum AI Engine Online")

async def stats(update, context):
    from database import get_stats
    total = get_stats()
    await update.message.reply_text(f"📊 Database Stats\nStories: {total}")

async def search(update, context):
    text = update.message.text.strip()

    if not is_valid_query(text):
        return

    msg = await run_progress(update)

    result = search_story_ai(text)

    if not result:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Request Story", callback_data="request")
        ]])
        await msg.edit_text("❌ Story not found", reply_markup=keyboard)
        return

    name, type_, link = result

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Open Story", url=link)],
        [InlineKeyboardButton("Delete", callback_data="delete")],
        [InlineKeyboardButton("Copyright", callback_data="copyright")]
    ])

    await msg.edit_text(
        f"✨ 𝐑𝐢𝐲𝐚 𝐒𝐭𝐨𝐫𝐲 𝐅𝐢𝐧𝐝𝐞𝐫 ✨\n\n🔥 Name :- {name}\n📖 Type :- {type_}",
        reply_markup=keyboard
    )

async def delete_msg(update, context):
    await update.callback_query.message.delete()

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(delete_msg, pattern="delete"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("Riya Bot v10 Quantum running")
    app.run_polling()

if __name__ == "__main__":
    main()
