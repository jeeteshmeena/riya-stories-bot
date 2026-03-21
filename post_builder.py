import os
import re
import html
import asyncio
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaPhoto
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

(
    SELECT_FORMAT,
    ENTER_NAME,
    SELECT_PLATFORM,
    HANDLE_DESC_CHOICE,
    ENTER_DESC,
    HANDLE_IMG_CHOICE,
    UPLOAD_IMAGE,
    ENTER_GENRE,
    ENTER_LINK,
    ENTER_EPISODES,
    SELECT_STATUS,
    ENTER_USERNAME,
    SELECT_DESTINATION,
    CUSTOM_DESTINATION,
    CONFIRM_POST
) = range(15)

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

async def _fetch_serper_desc(query: str):
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key: return ""
    payload = {"q": query + " story description site:pocketfm.com OR site:kukufm.com OR site:headfone.co.in", "gl": "in"}
    headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
    try:
        resp = await asyncio.to_thread(requests.post, "https://google.serper.dev/search", json=payload, headers=headers, timeout=10)
        data = resp.json()
        organic = data.get("organic", [])
        for res in organic:
            snip = res.get("snippet", "")
            if snip:
                return re.sub(r'\s+', ' ', snip).strip()
    except: pass
    return ""

async def _fetch_serper_image(query: str):
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key: return None
    payload = {"q": query + " cover image site:pocketfm.com OR site:kukufm.com OR site:headfone.co.in", "gl": "in"}
    headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
    try:
        resp = await asyncio.to_thread(requests.post, "https://google.serper.dev/images", json=payload, headers=headers, timeout=10)
        data = resp.json()
        images = data.get("images", [])
        if images:
            return images[0].get("imageUrl")
    except: pass
    return None


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
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    text = f"✨ <b>{html.escape(name)}</b> ✨\n\n"
    text += f"◃ <b>Genre:</b> {html.escape(genre)}\n"
    text += f"◃ <b>Status:</b> <b>{status}</b>\n\n"
    text += f"🔗 <b>Download / Read:</b>\n{link}"
    return text

def build_format_4(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown")
    desc = data.get("desc", "")
    link = data.get("link", "")
    safe_desc = html.escape(desc)
    desc_block = f"<blockquote expandable><i>{safe_desc}</i></blockquote>\n" if desc else ""
    text = f"╔════════════════════╗\n"
    text += f"  <b>{html.escape(name)}</b>\n"
    text += f"╚════════════════════╝\n\n"
    text += f"» <b>Platform:</b> {html.escape(platform)}\n"
    text += f"» <b>Episodes:</b> {episodes}\n"
    text += f"» <b>Genre:</b> {html.escape(genre)}\n"
    text += f"» <b>Progress:</b> <b>{status}</b>\n\n"
    if desc_block:
        text += f"<b>Synopsis:</b>\n{desc_block}\n"
    text += f"📥 <b>Access Now:</b>\n{link}"
    return text

def build_format_5(data):
    name = data.get("name", "Unknown")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    text = f"📱 <b>{html.escape(name)}</b> ◂ <b>[ {status} • {episodes} ]</b>\n\n"
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
        [InlineKeyboardButton("Format 1", callback_data="pb_fmt|1"), InlineKeyboardButton("Format 2", callback_data="pb_fmt|2")],
        [InlineKeyboardButton("Both 1 & 2", callback_data="pb_fmt|both")],
        [InlineKeyboardButton("Minimal Clean", callback_data="pb_fmt|3"), InlineKeyboardButton("Premium Box", callback_data="pb_fmt|4")],
        [InlineKeyboardButton("Compact Mobile", callback_data="pb_fmt|5")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    
    text = "<b>🛠 Story Post Builder</b>\n\nWelcome to the Post Builder! Pick a format to begin:"
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
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
        [InlineKeyboardButton("Pocket FM", callback_data="pb_plat|Pocket FM"), InlineKeyboardButton("Kuku FM", callback_data="pb_plat|Kuku FM")],
        [InlineKeyboardButton("Headfone", callback_data="pb_plat|Headfone"), InlineKeyboardButton("Type Custom", callback_data="pb_plat_custom")]
    ]
    await update.message.reply_text("🎵 <b>Step 2: Select Platform</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return SELECT_PLATFORM

async def handle_platform_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_plat_custom":
        await query.edit_message_text("Please type the custom platform name:")
        return SELECT_PLATFORM
        
    _, plat = query.data.split("|")
    context.user_data['pb_data']['platform'] = plat
    return await continue_to_desc(update, context, query)

async def handle_platform_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['platform'] = update.message.text
    return await continue_to_desc(update, context)

async def continue_to_desc(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    msg = query.message if query else update.message
    data = context.user_data['pb_data']
    name = data.get('name', '')
    platform = data.get('platform', '')
    
    try:
        if query: await query.message.delete()
    except: pass
    
    wait_msg = await msg.reply_text("⏳ <i>Searching and cleaning description...</i>", parse_mode="HTML")
    desc_found = await _fetch_serper_desc(f"{name} {platform}")
    
    if desc_found:
        from groq_helper import clean_description
        # Store raw purely
        data['desc_original'] = desc_found
        
        # Clean with Groq (Fallback returns original safely)
        cleaned_desc = await clean_description(desc_found)
        data['temp_found_desc'] = cleaned_desc
        
        try: await wait_msg.delete()
        except: pass
        
        text = f"★ <b>Description Found</b>\n✧ Source: {platform}\n\n<blockquote>{html.escape(cleaned_desc)}</blockquote>"
        keyboard = [
            [InlineKeyboardButton("✅ Use This", callback_data="pb_desc|use"), InlineKeyboardButton("📝 Short Version", callback_data="pb_desc|short")],
            [InlineKeyboardButton("✍️ Manual Enter", callback_data="pb_desc|manual")]
        ]
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return HANDLE_DESC_CHOICE
    else:
        try: await wait_msg.delete()
        except: pass
        await msg.reply_text("❌ <b>No description found.</b>\n\n✍️ <b>Step 3: Manual Description</b>\n\nPlease enter the description manually (or /skip):", parse_mode="HTML")
        return ENTER_DESC

async def handle_desc_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("|")[1]
    
    try: await query.message.delete()
    except: pass
    
    if choice == "use":
        context.user_data['pb_data']['desc'] = context.user_data['pb_data']['temp_found_desc']
        return await continue_to_image(update, context, query)
    elif choice == "short":
        desc = context.user_data['pb_data']['temp_found_desc']
        from groq_helper import shorten_description
        
        wait_msg = await query.message.reply_text("⏳ <i>Generating short version...</i>", parse_mode="HTML")
        short_desc = await shorten_description(desc)
        try: await wait_msg.delete()
        except: pass
        
        context.user_data['pb_data']['desc'] = short_desc
        context.user_data['pb_data']['desc_short'] = short_desc
        return await continue_to_image(update, context, query)
    elif choice == "manual":
        await query.message.reply_text("✍️ <b>Step 3: Manual Description</b>\n\nEnter the description:", parse_mode="HTML")
        return ENTER_DESC

async def handle_desc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "/skip":
        context.user_data['pb_data']['desc'] = update.message.text
    else:
        context.user_data['pb_data']['desc'] = ""
    return await continue_to_image(update, context)

async def continue_to_image(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    msg = query.message if query else update.message
    data = context.user_data['pb_data']
    name = data.get('name', '')
    platform = data.get('platform', '')
    
    wait_msg = await msg.reply_text("⏳ <i>Searching for cover image...</i>", parse_mode="HTML")
    img_url = await _fetch_serper_image(f"{name} {platform}")
    try: await wait_msg.delete()
    except: pass
    
    if img_url:
        data['temp_found_img'] = img_url
        text = f"★ <b>Image Found</b>\n✧ Source: {platform}"
        keyboard = [
            [InlineKeyboardButton("✅ Use This", callback_data="pb_imgc|use"), InlineKeyboardButton("🔄 Change / Manual", callback_data="pb_imgc|manual")]
        ]
        try:
            await context.bot.send_photo(chat_id=msg.chat_id, photo=img_url, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return HANDLE_IMG_CHOICE
        except Exception:
            await msg.reply_text("❌ <b>Image found, but failed to load.</b>\n\n🖼 <b>Step 4: Upload Image(s)</b>\nPlease upload manually (or /skip):", parse_mode="HTML")
            context.user_data['pb_data']['photo_ids'] = []
            return UPLOAD_IMAGE
    else:
        await msg.reply_text("❌ <b>No image found.</b>\n\n🖼 <b>Step 4: Upload Image(s)</b>\nPlease upload manually (or /skip):", parse_mode="HTML")
        context.user_data['pb_data']['photo_ids'] = []
        return UPLOAD_IMAGE

async def handle_img_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("|")[1]
    
    try: await query.message.delete()
    except: pass
    
    if choice == "use":
        context.user_data['pb_data']['photo_ids'] = [context.user_data['pb_data']['temp_found_img']]
        keyboard = [
            [InlineKeyboardButton("➡️ Continue to Content Setup", callback_data="pb_img_done"), InlineKeyboardButton("➕ Add More Images", callback_data="pb_img_more")]
        ]
        await query.message.reply_text("✅ Image selected. Click continue, or add more images.", reply_markup=InlineKeyboardMarkup(keyboard))
        return UPLOAD_IMAGE
    elif choice == "manual":
        context.user_data['pb_data']['photo_ids'] = []
        await query.message.reply_text("🖼 <b>Step 4: Upload Image(s)</b>\n\nSend a photo for the post (or multiple).", parse_mode="HTML")
        return UPLOAD_IMAGE

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try: await query.message.delete()
        except: pass
        
        if query.data == "pb_img_done":
            return await continue_to_genre(update, context, query)
        elif query.data == "pb_img_more":
            await query.message.reply_text("Send more photos now, then press '✅ Done Adding Images' when complete.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done Adding Images", callback_data="pb_img_done")]]))
            return UPLOAD_IMAGE
            
    if update.message and update.message.photo:
        context.user_data['pb_data']['photo_ids'].append(update.message.photo[-1].file_id)
        keyboard = [[InlineKeyboardButton("✅ Done Adding Images", callback_data="pb_img_done")]]
        await update.message.reply_text("Image saved! Send more images if needed, or click 'Done'.", reply_markup=InlineKeyboardMarkup(keyboard))
        return UPLOAD_IMAGE
    elif update.message and update.message.text == "/skip":
        return await continue_to_genre(update, context)
    else:
        if update.message:
            await update.message.reply_text("Please send a valid photo, press Done below, or /skip.")
        return UPLOAD_IMAGE

async def continue_to_genre(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    msg = query.message if query else update.message
    keyboard = [
        [InlineKeyboardButton("Crime", callback_data="pb_genre|Crime"), InlineKeyboardButton("Romance", callback_data="pb_genre|Romance")],
        [InlineKeyboardButton("Horror", callback_data="pb_genre|Horror"), InlineKeyboardButton("Thriller", callback_data="pb_genre|Thriller")],
        [InlineKeyboardButton("Type Custom", callback_data="pb_genre_custom")]
    ]
    await msg.reply_text("🎭 <b>Step 5: Select or Type Genre</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ENTER_GENRE

async def handle_genre_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_genre_custom":
        await query.edit_message_text("Please type the custom genre name:")
        return ENTER_GENRE
        
    context.user_data['pb_data']['genre'] = query.data.split("|")[1]
    await query.edit_message_text(f"Genre selected: {context.user_data['pb_data']['genre']}\n\n🔗 <b>Step 6: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_genre_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['genre'] = update.message.text
    await update.message.reply_text("🔗 <b>Step 6: Enter Telegram Link</b>", parse_mode="HTML")
    return ENTER_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['link'] = update.message.text
    await update.message.reply_text("🔢 <b>Step 7: Enter Episodes Count</b>", parse_mode="HTML")
    return ENTER_EPISODES

async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['episodes'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("Completed", callback_data="pb_status|Completed"), InlineKeyboardButton("Ongoing", callback_data="pb_status|Ongoing")],
        [InlineKeyboardButton("RIP", callback_data="pb_status|RIP")]
    ]
    await update.message.reply_text("📶 <b>Step 8: Select Status</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return SELECT_STATUS

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['pb_data']['status'] = query.data.split("|")[1]
    
    try: await query.message.delete()
    except: pass
    
    keyboard = [
        [InlineKeyboardButton(f"Use Default ({DEFAULT_JOIN_USERNAME})", callback_data="pb_user|default")],
        [InlineKeyboardButton("Type Custom Username", callback_data="pb_user_custom")]
    ]
    await query.message.reply_text("👤 <b>Step 9: Confirm Join Username</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ENTER_USERNAME

async def handle_username_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pb_user_custom":
        await query.message.reply_text("Please type the custom username (e.g. @MyChannel):")
        return ENTER_USERNAME
        
    context.user_data['pb_data']['username'] = DEFAULT_JOIN_USERNAME
    try: await query.message.delete()
    except: pass
    return await continue_to_destination(update, context, query)

async def handle_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['username'] = update.message.text
    return await continue_to_destination(update, context)

async def continue_to_destination(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    msg = query.message if query else update.message
    from database import load_config
    bot_config = load_config()
    destinations = bot_config.get("post_channels", [])
    
    keyboard = []
    for d in destinations:
        keyboard.append([KeyboardButton(d)])
    keyboard.append([KeyboardButton("➕ Add New Channel")])
    keyboard.append([KeyboardButton("✖ Skip (Post to this chat)")])
    
    text = "🎯 <b>Step 10: Select Target Channel</b>\nWhere should this be auto-posted upon confirmation?"
    await msg.reply_text(text, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True), parse_mode="HTML")
    return SELECT_DESTINATION

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    if dest == "➕ Add New Channel":
        await update.message.reply_text("Send the Channel Username (e.g. @MyChannel) or ID (e.g. -1001234567):", reply_markup=ReplyKeyboardRemove())
        return CUSTOM_DESTINATION
        
    if dest != "✖ Skip (Post to this chat)":
        context.user_data['pb_data']['destination'] = dest
        
    msg = await update.message.reply_text("Channel configured.", reply_markup=ReplyKeyboardRemove())
    return await generate_preview(msg, context)

async def handle_custom_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    from database import load_config, save_config
    bot_config = load_config()
    destinations = bot_config.get("post_channels", [])
    if dest not in destinations:
        destinations.append(dest)
        bot_config["post_channels"] = destinations
        save_config(bot_config)
        
    context.user_data['pb_data']['destination'] = dest
    msg = await update.message.reply_text(f"✅ Channel {dest} saved for future use.", reply_markup=ReplyKeyboardRemove())
    return await generate_preview(msg, context)

async def generate_preview(message, context: ContextTypes.DEFAULT_TYPE):
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
        
    data['cached_previews'] = previews
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
            
    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Send", callback_data="pb_final_send")],
        [InlineKeyboardButton("🔄 Edit (Restart)", callback_data="pb_final_edit"), InlineKeyboardButton("❌ Cancel", callback_data="pb_cancel")]
    ]
    await message.reply_text("<b>Ready to post?</b>\nYour post is prepared. Select below:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CONFIRM_POST

async def handle_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    elif query.data == "pb_final_edit":
        context.user_data['pb_data'] = {}
        keyboard = [
            [InlineKeyboardButton("Format 1", callback_data="pb_fmt|1"), InlineKeyboardButton("Format 2", callback_data="pb_fmt|2")],
            [InlineKeyboardButton("Both 1 & 2", callback_data="pb_fmt|both")],
            [InlineKeyboardButton("Minimal Clean", callback_data="pb_fmt|3"), InlineKeyboardButton("Premium Box", callback_data="pb_fmt|4")],
            [InlineKeyboardButton("Compact Mobile", callback_data="pb_fmt|5")],
            [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
        ]
        await query.edit_message_text("<b>🛠 Story Post Builder</b>\n\nRestarting...", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return SELECT_FORMAT
        
    elif query.data == "pb_final_send":
        data = context.user_data.get('pb_data', {})
        previews = data.get('cached_previews', [])
        photo_ids = data.get('photo_ids', [])
        dest = data.get('destination')
        
        # Save to DB
        from database import load_db, save_db
        db = load_db()
        story_name = data.get('name')
        if story_name:
            db_entry = db.get(story_name, {})
            db_entry['platform'] = data.get('platform', '')
            db_entry['description_original'] = data.get('desc_original', data.get('desc', ''))
            db_entry['description_short'] = data.get('desc_short', data.get('desc', ''))
            if photo_ids:
                db_entry['image_url'] = photo_ids[0]
                db_entry['extra_images'] = photo_ids[1:] if len(photo_ids) > 1 else []
            db_entry['last_used_template'] = data.get('format', '')
            db_entry['genre'] = data.get('genre', '')
            db_entry['link'] = data.get('link', '')
            db_entry['status'] = data.get('status', '')
            db_entry['episodes'] = data.get('episodes', '')
            db[story_name] = db_entry
            save_db(db)
            
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
                
        await query.edit_message_text("✅ <b>Post Dispatched & Database Updating Successfully!</b>", parse_mode="HTML")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END

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
        SELECT_PLATFORM: [
            CallbackQueryHandler(handle_platform_btn, pattern="^pb_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform_text)
        ],
        HANDLE_DESC_CHOICE: [CallbackQueryHandler(handle_desc_choice, pattern="^pb_desc\|")],
        ENTER_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_text)],
        HANDLE_IMG_CHOICE: [CallbackQueryHandler(handle_img_choice, pattern="^pb_imgc\|")],
        UPLOAD_IMAGE: [
            CallbackQueryHandler(handle_image, pattern="^pb_img_done|^pb_img_more"),
            MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, handle_image),
            CommandHandler("skip", handle_image)
        ],
        ENTER_GENRE: [
            CallbackQueryHandler(handle_genre_btn, pattern="^pb_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre_text)
        ],
        ENTER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        ENTER_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        SELECT_STATUS: [CallbackQueryHandler(handle_status, pattern="^pb_")],
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
