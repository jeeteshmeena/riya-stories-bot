import re
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Admin check function from main bot
# Note: we will dynamically import `is_admin` to avoid circular imports, or just duplicate the simple check if we can't

# Conversation states
(
    SELECT_FORMAT,
    ENTER_NAME,
    SELECT_STATUS,
    SELECT_GENRE,
    ENTER_LINK,
    UPLOAD_IMAGE,
    ENTER_DESC,
    ENTER_EPISODES,
    SELECT_PLATFORM,
    ENTER_USERNAME,
    CONFIRM_POST
) = range(11)

# Emojis for formats
STATUS_EMOJIS = {"Completed": "✅", "Ongoing": "⏳", "RIP": "💀"}
DEFAULT_JOIN_USERNAME = "@StoriesByJeetXNew"
DEFAULT_FORMAT_1_EMOJI = "🫠"
DEFAULT_FORMAT_1_JOIN_EMOJI = "🦊"

def is_admin_local(user_id):
    from config import ADMIN_ID, OWNER_ID
    from database import load_config
    if user_id == OWNER_ID or (ADMIN_ID != 0 and user_id == ADMIN_ID):
        return True
    bot_config = load_config()
    moderators = bot_config.get("moderators", [])
    if str(user_id) in moderators or user_id in moderators:
        return True
    return False

def build_format_1(data):
    # Name :- Nayi Padosan ( Completed )
    # Story Type :- Crime (NFK)
    # Link 🖇:- https://t.me/...
    # JOIN FOR ALL EPISODES.
    # Join - @Username
    
    emoji = DEFAULT_FORMAT_1_EMOJI
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown")
    link = data.get("link", "")
    join_username = data.get("username", DEFAULT_JOIN_USERNAME)
    join_emoji = DEFAULT_FORMAT_1_JOIN_EMOJI
    
    # Exact spacing and formatting
    text = f"{emoji} <b>Name :-</b>  <b>{html.escape(name)}</b> ( <b>{status}</b> )\n\n"
    text += f"<b>Story Type :-</b> {html.escape(genre)}\n\n"
    text += f"<b>Link 🖇:-</b> {link}\n\n"
    text += f"{join_emoji}<b>JOIN FOR ALL EPISODES.</b>\n\n"
    text += f"Join - {join_username}"
    return text

def build_format_2(data):
    # Secret Ameerzada • Pocket FM
    # > _Description_
    # Episodes - 1116
    # Status - Completed
    # All Stories Available on Stories🫶🏻.
    # link
    # link
    
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    desc = data.get("desc", "")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    availability = data.get("availability", "All Stories Available on Stories🫶🏻.")
    
    # Use blockquote (requires HTML parse_mode)
    safe_desc = html.escape(desc)
    # Adding italic
    desc_block = f"<blockquote><i>{safe_desc}</i></blockquote>\n\n" if desc else ""
    
    text = f"<b>{html.escape(name)} • {html.escape(platform)}</b>\n\n"
    text += desc_block
    text += f"<b>Episodes -</b> <b>{episodes}</b>\n"
    text += f"<b>Status -</b> <b>{status}</b>\n\n"
    text += f"{html.escape(availability)}\n\n"
    text += f"{link}\n{link}"
    return text

async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_local(user.id):
        await update.message.reply_text("⛔ Admin only feature.")
        return ConversationHandler.END
        
    # Reset state
    context.user_data['pb_data'] = {}
    
    keyboard = [
        [InlineKeyboardButton("Format 1 (Channel)", callback_data="pb_fmt|1")],
        [InlineKeyboardButton("Format 2 (Detail)", callback_data="pb_fmt|2")],
        [InlineKeyboardButton("Both", callback_data="pb_fmt|both")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    await update.message.reply_text(
        "<b>🛠 Story Post Builder</b>\n\n"
        "Welcome to the Post Builder! Pick a format to begin:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return SELECT_FORMAT

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_cancel":
        await query.edit_message_text("Post builder cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    _, fmt = query.data.split("|")
    context.user_data['pb_data']['format'] = fmt
    
    await query.edit_message_text("📝 <b>Step 1: Enter Story Name</b>\n\nType the name below:", parse_mode="HTML")
    return ENTER_NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    context.user_data['pb_data']['name'] = name
    
    keyboard = [
        [
            InlineKeyboardButton("Completed", callback_data="pb_status|Completed"),
            InlineKeyboardButton("Ongoing", callback_data="pb_status|Ongoing")
        ],
        [InlineKeyboardButton("RIP", callback_data="pb_status|RIP")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    await update.message.reply_text("📶 <b>Step 2: Select Status</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return SELECT_STATUS

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
        
    _, status = query.data.split("|")
    context.user_data['pb_data']['status'] = status
    
    fmt = context.user_data['pb_data']['format']
    if fmt in ["1", "both"]:
        keyboard = [
            [InlineKeyboardButton("Crime", callback_data="pb_genre|Crime"), InlineKeyboardButton("Romance", callback_data="pb_genre|Romance")],
            [InlineKeyboardButton("Horror", callback_data="pb_genre|Horror"), InlineKeyboardButton("Thriller", callback_data="pb_genre|Thriller")],
            [InlineKeyboardButton("Type Custom", callback_data="pb_genre_custom")]
        ]
        await query.edit_message_text("🎭 <b>Step 3: Select or Type Genre</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_GENRE
    else:
        # Format 2 doesn't use genre, jump to Link
        await query.edit_message_text("🔗 <b>Step 4: Enter Telegram Link</b>", parse_mode="HTML")
        return ENTER_LINK

async def handle_genre_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
    elif query.data == "pb_genre_custom":
        await query.edit_message_text("Please type the custom genre name:")
        return SELECT_GENRE
        
    _, genre = query.data.split("|")
    context.user_data['pb_data']['genre'] = genre
    
    await query.edit_message_text(f"Genre selected: {genre}\n\n🔗 <b>Step 4: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_genre_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    genre = update.message.text
    context.user_data['pb_data']['genre'] = genre
    await update.message.reply_text("🔗 <b>Step 4: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text
    context.user_data['pb_data']['link'] = link
    
    await update.message.reply_text("🖼 <b>Step 5: Upload Image</b>\n\nSend a photo for the post.", parse_mode="HTML")
    return UPLOAD_IMAGE

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        context.user_data['pb_data']['photo_id'] = photo_id
    elif update.message.text == "/skip":
        context.user_data['pb_data']['photo_id'] = None
    else:
        await update.message.reply_text("Please upload an image/photo, or send /skip if you want text only.")
        return UPLOAD_IMAGE
        
    fmt = context.user_data['pb_data']['format']
    if fmt in ["2", "both"]:
        await update.message.reply_text("📝 <b>Step 6: Enter Description</b>\n\n(It will be auto-formatted as a blockquote italic)", parse_mode="HTML")
        return ENTER_DESC
    else:
        # Format 1 jump to Username
        keyboard = [
            [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
            [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
        ]
        await update.message.reply_text("👤 <b>Step 9: Confirm Join Username</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ENTER_USERNAME

async def handle_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['desc'] = update.message.text
    await update.message.reply_text("🔢 <b>Step 7: Enter Episodes Count</b>\n\nE.g.: 1116", parse_mode="HTML")
    return ENTER_EPISODES

async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['episodes'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("Pocket FM", callback_data="pb_plat|Pocket FM"), InlineKeyboardButton("Kuku FM", callback_data="pb_plat|Kuku FM")],
        [InlineKeyboardButton("Headfone", callback_data="pb_plat|Headfone"), InlineKeyboardButton("Type Custom", callback_data="pb_plat_custom")]
    ]
    await update.message.reply_text("🎵 <b>Step 8: Select Platform</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return SELECT_PLATFORM

async def handle_platform_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_plat_custom":
        await query.edit_message_text("Please type the custom platform name:")
        return SELECT_PLATFORM
        
    _, plat = query.data.split("|")
    context.user_data['pb_data']['platform'] = plat
    
    keyboard = [
        [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
        [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
    ]
    await query.edit_message_text("👤 <b>Step 9: Confirm Join Username</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ENTER_USERNAME

async def handle_platform_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plat = update.message.text
    context.user_data['pb_data']['platform'] = plat
    keyboard = [
        [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
        [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
    ]
    await update.message.reply_text("👤 <b>Step 9: Confirm Join Username</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ENTER_USERNAME

async def handle_username_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_user_custom":
        await query.edit_message_text("Please type the custom username (e.g. @MyChannel):")
        return ENTER_USERNAME
        
    context.user_data['pb_data']['username'] = DEFAULT_JOIN_USERNAME
    return await generate_preview(query.message, context)

async def handle_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['username'] = update.message.text
    return await generate_preview(update.message, context)

async def generate_preview(message, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get('pb_data', {})
    fmt = data.get('format')
    
    previews = []
    if fmt in ["1", "both"]:
        previews.append(build_format_1(data))
    if fmt in ["2", "both"]:
        previews.append(build_format_2(data))
        
    data['cached_previews'] = previews
    
    keyboard = [
        [InlineKeyboardButton("Confirm & Send", callback_data="pb_final_send")],
        [InlineKeyboardButton("Edit (Restart)", callback_data="pb_final_edit"), InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    
    await message.reply_text("🔎 <b>PREVIEW GENERATED</b>\nReview your posts below.", parse_mode="HTML")
    
    for p in previews:
        if data.get('photo_id'):
            try:
                await context.bot.send_photo(chat_id=message.chat_id, photo=data['photo_id'], caption=p, parse_mode="HTML")
            except Exception as e:
                # Auto fallback to text if long caption
                await context.bot.send_photo(chat_id=message.chat_id, photo=data['photo_id'])
                await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
            
    await message.reply_text("Proceed with posting?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_POST

async def handle_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
    elif query.data == "pb_final_edit":
        # Restart
        context.user_data['pb_data'] = {}
        keyboard = [
            [InlineKeyboardButton("Format 1 (Channel)", callback_data="pb_fmt|1")],
            [InlineKeyboardButton("Format 2 (Detail)", callback_data="pb_fmt|2")],
            [InlineKeyboardButton("Both", callback_data="pb_fmt|both")],
            [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
        ]
        await query.edit_message_text("<b>🛠 Story Post Builder</b>\n\nRestarting...", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_FORMAT
    elif query.data == "pb_final_send":
        data = context.user_data.get('pb_data', {})
        previews = data.get('cached_previews', [])
        photo_id = data.get('photo_id')
        
        for p in previews:
            if photo_id:
                try:
                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=photo_id, caption=p, parse_mode="HTML")
                except Exception:
                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=photo_id)
                    await context.bot.send_message(chat_id=query.message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
                
        await query.edit_message_text("✅ <b>Post Generated Successfully!</b>", parse_mode="HTML")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END

# Error Handler for Conversation
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Process cancelled.")
    context.user_data.pop('pb_data', None)
    return ConversationHandler.END

# The conversational handler definition
post_builder_handler = ConversationHandler(
    entry_points=[
        CommandHandler("createpost", start_builder),
        CallbackQueryHandler(start_builder, pattern="^menu\|createpost")
    ],
    states={
        SELECT_FORMAT: [CallbackQueryHandler(handle_format, pattern="^pb_")],
        ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        SELECT_STATUS: [CallbackQueryHandler(handle_status, pattern="^pb_")],
        SELECT_GENRE: [
            CallbackQueryHandler(handle_genre_btn, pattern="^pb_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre_text)
        ],
        ENTER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        UPLOAD_IMAGE: [
            MessageHandler(filters.PHOTO, handle_image),
            CommandHandler("skip", handle_image) # optional textual skip if we change our mind
        ],
        ENTER_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc)],
        ENTER_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        SELECT_PLATFORM: [
            CallbackQueryHandler(handle_platform_btn, pattern="^pb_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform_text)
        ],
        ENTER_USERNAME: [
            CallbackQueryHandler(handle_username_btn, pattern="^pb_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username_text)
        ],
        CONFIRM_POST: [CallbackQueryHandler(handle_final, pattern="^pb_final_|^pb_cancel")]
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)],
    per_message=False
)
