import os
import re
import html
import asyncio
import io
import time
import requests
import logging
from PIL import Image, ImageFilter

# ── OCR setup ──────────────────────────────────────────────────────────────
OCR_AVAILABLE = False
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
    logging.getLogger(__name__).info("OCR: pytesseract detected and available ✓")
except Exception as _ocr_err:
    logging.getLogger(__name__).warning(f"OCR: pytesseract NOT available — {_ocr_err}")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaDocument, InputMediaPhoto
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

(
    STATE_MODE,
    STATE_EDIT_LINK,
    STATE_FORMAT,
    STATE_NAME,
    STATE_PLATFORM,
    STATE_DESC_MODE,
    STATE_DESC_CHOICE,
    STATE_DESC_ENTER,
    STATE_DESC_OCR,
    STATE_IMG_MODE,
    STATE_IMG_CHOICE,
    STATE_IMG_UPLOAD,
    STATE_GENRE,
    STATE_LINK,
    STATE_EPISODES,
    STATE_STATUS,
    STATE_USERNAME,
    STATE_DESTINATION,
    STATE_CUSTOM_DEST,
    STATE_CONFIRM
) = range(20)

STATUS_EMOJIS = {"Completed": "✅", "Ongoing": "⏳", "RIP": "💀"}
DEFAULT_JOIN_USERNAME = "@StoriesByJeetXNew"
DEFAULT_FORMAT_1_EMOJI = "📖"  # fallback; genre-based set in build_format_1
DEFAULT_FORMAT_1_JOIN_EMOJI = "🦊"

GENRE_EMOJI_MAP = {
    "romance": "❤️", "love": "❤️",
    "thriller": "🔍", "suspense": "🔍",
    "horror": "👻",
    "crime": "🔪",
    "action": "⚔️",
    "comedy": "😂",
    "fantasy": "🧙",
    "mystery": "🕵️",
    "drama": "🎭",
    "school": "🏫",
    "history": "📜",
    "sci-fi": "🚀", "science": "🚀",
}

def _genre_emoji(genre: str) -> str:
    if not genre:
        return "📖"
    lo = genre.lower()
    for k, v in GENRE_EMOJI_MAP.items():
        if k in lo:
            return v
    return "📖"


def is_admin_local(user_id):
    from config import ADMIN_ID, OWNER_ID
    from database import load_config
    if user_id == OWNER_ID or (ADMIN_ID != 0 and user_id == ADMIN_ID): return True
    bot_config = load_config()
    moderators = bot_config.get("moderators", [])
    if str(user_id) in moderators or user_id in moderators: return True
    return False

def build_format_1(data):
    genre = data.get("genre", "")
    emoji = _genre_emoji(genre)
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown") or "Unknown"
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
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    text = f"╔════════════════════╗\n"
    text += f"  <b>{html.escape(name)}</b>\n"
    text += f"╚════════════════════╝\n\n"
    text += f"» <b>Platform:</b> {html.escape(platform)}\n"
    text += f"» <b>Status:</b> {status}\n\n"
    text += f"📥 <b>Get it here:</b>\n{link}"
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
            try: await update.callback_query.answer("⛔ Admin only feature.", show_alert=True)
            except: pass
        else:
            await update.message.reply_text("⛔ Admin only feature.")
        return ConversationHandler.END
        
    if context.user_data.get('pb_data'):
        # Session lock
        if update.callback_query:
            try: await update.callback_query.message.reply_text("⚠️ Previous post creation cancelled.")
            except: pass
        else:
            await update.message.reply_text("⚠️ Previous post creation cancelled.")
            
    context.user_data['pb_data'] = {}
    
    keyboard = [
        [InlineKeyboardButton("New Post", callback_data="pb_m|new"), InlineKeyboardButton("Edit Post", callback_data="pb_m|edit")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    
    text = "<b>🛠 Story Post Builder</b>\n\nWelcome to the Post Builder! Pick a mode:"
    
    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass
        try:
            await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except:
            await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        
    return STATE_MODE

async def handle_post_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Post builder cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    mode = query.data.split("|")[1]
    context.user_data['pb_data']['post_mode'] = mode
    
    if mode == "edit":
        await query.edit_message_text("📝 <b>Edit Post</b>\n\nPaste the Telegram post link to edit:\n(e.g., https://t.me/channel/123)", parse_mode="HTML")
        return STATE_EDIT_LINK
    
    keyboard = [
        [InlineKeyboardButton("Format 1", callback_data="pb_f|1"), InlineKeyboardButton("Format 2", callback_data="pb_f|2")],
        [InlineKeyboardButton("Both 1 & 2", callback_data="pb_f|both")],
        [InlineKeyboardButton("Minimal Clean", callback_data="pb_f|3"), InlineKeyboardButton("Premium Box", callback_data="pb_f|4")],
        [InlineKeyboardButton("Compact Mobile", callback_data="pb_f|5")],
        [InlineKeyboardButton("Cancel", callback_data="pb_cancel")]
    ]
    await query.edit_message_text("<b>🛠 Select Format</b>\n\nPick a format to begin:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_FORMAT

async def handle_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "t.me/" not in text:
        await update.message.reply_text("❌ Invalid link. Please send a valid Telegram post link:")
        return STATE_EDIT_LINK
        
    try:
        parts = text.split("t.me/")[1].split("/")
        if parts[0] == "c":
            chat_username = "-100" + parts[1]
            msg_id = int(parts[2].split("?")[0])
        else:
            chat_username = "@" + parts[0] if not parts[0].startswith("@") else parts[0]
            msg_id = int(parts[1].split("?")[0])
            
        context.user_data['pb_data']['edit_chat_id'] = chat_username
        context.user_data['pb_data']['edit_msg_id'] = msg_id
    except Exception:
        await update.message.reply_text("❌ Could not parse channel and message ID. Try again:")
        return STATE_EDIT_LINK
    
    try:
        # Validate admin access
        admin_member = await context.bot.get_chat_member(chat_username, context.bot.id)
        if not admin_member.can_edit_messages and admin_member.status != "creator":
            await update.message.reply_text("❌ Bot does not seem to have 'Edit Messages' permission in that channel. Try again or fix permissions:")
            return STATE_EDIT_LINK
    except Exception as e:
        pass # Ignore strict check if channel is private and bot can't fetch member easily without ID
        
    keyboard = [
        [InlineKeyboardButton("Format 1", callback_data="pb_f|1"), InlineKeyboardButton("Format 2", callback_data="pb_f|2")],
        [InlineKeyboardButton("Minimal Clean", callback_data="pb_f|3"), InlineKeyboardButton("Premium Box", callback_data="pb_f|4")],
        [InlineKeyboardButton("Compact Mobile", callback_data="pb_f|5")]
    ]
    await update.message.reply_text("✅ Target saved!\n\n<b>🛠 Select Format to Edit TO:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_FORMAT

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Post builder cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    fmt = query.data.split("|")[1]
    context.user_data['pb_data']['format'] = fmt
    await query.edit_message_text("📝 <b>Step 1: Enter Story Name</b>\n\nType the name below:", parse_mode="HTML")
    return STATE_NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['name'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("Pocket FM", callback_data="pb_p|Pocket FM"), InlineKeyboardButton("Kuku FM", callback_data="pb_p|Kuku FM")],
        [InlineKeyboardButton("Headfone", callback_data="pb_p|Headfone"), InlineKeyboardButton("Type Custom", callback_data="pb_p_cust")]
    ]
    await update.message.reply_text("🎵 <b>Step 2: Select Platform</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_PLATFORM

async def handle_platform_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    if query.data == "pb_p_cust":
        await query.edit_message_text("Please type the custom platform name:")
        return STATE_PLATFORM
        
    plat = query.data.split("|")[1]
    context.user_data['pb_data']['platform'] = plat
    return await transition_to_desc(update, context, query)

async def handle_platform_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['platform'] = update.message.text
    return await transition_to_desc(update, context, None)


async def _bg_prefetch_img(context, name: str, platform: str):
    """Prefetch image in background while user fills description."""
    _logger = logging.getLogger(__name__)
    _logger.info(f"[IMG-BG] Prefetch started: {name} / {platform}")
    try:
        from advanced_scraper import extract_hd_image
        img_bytes = await asyncio.wait_for(extract_hd_image(name, platform), timeout=12)
        if img_bytes:
            data = context.user_data.get('pb_data', {})
            if not data.get('img_mode_done'):
                data['temp_img_bytes'] = img_bytes
                _logger.info(f"[IMG-BG] Prefetch success — {len(img_bytes)} bytes cached")
        else:
            _logger.info("[IMG-BG] Prefetch returned no image")
    except Exception as e:
        _logger.warning(f"[IMG-BG] Prefetch failed: {e}")

# Auto description fetch removed — manual/OCR/skip only

async def transition_to_desc(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    _logger = logging.getLogger(__name__)
    msg = query.message if query else update.message
    try:
        if query: await query.message.delete()
    except: pass

    data = context.user_data['pb_data']
    name = data.get('name', '')
    platform = data.get('platform', '')

    # Start image fetch in background immediately so it's ready by the time user reaches image step
    _logger.info(f"[IMG-BG] Starting background image fetch for '{name}' on '{platform}'")
    asyncio.create_task(_bg_prefetch_img(context, name, platform))

    text = "★ <b>Description</b>\n✧ How to add description?"
    keyboard = [
        [InlineKeyboardButton("✎ Manual", callback_data="pb_dm|manual"),
         InlineKeyboardButton("📷 OCR", callback_data="pb_dm|ocr"),
         InlineKeyboardButton("⏭ Skip", callback_data="pb_dm|skip")]
    ]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    data['desc_mode_done'] = True
    return STATE_DESC_MODE

async def handle_desc_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    choice = query.data.split("|")[1]
    context.user_data['pb_data']['desc_mode_done'] = True
    
    if choice == "skip":
        try: await query.message.delete()
        except: pass
        context.user_data['pb_data']['desc'] = ""
        return await transition_to_img(update, context, query)
    elif choice == "manual":
        try: await query.message.delete()
        except: pass
        await query.message.reply_text("✍️ <b>Step 3: Manual Description</b>\n\nEnter the description:", parse_mode="HTML")
        return STATE_DESC_ENTER
    elif choice == "ocr":
        try: await query.message.delete()
        except: pass
        await query.message.reply_text("📸 <b>Upload Screenshot (OCR)</b>\n\nSend the image containing the description:", parse_mode="HTML")
        return STATE_DESC_OCR
    else:
        # Unknown/auto — just re-show options
        return STATE_DESC_MODE

async def handle_desc_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _logger = logging.getLogger(__name__)
    _logger.info("[OCR] handler triggered")
    if not OCR_AVAILABLE:
        _logger.warning("[OCR] pytesseract not available")
        await update.message.reply_text(
            "❌ <b>OCR not installed on server</b>\n\n"
            "Run on your VPS to enable:\n"
            "<code>sudo apt install tesseract-ocr tesseract-ocr-hin -y</code>\n"
            "<code>pip install pytesseract</code>\n\n"
            "✍️ Please enter description manually:",
            parse_mode="HTML"
        )
        return STATE_DESC_ENTER
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Please send an image or document to scan.")
        return STATE_DESC_OCR
    wait_msg = await update.message.reply_text("⏳ <i>Scanning image via OCR...</i>", parse_mode="HTML")
    _logger.info("[OCR] Downloading image...")
    try:
        tg_file = await (update.message.photo[-1].get_file() if update.message.photo else update.message.document.get_file())
        raw_bytes = await tg_file.download_as_bytearray()
        img = Image.open(io.BytesIO(raw_bytes))
        _logger.info(f"[OCR] Image loaded size={img.size}")
        def _do_ocr(image):
            return pytesseract.image_to_string(image, lang="eng+hin")
        extracted = await asyncio.to_thread(_do_ocr, img)
        _logger.info(f"[OCR] Raw extracted length={len(extracted)}")

        # Filter: extract only text after "About ..." section
        story_name = context.user_data.get("pb_data", {}).get("name", "")
        lines_raw = extracted.split("\n")
        about_idx = None
        for idx, ln in enumerate(lines_raw):
            lo = ln.strip().lower()
            if lo.startswith("about") and (not story_name or story_name.lower()[:6] in lo or lo == "about"):
                about_idx = idx
                break
        if about_idx is not None and about_idx + 1 < len(lines_raw):
            _logger.info(f"[OCR] Found 'About' section at line {about_idx} — extracting below")
            lines_raw = lines_raw[about_idx + 1:]
        else:
            _logger.info("[OCR] No 'About' section found — using full OCR text")

        # Clean: skip UI junk keywords
        UI_JUNK = {"play", "resume", "episode", "episodes", "rating", "ratings",
                   "download", "follow", "share", "like", "subscribe", "login",
                   "sign in", "sign up", "register", "comments", "comment"}
        lines = []
        for ln in lines_raw:
            stripped = ln.strip()
            if len(stripped) < 3:
                continue
            lo = stripped.lower()
            if any(lo == junk or lo.startswith(junk+" ") for junk in UI_JUNK):
                continue
            lines.append(stripped)
        cleaned = " ".join(lines).strip()
        await wait_msg.delete()
        if len(cleaned) < 10:
            _logger.warning("[OCR] Extracted text too short")
            await update.message.reply_text("❌ OCR could not find readable text.\n\n✍️ Please enter manually:")
            return STATE_DESC_ENTER
        _logger.info(f"[OCR] Success - {len(cleaned)} chars extracted")
        context.user_data["pb_data"]["temp_found_desc"] = cleaned
        preview = html.escape(cleaned[:800])
        text_out = f"★ <b>Extracted Description</b>\n\n<blockquote>{preview}</blockquote>"
        keyboard = [[InlineKeyboardButton("✅ Use This", callback_data="pb_dc|use"), InlineKeyboardButton("✍️ Edit", callback_data="pb_dc|manual")]]
        await update.message.reply_text(text_out, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return STATE_DESC_CHOICE
    except Exception as e:
        _logger.error(f"[OCR] Exception: {e}", exc_info=True)
        try:
            await wait_msg.edit_text(f"❌ OCR error: {html.escape(str(e))}\n\n✍️ Please enter manually:")
        except Exception:
            pass
        return STATE_DESC_ENTER

async def handle_desc_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    choice = query.data.split("|")[1]
    
    try: await query.message.delete()
    except: pass
    
    if choice == "use":
        context.user_data['pb_data']['desc'] = context.user_data['pb_data'].get('temp_found_desc', '')
        return await transition_to_img(update, context, query)
    elif choice == "short":
        from groq_helper import shorten_description
        wait_msg = await query.message.reply_text("⏳ <i>Generating short version...</i>", parse_mode="HTML")
        original = context.user_data['pb_data'].get('desc_original', '')
        
        try: short = await asyncio.wait_for(shorten_description(original), timeout=4.5)
        except: short = original
        
        context.user_data['pb_data']['desc'] = short
        context.user_data['pb_data']['desc_short'] = short
        try: await wait_msg.delete()
        except: pass
        return await transition_to_img(update, context, query)
    else:
        await query.message.reply_text("✍️ <b>Step 3: Manual Description</b>\n\nType the description manually:", parse_mode="HTML")
        return STATE_DESC_ENTER

async def handle_desc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['desc'] = update.message.text
    return await transition_to_img(update, context, None)

# _bg_fetch_img removed — image is pre-fetched via _bg_prefetch_img in transition_to_desc

async def transition_to_img(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    _logger = logging.getLogger(__name__)
    msg = query.message if query else update.message
    data = context.user_data['pb_data']

    # Check if image was already prefetched in background
    cached = data.get('temp_img_bytes')
    if cached:
        _logger.info(f"[IMG] Prefetched image available ({len(cached)} bytes) — showing immediately")
        try:
            sent = await msg.reply_document(
                document=cached,
                filename=f"{data.get('name','cover')}_cover.jpg",
                caption="★ <b>Cover Image (auto-fetched)</b>\n✧ Use it, or upload a different one.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Use", callback_data="pb_ic|use_direct"),
                    InlineKeyboardButton("🔄 Replace", callback_data="pb_im|manual"),
                    InlineKeyboardButton("⏭ Skip", callback_data="pb_im|skip")
                ]])
            )
        except Exception as e:
            _logger.warning(f"[IMG] Could not send prefetched image: {e}")
            cached = None

    if not cached:
        _logger.info("[IMG] No prefetched image — prompting manual")
        text = "★ <b>Cover Image</b>\n✧ No auto image found. Upload or skip."
        keyboard = [[
            InlineKeyboardButton("📁 Upload", callback_data="pb_im|manual"),
            InlineKeyboardButton("⏭ Skip", callback_data="pb_im|skip")
        ]]
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    data['img_mode_done'] = False
    return STATE_IMG_MODE

async def handle_img_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    choice = query.data.split("|")[1]
    context.user_data['pb_data']['img_mode_done'] = True
    
    try: await query.message.delete()
    except: pass
    
    if choice == "skip":
        context.user_data['pb_data']['photo_ids'] = []
        return await transition_to_genre(update, context, query)
    elif choice == "manual":
        context.user_data['pb_data']['photo_ids'] = []
        await query.message.reply_text("🖼 <b>Step 4: Upload Image(s) / Documents</b>\n\nSend a photo or document.", parse_mode="HTML")
        return STATE_IMG_UPLOAD
    else:
        return STATE_IMG_MODE

async def handle_img_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    choice = query.data.split("|")[1]
    
    try: await query.message.delete()
    except: pass
    
    if choice == "use_direct":
        data = context.user_data['pb_data']
        img_bytes = data.get('temp_img_bytes')
        if img_bytes:
            try:
                sent_msg = await context.bot.send_document(chat_id=query.message.chat_id, document=img_bytes, filename=f"{data.get('name')}_cover.jpg")
                data['temp_found_img'] = {"id": sent_msg.document.file_id, "type": "doc"}
                context.user_data['pb_data']['photo_ids'] = [data['temp_found_img']]
            except Exception: pass
        return await transition_to_genre(update, context, query)
    else:
        context.user_data['pb_data']['photo_ids'] = []
        await query.message.reply_text("🖼 <b>Step 4: Upload Image</b>\nPlease upload manually (or /skip):", parse_mode="HTML")
        return STATE_IMG_UPLOAD

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text == "/skip":
        pass
    else:
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            m_type = "photo"
        elif update.message.document:
            file_id = update.message.document.file_id
            m_type = "doc"
        else:
            await update.message.reply_text("❌ Please send a valid photo or document.")
            return STATE_IMG_UPLOAD
            
        context.user_data['pb_data']['photo_ids'].append({"id": file_id, "type": m_type})
        count = len(context.user_data['pb_data']['photo_ids'])
        if count == 1:
            # Single image: go directly to next step
            return await transition_to_genre(update, context, None)
        else:
            keyboard = [[InlineKeyboardButton("✅ Done", callback_data="pb_idone")]]
            await update.message.reply_text(f"✅ {count} files received. Send more or tap Done.", reply_markup=InlineKeyboardMarkup(keyboard))
            return STATE_IMG_UPLOAD
        
    return await transition_to_genre(update, context, None)

async def handle_image_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    try: await query.message.delete()
    except: pass
    return await transition_to_genre(update, context, query)

async def transition_to_genre(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    keyboard = [
        [InlineKeyboardButton("Romance", callback_data="pb_g|Romance"), InlineKeyboardButton("Thriller", callback_data="pb_g|Thriller")],
        [InlineKeyboardButton("Crime", callback_data="pb_g|Crime"), InlineKeyboardButton("Horror", callback_data="pb_g|Horror")],
        [InlineKeyboardButton("Suspense", callback_data="pb_g|Suspense"), InlineKeyboardButton("Type Custom", callback_data="pb_g_cust")]
    ]
    await msg.reply_text("🎭 <b>Step 5: Select Genre</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_GENRE

async def handle_genre_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    if query.data == "pb_g_cust":
        await query.edit_message_text("Type the custom genre:")
        return STATE_GENRE
    context.user_data['pb_data']['genre'] = query.data.split("|")[1]
    await query.edit_message_text("🔗 <b>Step 6: Link</b>\n\nPaste the story link (or /skip):", parse_mode="HTML")
    return STATE_LINK

async def handle_genre_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['genre'] = update.message.text
    await update.message.reply_text("🔗 <b>Step 6: Link</b>\n\nPaste the story link (or /skip):", parse_mode="HTML")
    return STATE_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['link'] = update.message.text if update.message.text != "/skip" else ""
    await update.message.reply_text("🔢 <b>Step 7: Episodes</b>\n\nEnter total episodes number:", parse_mode="HTML")
    return STATE_EPISODES

async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['episodes'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("Ongoing ⏳", callback_data="pb_s|Ongoing")],
        [InlineKeyboardButton("Completed ✅", callback_data="pb_s|Completed")]
    ]
    await update.message.reply_text("📊 <b>Step 8: Status</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_STATUS

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data['pb_data']['status'] = query.data.split("|")[1]
    
    keyboard = [
        [InlineKeyboardButton("Use Default User", callback_data="pb_u|default")],
        [InlineKeyboardButton("Type Custom User", callback_data="pb_u_cust")]
    ]
    await query.edit_message_text("👤 <b>Step 9: Username</b>\n\nSet the join username.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return STATE_USERNAME

async def handle_username_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    if query.data == "pb_u_cust":
        await query.edit_message_text("Type the custom username (e.g. @MyUser):")
        return STATE_USERNAME
    context.user_data['pb_data']['username'] = DEFAULT_JOIN_USERNAME
    return await transition_to_dest(update, context, query)

async def handle_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pb_data']['username'] = update.message.text
    return await transition_to_dest(update, context, None)

async def transition_to_dest(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    
    if context.user_data['pb_data'].get("post_mode") == "edit":
        # Skip destination if editing existing
        return await generate_preview(msg, context)
        
    from database import load_config
    bot_config = load_config()
    destinations = bot_config.get("post_channels", [])
    
    keyboard = []
    for d in destinations[:5]: keyboard.append([KeyboardButton(str(d))])
    keyboard.append([KeyboardButton("➕ Add New Channel")])
    keyboard.append([KeyboardButton("✖ Skip (Post to this chat)")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    if query:
        try: await query.message.delete()
        except: pass
    await msg.reply_text("📢 <b>Step 10: Destination</b>\n\nWhere to post? Select or Add:", reply_markup=reply_markup, parse_mode="HTML")
    return STATE_DESTINATION

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    if dest == "➕ Add New Channel":
        await update.message.reply_text("Send the Channel Username (e.g. @MyChannel) or ID (e.g. -1001234567):", reply_markup=ReplyKeyboardRemove())
        return STATE_CUSTOM_DEST
        
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
                item = photo_ids[0]
                try:
                    if item["type"] == "doc":
                        await context.bot.send_document(chat_id=message.chat_id, document=item["id"], caption=p, parse_mode="HTML")
                    else:
                        await context.bot.send_photo(chat_id=message.chat_id, photo=item["id"], caption=p, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
            else:
                doc_grp, pho_grp = [], []
                for i, item in enumerate(photo_ids):
                    cap = p if i == 0 else ""
                    if item["type"] == "doc": doc_grp.append(InputMediaDocument(media=item["id"], caption=cap, parse_mode="HTML"))
                    else: pho_grp.append(InputMediaPhoto(media=item["id"], caption=cap, parse_mode="HTML"))
                if doc_grp: await context.bot.send_media_group(chat_id=message.chat_id, media=doc_grp)
                if pho_grp: await context.bot.send_media_group(chat_id=message.chat_id, media=pho_grp)
        else:
            await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)

    keyboard = [
        [InlineKeyboardButton("✅ Confirm & Post", callback_data="pb_final_yes")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pb_cancel")]
    ]
    await message.reply_text("Everything looks good?", reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_CONFIRM

async def handle_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    if query.data == "pb_cancel":
        await query.edit_message_text("Post builder cancelled.")
        context.user_data.pop('pb_data', None)
        return ConversationHandler.END
        
    data = context.user_data['pb_data']
    post_mode = data.get("post_mode", "new")
    dest = data.get("destination")
    edit_chat_id = data.get("edit_chat_id")
    edit_msg_id = data.get("edit_msg_id")
    
    if post_mode == "new" and not dest:
        await query.edit_message_text("❌ No destination found. Cancelled.")
        return ConversationHandler.END
        
    chat_id = edit_chat_id if post_mode == "edit" else dest
    previews = data.get("cached_previews", [])
    photo_ids = data.get("photo_ids", [])
    
    try: await query.message.delete()
    except: pass
    
    working_msg = await query.message.reply_text("⏳ <i>Processing post...</i>", parse_mode="HTML")
    
    from database import add_story
    story_name = data.get("name")
    
    try:
        if post_mode == "edit":
            p = previews[0]
            if photo_ids:
                item = photo_ids[0]
                if item["type"] == "doc":
                    await context.bot.edit_message_media(chat_id=chat_id, message_id=edit_msg_id, media=InputMediaDocument(media=item["id"], caption=p, parse_mode="HTML"))
                else:
                    await context.bot.edit_message_media(chat_id=chat_id, message_id=edit_msg_id, media=InputMediaPhoto(media=item["id"], caption=p, parse_mode="HTML"))
            else:
                try: await context.bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
                except Exception: await context.bot.edit_message_caption(chat_id=chat_id, message_id=edit_msg_id, caption=p, parse_mode="HTML")
            await working_msg.delete()
            
            end_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Create Another", callback_data="menu|createpost")]])
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"✅ <b>Successfully Edited Target Post in {chat_id}!</b>", reply_markup=end_kb, parse_mode="HTML")
            
        else:
            for p in previews:
                if photo_ids:
                    if len(photo_ids) == 1:
                        item = photo_ids[0]
                        if item["type"] == "doc": await context.bot.send_document(chat_id=chat_id, document=item["id"], caption=p, parse_mode="HTML")
                        else: await context.bot.send_photo(chat_id=chat_id, photo=item["id"], caption=p, parse_mode="HTML")
                    else:
                        docs, photos = [], []
                        for i, item in enumerate(photo_ids):
                            if item["type"] == "doc": docs.append(InputMediaDocument(media=item["id"], caption=p if i == 0 else "", parse_mode="HTML"))
                            else: photos.append(InputMediaPhoto(media=item["id"], caption=p if i == 0 else "", parse_mode="HTML"))
                        if docs: await context.bot.send_media_group(chat_id=chat_id, media=docs)
                        if photos: await context.bot.send_media_group(chat_id=chat_id, media=photos)
                else:
                    await context.bot.send_message(chat_id=chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
                    
                try: fmt_num = int(data.get("format")) if str(data.get("format")) != "both" else 1
                except: fmt_num = 1
                
                add_story({
                    "name": story_name, "genre": data.get("genre"), "link": data.get("link"), "episodes": data.get("episodes"), "status": data.get("status"),
                    "description_original": data.get("desc_original", ""), "description_short": data.get("desc_short", ""),
                    "image_url": photo_ids[0]["id"] if photo_ids else "", "extra_images": [item["id"] for item in photo_ids[1:]] if len(photo_ids) > 1 else [],
                    "format_number": fmt_num
                })
                
            await working_msg.delete()
            end_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Create Another", callback_data="menu|createpost")]])
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"✅ <b>Successfully Posted to {chat_id}!</b>", reply_markup=end_kb, parse_mode="HTML")
            
    except Exception as e:
        await working_msg.edit_text(f"❌ <b>Error processing:</b>\n<code>{html.escape(str(e))}</code>", parse_mode="HTML")
        
    context.user_data.pop('pb_data', None)
    return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Process cancelled.", reply_markup=ReplyKeyboardRemove())
    context.user_data.pop('pb_data', None)
    return ConversationHandler.END

post_builder_handler = ConversationHandler(
    allow_reentry=True,
    entry_points=[
        CommandHandler("createpost", start_builder),
        CallbackQueryHandler(start_builder, pattern="^menu\|createpost")
    ],
    states={
        STATE_MODE: [CallbackQueryHandler(handle_post_mode, pattern="^pb_m\||^pb_cancel")],
        STATE_EDIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_link)],
        STATE_FORMAT: [CallbackQueryHandler(handle_format, pattern="^pb_f\||^pb_cancel")],
        STATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        STATE_PLATFORM: [
            CallbackQueryHandler(handle_platform_btn, pattern="^pb_p\||^pb_p_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform_text)
        ],
        STATE_DESC_MODE: [
            CallbackQueryHandler(handle_desc_mode, pattern="^pb_dm\|"),
            CallbackQueryHandler(handle_desc_choice, pattern="^pb_dc\|")
        ],
        STATE_DESC_CHOICE: [CallbackQueryHandler(handle_desc_choice, pattern="^pb_dc\|")],
        STATE_DESC_ENTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_text)],
        STATE_DESC_OCR: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_desc_ocr)],
        STATE_IMG_MODE: [
            CallbackQueryHandler(handle_img_mode, pattern="^pb_im\|"),
            CallbackQueryHandler(handle_img_choice, pattern="^pb_ic\|")
        ],
        STATE_IMG_CHOICE: [CallbackQueryHandler(handle_img_choice, pattern="^pb_ic\|")],
        STATE_IMG_UPLOAD: [
            CallbackQueryHandler(handle_image_done, pattern="^pb_idone"),
            MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.TEXT & ~filters.COMMAND, handle_image),
            CommandHandler("skip", handle_image)
        ],
        STATE_GENRE: [
            CallbackQueryHandler(handle_genre_btn, pattern="^pb_g\||^pb_g_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre_text)
        ],
        STATE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        STATE_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        STATE_STATUS: [CallbackQueryHandler(handle_status, pattern="^pb_s\|")],
        STATE_USERNAME: [
            CallbackQueryHandler(handle_username_btn, pattern="^pb_u\||^pb_u_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username_text)
        ],
        STATE_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_destination)],
        STATE_CUSTOM_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_destination)],
        STATE_CONFIRM: [CallbackQueryHandler(handle_final, pattern="^pb_final_yes|^pb_cancel")]
    },
    fallbacks=[CommandHandler("cancel", cancel_handler), CallbackQueryHandler(cancel_handler, pattern="^pb_cancel")],
    per_message=False
)
