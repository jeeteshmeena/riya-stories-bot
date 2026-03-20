import re
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

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
    SELECT_DESTINATION,
    CUSTOM_DESTINATION,
    CONFIRM_POST
) = range(13)

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
    emoji = DEFAULT_FORMAT_1_EMOJI
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown")
    link = data.get("link", "")
    join_username = data.get("username", DEFAULT_JOIN_USERNAME)
    join_emoji = DEFAULT_FORMAT_1_JOIN_EMOJI
    text = f"{emoji} <b>Name :-</b>  <b>{html.escape(name)}</b> ( <b>{status}</b> )\n\n"
    text += f"<b>Story Type :-</b> {html.escape(genre)}\n\n"
    text += f"<b>Link 🖇:-</b> {link}\n\n"
    text += f"{join_emoji}<b>JOIN FOR ALL EPISODES.</b>\n\n"
    text += f"Join - {join_username}"
    return text

def build_format_2(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    desc = data.get("desc", "")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    availability = data.get("availability", "All Stories Available on Stories🫶🏻.")
    safe_desc = html.escape(desc)
    desc_block = f"<blockquote expandable><i>{safe_desc}</i></blockquote>\n\n" if desc else ""
    text = f"<b>{html.escape(name)} • {html.escape(platform)}</b>\n\n"
    text += desc_block
    text += f"<b>Episodes -</b> <b>{episodes}</b>\n"
    text += f"<b>Status -</b> <b>{status}</b>\n\n"
    text += f"{html.escape(availability)}\n\n"
    text += f"{link}\n{link}"
    return text

def build_format_3(data):
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown")
    link = data.get("link", "")
    text = f"❤️‍🔥 <b>{html.escape(name)}</b> ❤️‍🔥\n\n"
    text += f"🔹 <b>Status:</b> <b>{status}</b>\n"
    text += f"🔹 <b>Genre:</b> {html.escape(genre)}\n\n"
    text += f"📥 <b>Download Now</b> 👇\n"
    text += f"{link}"
    return text

def build_format_4(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    username = data.get("username", DEFAULT_JOIN_USERNAME)
    text = f"🎬 <b>Name:</b> <b>{html.escape(name)}</b>\n"
    text += f"🎧 <b>Platform:</b> {html.escape(platform)}\n"
    text += f"🔢 <b>Episodes:</b> {episodes} ({status})\n\n"
    text += f"🔗 <b>Link:</b> {link}\n\n"
    text += f"Join: {username}"
    return text

def build_format_5(data):
    name = data.get("name", "Unknown")
    desc = data.get("desc", "")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    text = f"📖 <b>{html.escape(name)}</b>\n\n"
    if desc:
        text += f"<blockquote expandable><i>{html.escape(desc)}</i></blockquote>\n\n"
    text += f"🎭 <b>Genre:</b> {html.escape(genre)}\n"
    text += f"📌 <b>Status:</b> <b>{status}</b>\n\n"
    text += f"🔗 Read Here: {link}"
    return text

def build_format_6(data):
    name = data.get("name", "Unknown")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    episodes = data.get("episodes", "0")
    desc = data.get("desc", "")
    link = data.get("link", "")
    safe_desc = html.escape(desc)
    desc_block = f"<blockquote expandable><i>{safe_desc}</i></blockquote>\n\n" if desc else ""
    text = f"<b>🪷 {html.escape(name)}</b>\n"
    text += f"━━━━━━━━━━━━━━━━\n"
    text += f"▸ <b>Genre :</b> {html.escape(genre)}\n"
    text += f"▸ <b>Episodes :</b> {episodes}\n"
    text += f"▸ <b>Status :</b> <b>{status}</b>\n"
    text += f"━━━━━━━━━━━━━━━━\n"
    text += f"📖 <b>Story Summary:</b>\n"
    text += f"{desc_block}"
    text += f"▶️ <b>Read/Listen Now:</b>\n{link}"
    return text

def build_format_7(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    desc = data.get("desc", "")
    link = data.get("link", "")
    username = data.get("username", DEFAULT_JOIN_USERNAME)
    safe_desc = html.escape(desc)
    desc_block = f"<blockquote expandable><i>{safe_desc}</i></blockquote>\n\n" if desc else ""
    text = f"🎞 <b>{html.escape(name)}</b> 🎞\n"
    text += f"<i>{html.escape(platform)} Exclusive</i>\n\n"
    text += f"<b>Category:</b> {html.escape(genre)}  |  <b>Progress:</b> <b>{status}</b>\n\n"
    text += f"{desc_block}"
    text += f"🔗 <b>Full Episodes Link:</b>\n{link}\n\n"
    text += f"━━━━━━━━━━━━━━━━\n"
    text += f"👥 <b>Join channel for updates:</b> {username}"
    return text

def build_format_8(data):
    name = data.get("name", "Unknown")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    episodes = data.get("episodes", "0")
    link = data.get("link", "")
    text = f"<b>⊛ {html.escape(name)}</b>\n\n"
    text += f"<b>[ {html.escape(genre)} • {status} • {episodes} Eps ]</b>\n\n"
    text += f"{link}"
    return text

async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_local(user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ Admin only feature.", show_alert=True)
        else:
            await update.message.reply_text("⛔ Admin only feature.")
        return ConversationHandler.END
        
    context.user_data['pb_data'] = {}
    
    keyboard = [
        [InlineKeyboardButton("1 (Channel)", callback_data="pb_fmt|1"), InlineKeyboardButton("2 (Detail)", callback_data="pb_fmt|2")],
        [InlineKeyboardButton("3 (Minimal)", callback_data="pb_fmt|3"), InlineKeyboardButton("4 (Banner)", callback_data="pb_fmt|4")],
        [InlineKeyboardButton("5 (Focus)", callback_data="pb_fmt|5"), InlineKeyboardButton("6 (VIP)", callback_data="pb_fmt|6")],
        [InlineKeyboardButton("7 (Cinematic)", callback_data="pb_fmt|7"), InlineKeyboardButton("8 (Dark)", callback_data="pb_fmt|8")],
        [InlineKeyboardButton("Both 1 & 2", callback_data="pb_fmt|both")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    
    text = "<b>🛠 Story Post Builder</b>\n\nWelcome to the Post Builder! Pick a format to begin:"
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        
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
    if fmt in ["1", "3", "5", "6", "7", "8", "both"]:
        keyboard = [
            [InlineKeyboardButton("Crime", callback_data="pb_genre|Crime"), InlineKeyboardButton("Romance", callback_data="pb_genre|Romance")],
            [InlineKeyboardButton("Horror", callback_data="pb_genre|Horror"), InlineKeyboardButton("Thriller", callback_data="pb_genre|Thriller")],
            [InlineKeyboardButton("Type Custom", callback_data="pb_genre_custom")]
        ]
        await query.edit_message_text("🎭 <b>Step 3: Select or Type Genre</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_GENRE
    else:
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
        
    context.user_data['pb_data']['genre'] = query.data.split("|")[1]
    await query.edit_message_text(f"Genre selected: {context.user_data['pb_data']['genre']}\n\n🔗 <b>Step 4: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_genre_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['genre'] = update.message.text
    await update.message.reply_text("🔗 <b>Step 4: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['link'] = update.message.text
    await update.message.reply_text("🖼 <b>Step 5: Upload Image(s)</b>\n\nSend a photo for the post. You can send multiple. Press 'Done' when finished, or /skip for text-only.", parse_mode="HTML")
    # Initialize array
    context.user_data['pb_data']['photo_ids'] = []
    return UPLOAD_IMAGE

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This captures multiple messages natively
    if update.message.photo:
        context.user_data['pb_data']['photo_ids'].append(update.message.photo[-1].file_id)
        keyboard = [[InlineKeyboardButton("✅ Done Adding Images", callback_data="pb_img_done")]]
        await update.message.reply_text("Image saved! Send more images if needed, or click 'Done'.", reply_markup=InlineKeyboardMarkup(keyboard))
        return UPLOAD_IMAGE
    elif update.message.text == "/skip":
        # Do not append anything
        pass
    else:
        await update.message.reply_text("Please send a valid photo, press Done below, or /skip.")
        return UPLOAD_IMAGE

    return await handle_image_next(update, context)

async def handle_image_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await handle_image_next(update, context, query)

async def handle_image_next(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    fmt = context.user_data['pb_data']['format']
    msg = query.message if query else update.message
    
    if fmt in ["2", "4", "5", "6", "7", "both"]:
        text = "📝 <b>Step 6: Enter Description</b>\n\n(It will be auto-formatted as a blockquote italic)"
        if query: await query.edit_message_text(text, parse_mode="HTML")
        else: await msg.reply_text(text, parse_mode="HTML")
        return ENTER_DESC
    elif fmt in ["1", "3", "8"]:
        if fmt == "3":
            return await generate_preview(msg, context, query)
        elif fmt == "8":
            text = "🔢 <b>Step 7: Enter Episodes Count</b>"
            if query: await query.edit_message_text(text, parse_mode="HTML")
            else: await msg.reply_text(text, parse_mode="HTML")
            return ENTER_EPISODES
            
        keyboard = [
            [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
            [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
        ]
        text = "👤 <b>Step 9: Confirm Join Username</b>"
        if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else: await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ENTER_USERNAME

async def handle_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['desc'] = update.message.text
    fmt = context.user_data['pb_data']['format']
    
    if fmt == "5":
        return await generate_preview(update.message, context)
        
    if fmt == "7":
        keyboard = [
            [InlineKeyboardButton("Pocket FM", callback_data="pb_plat|Pocket FM"), InlineKeyboardButton("Kuku FM", callback_data="pb_plat|Kuku FM")],
            [InlineKeyboardButton("Headfone", callback_data="pb_plat|Headfone"), InlineKeyboardButton("Type Custom", callback_data="pb_plat_custom")]
        ]
        await update.message.reply_text("🎵 <b>Step 8: Select Platform</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_PLATFORM
        
    await update.message.reply_text("🔢 <b>Step 7: Enter Episodes Count</b>\n\nE.g.: 1116", parse_mode="HTML")
    return ENTER_EPISODES

async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['episodes'] = update.message.text
    fmt = context.user_data['pb_data']['format']
    
    if fmt in ["6", "8"]:
        return await generate_preview(update.message, context)
        
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
    
    fmt = context.user_data['pb_data']['format']
    if fmt == "2":
        return await generate_preview(query.message, context, query)
        
    keyboard = [
        [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
        [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
    ]
    await query.edit_message_text("👤 <b>Step 9: Confirm Join Username</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ENTER_USERNAME

async def handle_platform_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plat = update.message.text
    context.user_data['pb_data']['platform'] = plat
    
    fmt = context.user_data['pb_data']['format']
    if fmt == "2":
        return await generate_preview(update.message, context)
        
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
    return await generate_preview(query.message, context, query)

async def handle_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['username'] = update.message.text
    return await generate_preview(update.message, context)

async def generate_preview(message, context: ContextTypes.DEFAULT_TYPE, query=None):
    data = context.user_data.get('pb_data', {})
    fmt = data.get('format')
    
    previews = []
    formats = [fmt] if fmt != "both" else ["1", "2"]
    for f in formats:
        if f == "1": previews.append(build_format_1(data))
        elif f == "2": previews.append(build_format_2(data))
        elif f == "3": previews.append(build_format_3(data))
        elif f == "4": previews.append(build_format_4(data))
        elif f == "5": previews.append(build_format_5(data))
        elif f == "6": previews.append(build_format_6(data))
        elif f == "7": previews.append(build_format_7(data))
        elif f == "8": previews.append(build_format_8(data))
        
    data['cached_previews'] = previews
    
    if query:
        await query.edit_message_text("🔎 <b>PREVIEW GENERATED</b>\nReview your posts below.", parse_mode="HTML")
    else:
        await message.reply_text("🔎 <b>PREVIEW GENERATED</b>\nReview your posts below.", parse_mode="HTML")
    
    for p in previews:
        photo_ids = data.get('photo_ids', [])
        if photo_ids:
            if len(photo_ids) == 1:
                try:
                    await context.bot.send_photo(chat_id=message.chat_id, photo=photo_ids[0], caption=p, parse_mode="HTML")
                except Exception:
                    await context.bot.send_photo(chat_id=message.chat_id, photo=photo_ids[0])
                    await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
            else:
                media_group = [InputMediaPhoto(media=photo_ids[0], caption=p, parse_mode="HTML")]
                for pid in photo_ids[1:]:
                    media_group.append(InputMediaPhoto(media=pid))
                await context.bot.send_media_group(chat_id=message.chat_id, media=media_group)
        else:
            await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
            
    # Move to destination picker
    from database import load_config
    bot_config = load_config()
    destinations = bot_config.get("post_channels", [])
    
    keyboard = []
    for d in destinations:
        keyboard.append([KeyboardButton(d)])
    keyboard.append([KeyboardButton("➕ Add New Channel")])
    keyboard.append([KeyboardButton("✖ Skip (Post here only)")])
    
    await message.reply_text(
        "🎯 <b>Select Destination Channel</b>\nWhere should this be auto-posted?", 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="HTML"
    )
    return SELECT_DESTINATION

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    if dest == "➕ Add New Channel":
        await update.message.reply_text("Send the Channel Username (e.g. @MyChannel) or ID (e.g. -1001234567):", reply_markup=ReplyKeyboardRemove())
        return CUSTOM_DESTINATION
        
    if dest != "✖ Skip (Post here only)":
        context.user_data['pb_data']['destination'] = dest
        
    await ask_final_confirm(update.message)
    return CONFIRM_POST

async def handle_custom_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    # Save globally
    from database import load_config, save_config
    bot_config = load_config()
    destinations = bot_config.get("post_channels", [])
    if dest not in destinations:
        destinations.append(dest)
        bot_config["post_channels"] = destinations
        save_config(bot_config)
        
    context.user_data['pb_data']['destination'] = dest
    await update.message.reply_text(f"✅ Channel {dest} saved for future use.")
    await ask_final_confirm(update.message)
    return CONFIRM_POST
    
async def ask_final_confirm(message):
    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Send", callback_data="pb_final_send")],
        [InlineKeyboardButton("Edit (Restart)", callback_data="pb_final_edit"), InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    await message.reply_text("Ready to post? Select below:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def handle_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.", reply_markup=None)
        # We might have left a ReplyKeyboardMarkup dangling if they skipped via text and didn't remove it. 
        # But we did one_time_keyboard=True so it should hide.
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    elif query.data == "pb_final_edit":
        context.user_data['pb_data'] = {}
        keyboard = [
            [InlineKeyboardButton("1 (Channel)", callback_data="pb_fmt|1"), InlineKeyboardButton("2 (Detail)", callback_data="pb_fmt|2")],
            [InlineKeyboardButton("3 (Minimal)", callback_data="pb_fmt|3"), InlineKeyboardButton("4 (Banner)", callback_data="pb_fmt|4")],
            [InlineKeyboardButton("5 (Focus)", callback_data="pb_fmt|5"), InlineKeyboardButton("6 (VIP)", callback_data="pb_fmt|6")],
            [InlineKeyboardButton("7 (Cinematic)", callback_data="pb_fmt|7"), InlineKeyboardButton("8 (Dark)", callback_data="pb_fmt|8")],
            [InlineKeyboardButton("Both 1 & 2", callback_data="pb_fmt|both")],
            [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
        ]
        await query.edit_message_text("<b>🛠 Story Post Builder</b>\n\nRestarting...", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_FORMAT
        
    elif query.data == "pb_final_send":
        data = context.user_data.get('pb_data', {})
        previews = data.get('cached_previews', [])
        photo_ids = data.get('photo_ids', [])
        dest = data.get('destination')
        
        # Determine targets. Either just the chat, or both current chat and destination.
        targets = [query.message.chat_id]
        if dest:
            targets.append(dest)
            
        for t in targets:
            for p in previews:
                if photo_ids:
                    if len(photo_ids) == 1:
                        try:
                            await context.bot.send_photo(chat_id=t, photo=photo_ids[0], caption=p, parse_mode="HTML")
                        except Exception:
                            await context.bot.send_photo(chat_id=t, photo=photo_ids[0])
                            await context.bot.send_message(chat_id=t, text=p, parse_mode="HTML", disable_web_page_preview=True)
                    else:
                        media_group = [InputMediaPhoto(media=photo_ids[0], caption=p, parse_mode="HTML")]
                        for pid in photo_ids[1:]:
                            media_group.append(InputMediaPhoto(media=pid))
                        await context.bot.send_media_group(chat_id=t, media=media_group)
                else:
                    await context.bot.send_message(chat_id=t, text=p, parse_mode="HTML", disable_web_page_preview=True)
                
        await query.edit_message_text("✅ <b>Post Dispatched Successfully!</b>", parse_mode="HTML")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END

# Error Handler for Conversation
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Process cancelled.", reply_markup=ReplyKeyboardRemove())
    context.user_data.pop('pb_data', None)
    return ConversationHandler.END

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
            CallbackQueryHandler(handle_image_done, pattern="^pb_img_done"),
            MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, handle_image),
            CommandHandler("skip", handle_image)
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
        SELECT_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_destination)],
        CUSTOM_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_destination)],
        CONFIRM_POST: [CallbackQueryHandler(handle_final, pattern="^pb_final_|^pb_cancel")]
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)],
    per_message=False
)
