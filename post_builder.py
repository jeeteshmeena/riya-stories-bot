import os
import re
import html
import asyncio
import io
import logging
import requests
from PIL import Image, ImageFilter
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaDocument, InputMediaPhoto
)
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

_log = logging.getLogger(__name__)

# ── OCR setup ──────────────────────────────────────────────────────────────
OCR_AVAILABLE = False
try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
    _log.info("OCR: pytesseract ✓")
except Exception as _ocr_err:
    _log.warning(f"OCR: pytesseract NOT available — {_ocr_err}")

# ── States ──────────────────────────────────────────────────────────────────
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
    STATE_CONFIRM,
) = range(20)

# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_JOIN_USERNAME = "@StoriesByJeetXNew"
DEFAULT_FORMAT_1_JOIN_EMOJI = "🦊"

GENRE_EMOJI_MAP = {
    "romance": "❤️", "love": "❤️",
    "thriller": "🔥", "suspense": "🔥",
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

OCR_STOP_WORDS = {"episode", "episodes", "resume", "play", "rating", "ratings",
                  "download", "follow", "share", "like", "subscribe",
                  "login", "sign in", "sign up", "register"}

# ── Helpers ─────────────────────────────────────────────────────────────────
def _genre_emoji(genre: str) -> str:
    if not genre:
        return "📖"
    lo = genre.lower()
    for k, v in GENRE_EMOJI_MAP.items():
        if k in lo:
            return v
    return "📖"

def is_admin_local(user_id: int) -> bool:
    from config import ADMIN_ID, OWNER_ID
    from database import load_config
    if user_id == OWNER_ID or (ADMIN_ID != 0 and user_id == ADMIN_ID):
        return True
    cfg = load_config()
    mods = cfg.get("moderators", [])
    return str(user_id) in mods or user_id in mods

def _ocr_extract(raw_text: str, story_name: str, platform: str = "") -> str:
    """Clean symbols, fix spaces, keep full text, and intelligently merge broken lines."""
    import re
    
    # 1. Remove weird garbage symbols
    text = re.sub(r'[€॥\[\]/|\\<>{}]', '', raw_text)
    
    # 2. Light polish for common mistakes
    fixes = {
        r"\bbusiness\b": "Business",
        r"\bfun\b": "Fun",
        r"\bfeelings\b": "Feelings"
    }
    for old, new in fixes.items():
        text = re.sub(old, new, text, flags=re.IGNORECASE)

    # 3. Smart paragraph merging
    # Split by lines
    lines = text.split("\n")
    paras = []
    current_para = []
    
    for ln in lines:
        s = re.sub(r' +', ' ', ln.strip())
        if not s:
            # Empty line = true paragraph break based on OCR gap
            if current_para:
                # To format them beautifully, we can just join wrapped lines with space
                paras.append(" ".join(current_para))
                current_para = []
        else:
            current_para.append(s)
            
    if current_para:
        paras.append(" ".join(current_para))
        
    # Finally, to respect user example "रोहन रोय... है।\nकम उम्र...":
    # Let's clean the joined paragraphs to ensure proper line breaks after double danda or full stop
    # but since we stripped double danda (॥), we rely on single danda (।) and full stops.
    final_text = "\n\n".join(paras)
    # Automatically drop a single newline after a Hindi danda (।) or full stop (.) 
    # ONLY IF the next character is not already a newline or space-newline.
    final_text = re.sub(r'([।?!]) (?=[A-Za-z0-9ऀ-ॿ])', r'\1\n', final_text)
    
    return final_text

# ── Format builders ──────────────────────────────────────────────────────────
def build_format_1(data: dict) -> str:
    genre = data.get("genre", "") or ""
    emoji = _genre_emoji(genre)
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre_label = genre or "Unknown"
    link = data.get("link", "")
    username = data.get("username", DEFAULT_JOIN_USERNAME)
    t = f"{emoji} <b>Name :-</b>  <b>{html.escape(name)}</b> ( <b>{status}</b> )\n\n"
    t += f"<b>Story Type :-</b> {html.escape(genre_label)}\n\n"
    t += f"<b>Link 🖇:-</b> {link}\n\n"
    t += f"{DEFAULT_FORMAT_1_JOIN_EMOJI}<b>JOIN FOR ALL EPISODES.</b>\n\nJoin - {username}"
    return t

def build_format_2(data: dict) -> str:
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    desc = data.get("desc", "")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    availability = data.get("availability", "All Stories Available on Stories🫶🏻.")
    desc_block = f"<blockquote expandable><i>{html.escape(desc)}</i></blockquote>\n\n" if desc else ""
    t = f"<b>{html.escape(name)} • {html.escape(platform)}</b>\n\n"
    t += desc_block
    t += f"<b>Episodes -</b> <b>{episodes}</b>\n"
    t += f"<b>Status -</b> <b>{status}</b>\n\n"
    t += f"{html.escape(availability)}\n\n{link}\n{link}"
    return t

def build_format_3(data: dict) -> str:
    name = data.get("name", "Unknown")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    t = f"✨ <b>{html.escape(name)}</b> ✨\n\n"
    t += f"◃ <b>Genre:</b> {html.escape(genre)}\n◃ <b>Status:</b> <b>{status}</b>\n\n"
    t += f"🔗 <b>Download / Read:</b>\n{link}"
    return t

def build_format_4(data: dict) -> str:
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    t = "╔════════════════════╗\n"
    t += f"  <b>{html.escape(name)}</b>\n╚════════════════════╝\n\n"
    t += f"» <b>Platform:</b> {html.escape(platform)}\n» <b>Status:</b> {status}\n\n"
    t += f"📥 <b>Get it here:</b>\n{link}"
    return t

def build_format_5(data: dict) -> str:
    name = data.get("name", "Unknown")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    return f"📱 <b>{html.escape(name)}</b> ◂ <b>[ {status} • {episodes} ]</b>\n\n{link}"

def _build_previews(data: dict) -> list:
    fmt = data.get("format", "1")
    fmts = [fmt] if fmt != "both" else ["1", "2"]
    builders = {"1": build_format_1, "2": build_format_2, "3": build_format_3,
                "4": build_format_4, "5": build_format_5}
    return [builders[f](data) for f in fmts if f in builders]

# ── Background image prefetch ─────────────────────────────────────────────────
async def _bg_prefetch_img(context: ContextTypes.DEFAULT_TYPE, name: str, platform: str):
    """Start fetching image immediately when name+platform known. Timeout 12s."""
    _log.info(f"[IMG-BG] Starting prefetch: '{name}' / '{platform}'")
    try:
        from advanced_scraper import extract_hd_image
        # User requested max 3s internal timeout, setting background limit slightly higher to 4s
        img_bytes = await asyncio.wait_for(extract_hd_image(name, platform), timeout=4.0)
        data = context.user_data.get("pb_data", {})
        if img_bytes and not data.get("img_mode_done"):
            data["temp_img_bytes"] = img_bytes
            _log.info(f"[IMG-BG] Cached {len(img_bytes)} bytes")
        else:
            _log.info("[IMG-BG] No image found or state already done")
    except Exception as e:
        _log.warning(f"[IMG-BG] Prefetch failed: {e}")

# ── Entry: start_builder ─────────────────────────────────────────────────────
async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin_local(user.id):
        msg = "⛔ Admin only."
        if update.callback_query:
            try: await update.callback_query.answer(msg, show_alert=True)
            except: pass
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    # Cancel existing session cleanly
    if context.user_data.get("pb_data") is not None:
        note = "⚠️ Previous session cancelled."
        if update.callback_query:
            try: await update.callback_query.message.reply_text(note)
            except: pass
        else:
            try: await update.message.reply_text(note)
            except: pass

    context.user_data["pb_data"] = {}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("New Post", callback_data="pb_m|new"),
         InlineKeyboardButton("Edit Post", callback_data="pb_m|edit")],
        [InlineKeyboardButton("✖ Cancel", callback_data="pb_cancel")]
    ])
    text = "<b>🛠 Post Builder</b>\n\nChoose a mode:"

    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass
        try:
            await update.callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except:
            await update.callback_query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    return STATE_MODE

# ── Mode selection ─────────────────────────────────────────────────────────────
async def handle_post_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END

    mode = query.data.split("|")[1]
    context.user_data["pb_data"]["post_mode"] = mode

    if mode == "edit":
        await query.edit_message_text(
            "📝 <b>Edit Post</b>\n\nPaste the Telegram post link:\n<i>(e.g. https://t.me/channel/123)</i>",
            parse_mode="HTML"
        )
        return STATE_EDIT_LINK

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Format 1", callback_data="pb_f|1"),
         InlineKeyboardButton("Format 2", callback_data="pb_f|2")],
        [InlineKeyboardButton("Both 1 & 2", callback_data="pb_f|both")],
        [InlineKeyboardButton("Minimal", callback_data="pb_f|3"),
         InlineKeyboardButton("Box", callback_data="pb_f|4"),
         InlineKeyboardButton("Compact", callback_data="pb_f|5")],
        [InlineKeyboardButton("✖ Cancel", callback_data="pb_cancel")]
    ])
    await query.edit_message_text("<b>Select Format:</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_FORMAT

# ── Edit link ─────────────────────────────────────────────────────────────────
async def handle_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "t.me/" not in text:
        await update.message.reply_text("❌ Invalid link. Send a Telegram post link:")
        return STATE_EDIT_LINK
    try:
        parts = text.split("t.me/")[1].split("/")
        if parts[0] == "c":
            chat_id = "-100" + parts[1]
            msg_id = int(parts[2].split("?")[0])
        else:
            chat_id = ("@" + parts[0]) if not parts[0].startswith("@") else parts[0]
            msg_id = int(parts[1].split("?")[0])
        context.user_data["pb_data"]["edit_chat_id"] = chat_id
        context.user_data["pb_data"]["edit_msg_id"] = msg_id
    except Exception:
        await update.message.reply_text("❌ Could not parse link. Try again:")
        return STATE_EDIT_LINK

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Format 1", callback_data="pb_f|1"),
         InlineKeyboardButton("Format 2", callback_data="pb_f|2")],
        [InlineKeyboardButton("Minimal", callback_data="pb_f|3"),
         InlineKeyboardButton("Box", callback_data="pb_f|4"),
         InlineKeyboardButton("Compact", callback_data="pb_f|5")]
    ])
    await update.message.reply_text(
        "✅ Target saved!\n\n<b>Select Format to apply:</b>",
        reply_markup=kb, parse_mode="HTML"
    )
    return STATE_FORMAT

# ── Format ────────────────────────────────────────────────────────────────────
async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END

    context.user_data["pb_data"]["format"] = query.data.split("|")[1]
    await query.edit_message_text("✏️ <b>Enter Story Name:</b>", parse_mode="HTML")
    return STATE_NAME

# ── Name ──────────────────────────────────────────────────────────────────────
async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["name"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Pocket FM", callback_data="pb_p|Pocket FM"),
         InlineKeyboardButton("Kuku FM", callback_data="pb_p|Kuku FM")],
        [InlineKeyboardButton("Headfone", callback_data="pb_p|Headfone"),
         InlineKeyboardButton("✎ Custom", callback_data="pb_p_cust")]
    ])
    await update.message.reply_text("🎵 <b>Select Platform:</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_PLATFORM

# ── Platform ──────────────────────────────────────────────────────────────────
async def handle_platform_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    if query.data == "pb_p_cust":
        await query.edit_message_text("Type the platform name:")
        return STATE_PLATFORM

    context.user_data["pb_data"]["platform"] = query.data.split("|")[1]
    return await _transition_to_desc(update, context, query)

async def handle_platform_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["platform"] = update.message.text.strip()
    return await _transition_to_desc(update, context, None)

# ── Description step (no auto-fetch) ─────────────────────────────────────────
async def _transition_to_desc(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    try:
        if query: await query.message.delete()
    except: pass

    data = context.user_data["pb_data"]
    name = data.get("name", "")
    platform = data.get("platform", "")

    # Kick off background image prefetch immediately
    asyncio.create_task(_bg_prefetch_img(context, name, platform))
    _log.info(f"[DESC] Showing description options for '{name}'")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✎", callback_data="pb_dm|manual"),
        InlineKeyboardButton("📷", callback_data="pb_dm|ocr"),
        InlineKeyboardButton("⏭", callback_data="pb_dm|skip"),
    ]])
    await msg.reply_text(
        "★ <b>Description</b>\n✎ Manual  │  📷 OCR  │  ⏭ Skip",
        reply_markup=kb, parse_mode="HTML"
    )
    data["desc_mode_done"] = False
    return STATE_DESC_MODE

async def handle_desc_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    choice = query.data.split("|")[1]
    context.user_data["pb_data"]["desc_mode_done"] = True

    try: await query.message.delete()
    except: pass

    if choice == "skip":
        context.user_data["pb_data"]["desc"] = ""
        return await _transition_to_img(update, context, query)
    elif choice == "manual":
        await query.message.reply_text("✍️ <b>Enter Description:</b>\n\nType or paste it below:", parse_mode="HTML")
        return STATE_DESC_ENTER
    elif choice == "ocr":
        if not OCR_AVAILABLE:
            await query.message.reply_text(
                "❌ <b>OCR not available on server</b>\n\n"
                "Install on VPS:\n"
                "<code>sudo apt install tesseract-ocr tesseract-ocr-hin -y</code>\n"
                "<code>pip install pytesseract</code>\n\n"
                "✍️ Please enter manually instead:",
                parse_mode="HTML"
            )
            return STATE_DESC_ENTER
        await query.message.reply_text("📷 <b>Send screenshot containing the description:</b>", parse_mode="HTML")
        return STATE_DESC_OCR
    return STATE_DESC_MODE

# ── OCR Handler ───────────────────────────────────────────────────────────────
async def handle_desc_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _log.info("[OCR] Triggered")
    if not OCR_AVAILABLE:
        await update.message.reply_text(
            "❌ <b>OCR not available</b>\n\n"
            "Install on VPS:\n"
            "<code>sudo apt install tesseract-ocr tesseract-ocr-hin -y</code>\n"
            "<code>pip install pytesseract</code>\n\n"
            "✍️ Enter description manually:",
            parse_mode="HTML"
        )
        return STATE_DESC_ENTER

    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Send an image or document.")
        return STATE_DESC_OCR

    wait = await update.message.reply_text("⏳ <i>Scanning via OCR...</i>", parse_mode="HTML")
    try:
        tg_file = (
            await update.message.photo[-1].get_file()
            if update.message.photo
            else await update.message.document.get_file()
        )
        raw = await tg_file.download_as_bytearray()
        img = Image.open(io.BytesIO(raw))
        _log.info(f"[OCR] Original image size={img.size}")

        def _run_ocr(image):
            from PIL import ImageEnhance, ImageFilter
            # 1. Grayscale
            image = image.convert("L")
            # 2. Increase contrast 2.0x
            image = ImageEnhance.Contrast(image).enhance(2.0)
            # 3. Sharpen
            image = image.filter(ImageFilter.SHARPEN)
            # 4. Resize 1.5x using LANCZOS
            w, h = image.size
            image = image.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
            _log.info(f"[OCR] Enhanced image size={image.size}")
            
            return pytesseract.image_to_string(
                image, 
                lang="eng+hin", 
                config="--oem 3 --psm 6"
            )

        raw_text = await asyncio.to_thread(_run_ocr, img)
        _log.info(f"[OCR] Raw text length={len(raw_text)}")

        cleaned = _ocr_extract(raw_text, "", "")
        _log.info(f"[OCR] Cleaned length={len(cleaned)}")

        await wait.delete()

        if len(cleaned) < 10:
            _log.warning("[OCR] Too short — prompting manual")
            await update.message.reply_text("❌ Try clearer or cropped image\n\n✍️ Enter manually:")
            return STATE_DESC_ENTER

        context.user_data["pb_data"]["temp_found_desc"] = cleaned
        preview = html.escape(cleaned)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅", callback_data="pb_dc|use"),
            InlineKeyboardButton("✎", callback_data="pb_dc|manual"),
            InlineKeyboardButton("🔁", callback_data="pb_dc|retry"),
        ]])
        await update.message.reply_text(
            f"★ <b>Extracted:</b>\n\n<blockquote>{preview}</blockquote>",
            reply_markup=kb, parse_mode="HTML"
        )
        return STATE_DESC_CHOICE

    except Exception as e:
        _log.error(f"[OCR] Error: {e}", exc_info=True)
        try:
            await wait.edit_text(f"❌ OCR failed: {html.escape(str(e))}\n\n✍️ Enter manually:")
        except: pass
        return STATE_DESC_ENTER

async def handle_desc_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    choice = query.data.split("|")[1]
    try: await query.message.delete()
    except: pass

    if choice == "use":
        context.user_data["pb_data"]["desc"] = context.user_data["pb_data"].get("temp_found_desc", "")
        return await _transition_to_img(update, context, query)
    elif choice == "retry":
        await query.message.reply_text("📷 <b>Send another screenshot:</b>", parse_mode="HTML")
        return STATE_DESC_OCR
    else:  # manual
        await query.message.reply_text("✍️ <b>Enter Description:</b>", parse_mode="HTML")
        return STATE_DESC_ENTER

async def handle_desc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["desc"] = update.message.text.strip()
    return await _transition_to_img(update, context, None)

# ── Image step ────────────────────────────────────────────────────────────────
async def _transition_to_img(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    data = context.user_data["pb_data"]

    cached = data.get("temp_img_bytes")
    if cached:
        _log.info(f"[IMG] Prefetched image available ({len(cached)} bytes)")
        try:
            await msg.reply_document(
                document=cached,
                filename=f"{data.get('name','cover')}_cover.jpg",
                caption="★ <b>Auto-fetched Cover Image</b>\n✧ Use it or replace it.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Use Preview", callback_data="pb_ic|use_direct"),
                    InlineKeyboardButton("📤 Upload", callback_data="pb_im|manual"),
                    InlineKeyboardButton("⏭ Skip", callback_data="pb_im|skip"),
                ]])
            )
            data["img_mode_done"] = False
            return STATE_IMG_MODE
        except Exception as e:
            _log.warning(f"[IMG] Failed to send prefetched image: {e}")

    # No cached image
    _log.info("[IMG] No prefetched image — asking user")
    await msg.reply_text(
        "★ <b>Cover Image</b>\n✧ Upload an image or skip.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Upload", callback_data="pb_im|manual"),
            InlineKeyboardButton("⏭ Skip", callback_data="pb_im|skip"),
        ]]),
        parse_mode="HTML"
    )
    data["img_mode_done"] = False
    return STATE_IMG_MODE

async def handle_img_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    choice = query.data.split("|")[1]
    context.user_data["pb_data"]["img_mode_done"] = True

    try: await query.message.delete()
    except: pass

    if choice == "skip":
        context.user_data["pb_data"].setdefault("photo_ids", [])
        return await _transition_to_genre(update, context, query)
    elif choice == "manual":
        context.user_data["pb_data"]["photo_ids"] = []
        await query.message.reply_text("🖼 <b>Upload image/document:</b>", parse_mode="HTML")
        return STATE_IMG_UPLOAD
    return STATE_IMG_MODE

async def handle_img_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    choice = query.data.split("|")[1]
    try: await query.message.delete()
    except: pass
    data = context.user_data["pb_data"]

    if choice == "use_direct":
        img_bytes = data.get("temp_img_bytes")
        if img_bytes:
            try:
                sent = await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=img_bytes,
                    filename=f"{data.get('name','cover')}_cover.jpg"
                )
                data["photo_ids"] = [{"id": sent.document.file_id, "type": "doc"}]
            except Exception as e:
                _log.error(f"[IMG] use_direct failed: {e}")
        data["img_mode_done"] = True
        return await _transition_to_genre(update, context, query)
    else:
        data["photo_ids"] = []
        await query.message.reply_text("🖼 <b>Upload image/document:</b>", parse_mode="HTML")
        return STATE_IMG_UPLOAD

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    if update.message.text == "/skip":
        data.setdefault("photo_ids", [])
        return await _transition_to_genre(update, context, None)

    if update.message.photo:
        fid = update.message.photo[-1].file_id
        mtype = "photo"
    elif update.message.document:
        fid = update.message.document.file_id
        mtype = "doc"
    else:
        await update.message.reply_text("❌ Send a photo or document.")
        return STATE_IMG_UPLOAD

    data.setdefault("photo_ids", []).append({"id": fid, "type": mtype})
    count = len(data["photo_ids"])

    # Immediately proceed to next step (genre) on ANY file upload
    return await _transition_to_genre(update, context, None)

async def handle_image_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    try: await query.message.delete()
    except: pass
    return await _transition_to_genre(update, context, query)

# ── Genre ─────────────────────────────────────────────────────────────────────
async def _transition_to_genre(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Romance", callback_data="pb_g|Romance"),
         InlineKeyboardButton("🔥 Thriller", callback_data="pb_g|Thriller")],
        [InlineKeyboardButton("🔪 Crime", callback_data="pb_g|Crime"),
         InlineKeyboardButton("👻 Horror", callback_data="pb_g|Horror")],
        [InlineKeyboardButton("⚔️ Action", callback_data="pb_g|Action"),
         InlineKeyboardButton("✎ Custom", callback_data="pb_g_cust")],
    ])
    await msg.reply_text("🎭 <b>Select Genre:</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_GENRE

async def handle_genre_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    if query.data == "pb_g_cust":
        await query.edit_message_text("Type the genre:")
        return STATE_GENRE
    context.user_data["pb_data"]["genre"] = query.data.split("|")[1]
    await query.edit_message_text("🔗 <b>Story Link</b>\n\nPaste the link (or type /skip):", parse_mode="HTML")
    return STATE_LINK

async def handle_genre_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["genre"] = update.message.text.strip()
    await update.message.reply_text("🔗 <b>Story Link</b>\n\nPaste the link (or /skip):", parse_mode="HTML")
    return STATE_LINK

# ── Link ──────────────────────────────────────────────────────────────────────
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    context.user_data["pb_data"]["link"] = "" if t == "/skip" else t
    await update.message.reply_text("🔢 <b>Total Episodes:</b> (number or /skip)", parse_mode="HTML")
    return STATE_EPISODES

# ── Episodes ──────────────────────────────────────────────────────────────────
async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    context.user_data["pb_data"]["episodes"] = "" if t == "/skip" else t
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Ongoing", callback_data="pb_s|Ongoing"),
         InlineKeyboardButton("✅ Completed", callback_data="pb_s|Completed")]
    ])
    await update.message.reply_text("📊 <b>Status:</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_STATUS

# ── Status ────────────────────────────────────────────────────────────────────
async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data["pb_data"]["status"] = query.data.split("|")[1]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Default", callback_data="pb_u|default"),
         InlineKeyboardButton("✎ Custom", callback_data="pb_u_cust")]
    ])
    await query.edit_message_text("👤 <b>Join Username:</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_USERNAME

# ── Username ──────────────────────────────────────────────────────────────────
async def handle_username_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    if query.data == "pb_u_cust":
        await query.edit_message_text("Type the username (e.g. @MyUser):")
        return STATE_USERNAME
    context.user_data["pb_data"]["username"] = DEFAULT_JOIN_USERNAME
    return await _transition_to_dest(update, context, query)

async def handle_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["username"] = update.message.text.strip()
    return await _transition_to_dest(update, context, None)

# ── Destination ───────────────────────────────────────────────────────────────
async def _transition_to_dest(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    msg = query.message if query else update.message
    data = context.user_data["pb_data"]

    if data.get("post_mode") == "edit":
        return await _generate_preview(msg, context)

    from database import load_config
    cfg = load_config()
    destinations = cfg.get("post_channels", [])

    btns = [[KeyboardButton(str(d))] for d in destinations[:5]]
    btns.append([KeyboardButton("➕ Add Channel")])
    btns.append([KeyboardButton("✖ This Chat")])

    if query:
        try: await query.message.delete()
        except: pass

    await msg.reply_text(
        "📢 <b>Destination</b>\n\nWhere to post?",
        reply_markup=ReplyKeyboardMarkup(btns, resize_keyboard=True, one_time_keyboard=True),
        parse_mode="HTML"
    )
    return STATE_DESTINATION

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip()
    if dest == "➕ Add Channel":
        await update.message.reply_text(
            "Send channel username (e.g. @MyChannel) or ID:",
            reply_markup=ReplyKeyboardRemove()
        )
        return STATE_CUSTOM_DEST

    if dest != "✖ This Chat":
        context.user_data["pb_data"]["destination"] = dest

    msg = await update.message.reply_text("Channel set.", reply_markup=ReplyKeyboardRemove())
    return await _generate_preview(msg, context)

async def handle_custom_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip()
    from database import load_config, save_config
    cfg = load_config()
    dests = cfg.get("post_channels", [])
    if dest not in dests:
        dests.append(dest)
        cfg["post_channels"] = dests
        save_config(cfg)
    context.user_data["pb_data"]["destination"] = dest
    msg = await update.message.reply_text(f"✅ {dest} saved.", reply_markup=ReplyKeyboardRemove())
    return await _generate_preview(msg, context)

# ── Preview ───────────────────────────────────────────────────────────────────
async def _generate_preview(message, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("pb_data", {})
    previews = _build_previews(data)
    data["cached_previews"] = previews
    photo_ids = data.get("photo_ids", [])

    try:
        await message.reply_text("🔎 <b>Preview:</b>", parse_mode="HTML")
    except: pass

    for p in previews:
        try:
            if photo_ids:
                if len(photo_ids) == 1:
                    item = photo_ids[0]
                    if item["type"] == "doc":
                        await context.bot.send_document(
                            chat_id=message.chat_id, document=item["id"],
                            caption=p, parse_mode="HTML"
                        )
                    else:
                        await context.bot.send_photo(
                            chat_id=message.chat_id, photo=item["id"],
                            caption=p, parse_mode="HTML"
                        )
                else:
                    grp = []
                    for i, item in enumerate(photo_ids):
                        cap = p if i == 0 else ""
                        if item["type"] == "doc":
                            grp.append(InputMediaDocument(media=item["id"], caption=cap, parse_mode="HTML"))
                        else:
                            grp.append(InputMediaPhoto(media=item["id"], caption=cap, parse_mode="HTML"))
                    await context.bot.send_media_group(chat_id=message.chat_id, media=grp)
            else:
                await context.bot.send_message(
                    chat_id=message.chat_id, text=p,
                    parse_mode="HTML", disable_web_page_preview=True
                )
        except Exception as e:
            _log.error(f"[PREVIEW] Send failed: {e}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Post", callback_data="pb_final_yes"),
         InlineKeyboardButton("✖ Cancel", callback_data="pb_cancel")]
    ])
    await message.reply_text("Post this?", reply_markup=kb)
    return STATE_CONFIRM

# ── Final post ────────────────────────────────────────────────────────────────
async def handle_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass

    if query.data == "pb_cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END

    data = context.user_data["pb_data"]
    post_mode = data.get("post_mode", "new")
    dest = data.get("destination")
    edit_chat_id = data.get("edit_chat_id")
    edit_msg_id = data.get("edit_msg_id")

    if post_mode == "new" and not dest:
        await query.edit_message_text("❌ No destination. Cancelled.")
        return ConversationHandler.END

    chat_id = edit_chat_id if post_mode == "edit" else dest
    previews = data.get("cached_previews", [])
    photo_ids = data.get("photo_ids", [])

    try: await query.message.delete()
    except: pass

    working = await query.message.reply_text("⏳ <i>Processing...</i>", parse_mode="HTML")

    try:
        if post_mode == "edit":
            p = previews[0] if previews else ""
            if photo_ids:
                item = photo_ids[0]
                media = (
                    InputMediaDocument(media=item["id"], caption=p, parse_mode="HTML")
                    if item["type"] == "doc"
                    else InputMediaPhoto(media=item["id"], caption=p, parse_mode="HTML")
                )
                await asyncio.wait_for(
                    context.bot.edit_message_media(chat_id=chat_id, message_id=edit_msg_id, media=media),
                    timeout=10
                )
            else:
                try:
                    await asyncio.wait_for(
                        context.bot.edit_message_text(
                            chat_id=chat_id, message_id=edit_msg_id,
                            text=p, parse_mode="HTML", disable_web_page_preview=True
                        ),
                        timeout=10
                    )
                except Exception:
                    await asyncio.wait_for(
                        context.bot.edit_message_caption(
                            chat_id=chat_id, message_id=edit_msg_id,
                            caption=p, parse_mode="HTML"
                        ),
                        timeout=10
                    )

            await working.delete()
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁", callback_data="menu|createpost")]])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ <b>Edited {chat_id}</b>",
                reply_markup=kb, parse_mode="HTML"
            )

        else:  # new post
            from database import add_story
            story_name = data.get("name")

            for p in previews:
                try:
                    if photo_ids:
                        if len(photo_ids) == 1:
                            item = photo_ids[0]
                            if item["type"] == "doc":
                                await asyncio.wait_for(
                                    context.bot.send_document(chat_id=chat_id, document=item["id"], caption=p, parse_mode="HTML"),
                                    timeout=10
                                )
                            else:
                                await asyncio.wait_for(
                                    context.bot.send_photo(chat_id=chat_id, photo=item["id"], caption=p, parse_mode="HTML"),
                                    timeout=10
                                )
                        else:
                            grp = []
                            for i, item in enumerate(photo_ids):
                                c = p if i == 0 else ""
                                if item["type"] == "doc":
                                    grp.append(InputMediaDocument(media=item["id"], caption=c, parse_mode="HTML"))
                                else:
                                    grp.append(InputMediaPhoto(media=item["id"], caption=c, parse_mode="HTML"))
                            await asyncio.wait_for(
                                context.bot.send_media_group(chat_id=chat_id, media=grp),
                                timeout=10
                            )
                    else:
                        await asyncio.wait_for(
                            context.bot.send_message(chat_id=chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True),
                            timeout=10
                        )
                except Exception as send_err:
                    _log.error(f"[POST] Send error: {send_err}")
                    await working.edit_text(f"❌ <b>Send failed:</b>\n<code>{html.escape(str(send_err))}</code>", parse_mode="HTML")
                    context.user_data.pop("pb_data", None)
                    return ConversationHandler.END

            try:
                fmt_num = int(data.get("format")) if str(data.get("format")) != "both" else 1
            except: fmt_num = 1

            try:
                add_story({
                    "name": story_name,
                    "genre": data.get("genre"),
                    "link": data.get("link"),
                    "episodes": data.get("episodes"),
                    "status": data.get("status"),
                    "description_original": data.get("desc", ""),
                    "image_url": photo_ids[0]["id"] if photo_ids else "",
                    "extra_images": [x["id"] for x in photo_ids[1:]],
                    "format_number": fmt_num,
                })
            except Exception as db_err:
                _log.warning(f"[POST] DB save error (non-fatal): {db_err}")

            await working.delete()
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁", callback_data="menu|createpost")]])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ <b>Posted to {chat_id}</b>",
                reply_markup=kb, parse_mode="HTML"
            )

    except Exception as e:
        _log.error(f"[POST] handle_final error: {e}", exc_info=True)
        try:
            await working.edit_text(
                f"❌ <b>Error:</b>\n<code>{html.escape(str(e))}</code>",
                parse_mode="HTML"
            )
        except: pass

    context.user_data.pop("pb_data", None)
    return ConversationHandler.END

# ── Cancel ────────────────────────────────────────────────────────────────────
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pb_data", None)
    try: await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    except: pass
    return ConversationHandler.END

# ── ConversationHandler ────────────────────────────────────────────────────────
post_builder_handler = ConversationHandler(
    allow_reentry=True,
    entry_points=[
        CommandHandler("createpost", start_builder),
        CallbackQueryHandler(start_builder, pattern=r"^menu\|createpost"),
    ],
    states={
        STATE_MODE: [CallbackQueryHandler(handle_post_mode, pattern=r"^pb_m\||^pb_cancel")],
        STATE_EDIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_link)],
        STATE_FORMAT: [CallbackQueryHandler(handle_format, pattern=r"^pb_f\||^pb_cancel")],
        STATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        STATE_PLATFORM: [
            CallbackQueryHandler(handle_platform_btn, pattern=r"^pb_p\||^pb_p_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform_text),
        ],
        STATE_DESC_MODE: [CallbackQueryHandler(handle_desc_mode, pattern=r"^pb_dm\|")],
        STATE_DESC_CHOICE: [CallbackQueryHandler(handle_desc_choice, pattern=r"^pb_dc\|")],
        STATE_DESC_ENTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_text)],
        STATE_DESC_OCR: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_desc_ocr)],
        STATE_IMG_MODE: [
            CallbackQueryHandler(handle_img_mode, pattern=r"^pb_im\|"),
            CallbackQueryHandler(handle_img_choice, pattern=r"^pb_ic\|"),
        ],
        STATE_IMG_CHOICE: [CallbackQueryHandler(handle_img_choice, pattern=r"^pb_ic\|")],
        STATE_IMG_UPLOAD: [
            CallbackQueryHandler(handle_image_done, pattern=r"^pb_idone"),
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image),
            CommandHandler("skip", handle_image),
        ],
        STATE_GENRE: [
            CallbackQueryHandler(handle_genre_btn, pattern=r"^pb_g\||^pb_g_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre_text),
        ],
        STATE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        STATE_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        STATE_STATUS: [CallbackQueryHandler(handle_status, pattern=r"^pb_s\|")],
        STATE_USERNAME: [
            CallbackQueryHandler(handle_username_btn, pattern=r"^pb_u\||^pb_u_cust"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username_text),
        ],
        STATE_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_destination)],
        STATE_CUSTOM_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_destination)],
        STATE_CONFIRM: [CallbackQueryHandler(handle_final, pattern=r"^pb_final_yes|^pb_cancel")],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_handler),
        CallbackQueryHandler(cancel_handler, pattern=r"^pb_cancel"),
    ],
    per_message=False,
)
