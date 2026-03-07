
    await update.message.reply_text(
        "✨ Riya Bot v10 Quantum\n\nSend story name to search."
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = await update.message.reply_text(
        "🔍 Scanning channel..."
    )

    try:

        result = await scan_channel(CHANNEL_ID)

        await msg.edit_text(
            f"✅ Scan Completed\n\n"
            f"Messages scanned: {result['messages']}\n"
            f"Stories found: {result['stories']}"
        )

    except Exception as e:

        await msg.edit_text(f"❌ Scan failed\n{e}")


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.message.text.lower().strip()

    progress = await update.message.reply_text(progress_bar(0))

    result = fuzzy_search(query)

    if not result:

        await progress.edit_text("❌ Story not found")
        return

    text = (
        f"🔥 Story Found\n\n"
        f"Name: {result['name']}\n"
        f"Type: {result.get('type','Unknown')}\n\n"
        f"{result['link']}"
    )

    await progress.edit_text(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.error("Error:", exc_info=context.error)


# -----------------------
# Bot start
# -----------------------

def start_bot():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)
    )

    app.add_error_handler(error_handler)

    logger.info("Riya Bot v10 Quantum running")

    app.run_polling()


# -----------------------
# Main
# -----------------------

def main():

    # Render port
    threading.Thread(target=start_dummy_server).start()

    # Telegram bot
    start_bot()


if __name__ == "__main__":
    main()
