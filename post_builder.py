import os
import re
import html
import asyncio
import io
import logging
import requests
from PIL import Image, ImageFilter
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
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
    STATE_PLATFORM_CUST,
    STATE_DESC_MODE,
    STATE_DESC_CHOICE,
    STATE_DESC_ENTER,
    STATE_DESC_OCR,
    STATE_IMG_MODE,
    STATE_IMG_CHOICE,
    STATE_IMG_UPLOAD,
    STATE_GENRE,
    STATE_GENRE_CUST,
    STATE_EXTRA_INFO,
    STATE_LINK,
    STATE_EPISODES,
    STATE_STATUS,
    STATE_USERNAME,
    STATE_DESTINATION,
    STATE_CUSTOM_DEST,
    STATE_CONFIRM,
) = range(23)

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
async def start_builder(update, context):
    user = update.effective_user
    if not is_admin_local(user.id):
        await update.message.reply_text("⛔ Admin only.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if context.user_data.get("pb_data") is not None:
        await update.message.reply_text("⚠️ Previous session cancelled.", reply_markup=ReplyKeyboardRemove())

    context.user_data["pb_data"] = {}
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("🆕 New"), KeyboardButton("✏️ Edit")],
        [KeyboardButton("❌ Cancel")],
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>╭─❰ 🛠 POST BUILDER ❱─╮\n┣⊸ Choose mode\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_MODE

async def cancel_handler(update, context):
    context.user_data.pop("pb_data", None)
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── Mode ─────────────────────────────────────────────────────────────────────
async def handle_post_mode(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Edit" in txt:
        context.user_data["pb_data"]["post_mode"] = "edit"
        await update.message.reply_text("📝 <b>Edit Post</b>\nPaste the Telegram post link:\n<i>(e.g. https://t.me/channel/123)</i>", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return STATE_EDIT_LINK
    elif "New" in txt:
        context.user_data["pb_data"]["post_mode"] = "new"
        kb = ReplyKeyboardMarkup([
            [KeyboardButton("Format 1"), KeyboardButton("Format 2")],
            [KeyboardButton("Both 1 & 2")],
            [KeyboardButton("Minimal"), KeyboardButton("Box"), KeyboardButton("Compact")],
            [KeyboardButton("❌ Cancel")]
        ], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("<b>╭─❰ 📝 FORMAT ❱─╮\n┣⊸ Select layout\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
        return STATE_FORMAT
    else:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_MODE

async def handle_edit_link(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    if "t.me/" not in txt:
        await update.message.reply_text("❌ Invalid link. Send a Telegram post link:")
        return STATE_EDIT_LINK
    try:
        parts = txt.split("t.me/")[1].split("/")
        if parts[0] == "c":
            context.user_data["pb_data"]["edit_chat_id"] = "-100" + parts[1]
            context.user_data["pb_data"]["edit_msg_id"] = int(parts[2].split("?")[0])
        else:
            context.user_data["pb_data"]["edit_chat_id"] = ("@" + parts[0]) if not parts[0].startswith("@") else parts[0]
            context.user_data["pb_data"]["edit_msg_id"] = int(parts[1].split("?")[0])
    except:
        await update.message.reply_text("❌ Could not parse link. Try again:")
        return STATE_EDIT_LINK
        
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("Format 1"), KeyboardButton("Format 2")],
        [KeyboardButton("Both 1 & 2")],
        [KeyboardButton("Minimal"), KeyboardButton("Box"), KeyboardButton("Compact")],
        [KeyboardButton("❌ Cancel")]
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("✅ Target saved!\n\nSelect Format to apply:", reply_markup=kb)
    return STATE_FORMAT

# ── Format ───────────────────────────────────────────────────────────────────
async def handle_format(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    mapping = {"Format 1": "1", "Format 2": "2", "Both 1 & 2": "both", "Minimal": "3", "Box": "4", "Compact": "5"}
    fmt = mapping.get(txt)
    if not fmt:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_FORMAT
    context.user_data["pb_data"]["format"] = fmt
    await update.message.reply_text("<b>STEP 1 — NAME</b>\n<b>╭─❰ 📝 STORY NAME ❱─╮\n┣⊸ Enter the name\n┣⊸ Example: <code>A Nightmare</code>\n╰──────────────╯</b>", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    return STATE_NAME

# ── Name ──────────────────────────────────────────────────────────────────────
async def handle_name(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    context.user_data["pb_data"]["name"] = txt
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("📱 Pocket FM"), KeyboardButton("🎧 Kuku FM")],
        [KeyboardButton("➕ Custom"), KeyboardButton("❌ Cancel")],
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>STEP 2 — PLATFORM</b>\n<b>╭─❰ 🎵 PLATFORM ❱─╮\n┣⊸ Select or type it\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_PLATFORM

# ── Platform ──────────────────────────────────────────────────────────────────
async def handle_platform(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    if txt == "➕ Custom":
        await update.message.reply_text("Type the custom platform name:", reply_markup=ReplyKeyboardRemove())
        return STATE_PLATFORM_CUST
        
    platform = txt.replace("📱 ", "").replace("🎧 ", "")
    if len(platform) < 2:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_PLATFORM
    context.user_data["pb_data"]["platform"] = platform
    return await _transition_to_genre(update, context)

async def handle_platform_cust(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    context.user_data["pb_data"]["platform"] = txt
    return await _transition_to_genre(update, context)

# ── Genre ─────────────────────────────────────────────────────────────────────
async def _transition_to_genre(update, context):
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("❤️ Romance"), KeyboardButton("🔥 Thriller")],
        [KeyboardButton("🔪 Crime"), KeyboardButton("👻 Horror")],
        [KeyboardButton("⚔️ Action"), KeyboardButton("😂 Comedy")],
        [KeyboardButton("➕ Custom Genre"), KeyboardButton("❌ Cancel")]
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>STEP 3 — GENRE</b>\n<b>╭─❰ 🎭 GENRE ❱─╮\n┣⊸ Select genre\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_GENRE

async def handle_genre(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    if "Custom Genre" in txt:
        await update.message.reply_text("Type custom genre name:", reply_markup=ReplyKeyboardRemove())
        return STATE_GENRE_CUST
    
    # Strip emoji
    genre = txt.split(" ", 1)[-1] if " " in txt else txt
    if len(genre) < 2:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_GENRE
    context.user_data["pb_data"]["genre"] = genre
    return await _transition_to_status(update, context)

async def handle_genre_cust(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    context.user_data["pb_data"]["genre"] = txt
    return await _transition_to_status(update, context)

# ── Status ────────────────────────────────────────────────────────────────────
async def _transition_to_status(update, context):
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("✅ Completed"), KeyboardButton("🔄 Ongoing")],
        [KeyboardButton("☠️ RIP"), KeyboardButton("❌ Cancel")]
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>STEP 4 — STATUS</b>\n<b>╭─❰ 📊 STATUS ❱─╮\n┣⊸ Select series status\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_STATUS

async def handle_status(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Completed" in txt: context.user_data["pb_data"]["status"] = "Completed"
    elif "Ongoing" in txt: context.user_data["pb_data"]["status"] = "Ongoing"
    elif "RIP" in txt: context.user_data["pb_data"]["status"] = "RIP"
    else:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_STATUS
        
    return await _transition_to_img(update, context)

# ── Image ─────────────────────────────────────────────────────────────────────
async def _transition_to_img(update, context):
    data = context.user_data["pb_data"]
    name = data.get("name", "")
    platform = data.get("platform", "")

    # Fire off background prefetch immediately since we have name/platform
    # (Actually we had them since name step, but this is fine)
    asyncio.create_task(_bg_prefetch_img(context, name, platform))

    cached = data.get("temp_img_bytes")
    if cached:
        _log.info(f"[IMG] Prefetched image available ({len(cached)} bytes)")
        try:
            sent = await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=cached,
                filename=f"{data.get('name','cover')}_cover.jpg"
            )
            data["photo_ids"] = [{"id": sent.document.file_id, "type": "doc"}]
            await update.message.reply_text("✅ <b>Cover image auto-fetched.</b>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
            return await _transition_to_desc(update, context)
        except Exception as e:
            _log.warning(f"[IMG] Failed to send prefetched image: {e}")

    await update.message.reply_text("<b>STEP 5 — IMAGE</b>\n📷 Send image\n\n<i>Type /skip to skip.</i>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return STATE_IMG_UPLOAD

async def handle_image(update, context):
    txt = update.message.text
    if txt and txt.strip() in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    data = context.user_data["pb_data"]
    if txt and txt.strip() == "/skip":
        data.setdefault("photo_ids", [])
        return await _transition_to_desc(update, context)

    if update.message.photo:
        fid = update.message.photo[-1].file_id
        mtype = "photo"
    elif update.message.document:
        fid = update.message.document.file_id
        mtype = "doc"
    else:
        await update.message.reply_text("❌ Please send image or type /skip")
        return STATE_IMG_UPLOAD

    data["photo_ids"] = [{"id": fid, "type": mtype}]
    return await _transition_to_desc(update, context)

# ── Description ───────────────────────────────────────────────────────────────
async def _transition_to_desc(update, context):
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("✍️ Manual"), KeyboardButton("📸 OCR")],
        [KeyboardButton("❌ Cancel")],
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>STEP 6 — DESCRIPTION</b>\nSelect description mode:", reply_markup=kb, parse_mode="HTML")
    return STATE_DESC_MODE

async def handle_desc_mode(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Manual" in txt:
        await update.message.reply_text("✍️ <b>Enter Description:</b>\nType or paste it below:", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return STATE_DESC_ENTER
    elif "OCR" in txt:
        if not OCR_AVAILABLE:
            await update.message.reply_text("❌ <b>OCR not available.</b>\n✍️ Enter manually:", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
            return STATE_DESC_ENTER
        await update.message.reply_text("📷 <b>Send screenshot containing the description:</b>", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return STATE_DESC_OCR
    else:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_DESC_MODE

async def handle_desc_ocr(update, context):
    txt = update.message.text
    if txt and txt.strip() in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Send an image or document.")
        return STATE_DESC_OCR

    wait = await update.message.reply_text("⏳ <i>Scanning via OCR...</i>", parse_mode="HTML")
    try:
        tg_file = await update.message.photo[-1].get_file() if update.message.photo else await update.message.document.get_file()
        raw = await tg_file.download_as_bytearray()
        img = Image.open(io.BytesIO(raw))

        def _run_ocr(image):
            from PIL import ImageEnhance, ImageFilter
            image = image.convert("L")
            image = ImageEnhance.Contrast(image).enhance(2.0)
            image = image.filter(ImageFilter.SHARPEN)
            w, h = image.size
            image = image.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
            import pytesseract
            return pytesseract.image_to_string(image, lang="eng+hin", config="--oem 3 --psm 6")

        raw_text = await asyncio.to_thread(_run_ocr, img)
        cleaned = _ocr_extract(raw_text, "", "")
        await wait.delete()

        if len(cleaned) < 10:
            await update.message.reply_text("❌ Try clearer or cropped image\n\n✍️ Enter manually:", reply_markup=ReplyKeyboardRemove())
            return STATE_DESC_ENTER

        context.user_data["pb_data"]["temp_found_desc"] = cleaned
        preview = html.escape(cleaned)
        kb = ReplyKeyboardMarkup([
            [KeyboardButton("✅ Use Text"), KeyboardButton("✍️ Manual")],
            [KeyboardButton("🔁 Retry OCR"), KeyboardButton("❌ Cancel")],
        ], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(f"★ <b>Extracted:</b>\n\n<blockquote>{preview}</blockquote>", reply_markup=kb, parse_mode="HTML")
        return STATE_DESC_CHOICE

    except Exception as e:
        try: await wait.delete()
        except: pass
        await update.message.reply_text(f"❌ OCR failed: {html.escape(str(e))}\n\n✍️ Enter manually:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_ENTER

async def handle_desc_choice(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Use Text" in txt:
        context.user_data["pb_data"]["desc"] = context.user_data["pb_data"]["temp_found_desc"]
        return await _transition_to_extra(update, context)
    elif "Manual" in txt:
        await update.message.reply_text("✍️ <b>Enter Description:</b>", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        return STATE_DESC_ENTER
    elif "Retry" in txt:
        await update.message.reply_text("📷 Send another screenshot:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_OCR
    else:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_DESC_CHOICE

async def handle_desc_enter(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    context.user_data["pb_data"]["desc"] = txt
    return await _transition_to_extra(update, context)

# ── Extra Info (MERGED) ───────────────────────────────────────────────────────
async def _transition_to_extra(update, context):
    text = (
        "<b>STEP 7 — EXTRA INFO</b>\n"
        "Send Episodes, Link, and Join text in <b>3 lines</b>.\n\n"
        "<b>Example:</b>\n"
        "<code>150</code>\n"
        "<code>https://t.me/channel/123</code>\n"
        "<code>@MyChannel</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return STATE_EXTRA_INFO

async def handle_extra_info(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    if len(lines) < 2:
        await update.message.reply_text("❌ Please provide at least Episodes and Link on separate lines.\nExample:\n150\nhttps://t...\n@MyChat")
        return STATE_EXTRA_INFO
        
    context.user_data["pb_data"]["episodes"] = lines[0]
    context.user_data["pb_data"]["link"] = lines[1]
    context.user_data["pb_data"]["username"] = lines[2] if len(lines) >= 3 else DEFAULT_JOIN_USERNAME
    
    # Destination step
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("📢 Main Channel"), KeyboardButton("➕ Custom Dest")],
        [KeyboardButton("❌ Cancel")]
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>STEP 8 — DESTINATION</b>\nSelect where to post:", reply_markup=kb, parse_mode="HTML")
    return STATE_DESTINATION

# ── Destination ───────────────────────────────────────────────────────────────
async def handle_destination(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Main Channel" in txt:
        context.user_data["pb_data"]["dest"] = "main"
        return await _show_final_preview(update, context)
    elif "Custom" in txt:
        await update.message.reply_text("Type the Custom Channel ID or @username:", reply_markup=ReplyKeyboardRemove())
        return STATE_CUSTOM_DEST
    else:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_DESTINATION
        
async def handle_custom_dest(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    context.user_data["pb_data"]["dest"] = txt
    return await _show_final_preview(update, context)

# ── Preview & Final ──────────────────────────────────────────────────────────
async def _show_final_preview(update, context):
    data = context.user_data["pb_data"]
    previews = _build_previews(data)
    
    for i, p in enumerate(previews):
        try:
            if i == 0 and data.get("photo_ids"):
                media = data["photo_ids"][0]
                if media["type"] == "photo":
                    sent = await update.message.reply_photo(photo=media["id"], caption=p, parse_mode="HTML")
                else:
                    sent = await update.message.reply_document(document=media["id"], caption=p, parse_mode="HTML")
            else:
                sent = await update.message.reply_text(p, parse_mode="HTML", disable_web_page_preview=True)
            if i == 0: data["preview_msg_id"] = sent.message_id
        except Exception as e:
            await update.message.reply_text(f"❌ Preview Error format {i+1}: {e}")
            
    kb = ReplyKeyboardMarkup([
        [KeyboardButton("✅ Post"), KeyboardButton("✏️ Edit")],
        [KeyboardButton("❌ Cancel")],
    ], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("<b>╭─❰ 🚀 READY ❱─╮\n┣⊸ Post it?\n╰──────────────╯</b>", reply_markup=kb, parse_mode="HTML")
    return STATE_CONFIRM

async def handle_final(update, context):
    txt = update.message.text.strip()
    if txt in ["❌ Cancel", "/cancel"]: return await cancel_handler(update, context)
    
    if "Edit" in txt:
        return await start_builder(update, context)
        
    if "Post" not in txt:
        await update.message.reply_text("❌ Select from the keyboard.")
        return STATE_CONFIRM
        
    # User confirmed
    data = context.user_data["pb_data"]
    from database import load_config
    cfg = load_config()
    
    dest_str = data.get("dest", "main")
    if dest_str == "main": dest = cfg.get("main_channel")
    else: dest = dest_str
    
    if not dest:
        await update.message.reply_text("❌ Failed: Destination not configured.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
        
    mode = data.get("post_mode", "new")
    previews = _build_previews(data)
    
    wait = await update.message.reply_text("⏳ <i>Processing post...</i>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    
    try:
        from config import application
        bot = application.bot
        for i, text_content in enumerate(previews):
            part = i
            is_first = (part == 0)
            
            if mode == "edit":
                emid = data.get("edit_msg_id") + part
                echat = data.get("edit_chat_id")
                try:
                    await asyncio.wait_for(
                        bot.edit_message_caption(chat_id=echat, message_id=emid, caption=text_content, parse_mode="HTML")
                        if (is_first and data.get("photo_ids")) else
                        bot.edit_message_text(chat_id=echat, message_id=emid, text=text_content, parse_mode="HTML", disable_web_page_preview=True),
                        timeout=10.0
                    )
                except Exception as e:
                    _log.error(f"Edit failed part {part}: {e}")
            else: # new
                try:
                    if is_first and data.get("photo_ids"):
                        media = data["photo_ids"][0]
                        if media["type"] == "photo":
                            await asyncio.wait_for(bot.send_photo(chat_id=dest, photo=media["id"], caption=text_content, parse_mode="HTML"), timeout=10.0)
                        else:
                            await asyncio.wait_for(bot.send_document(chat_id=dest, document=media["id"], caption=text_content, parse_mode="HTML"), timeout=10.0)
                    else:
                        await asyncio.wait_for(bot.send_message(chat_id=dest, text=text_content, parse_mode="HTML", disable_web_page_preview=True), timeout=10.0)
                except Exception as e:
                    _log.error(f"Send failed part {part}: {e}")

        kb = ReplyKeyboardMarkup([[KeyboardButton("/JeetX Create Another")]], resize_keyboard=True, one_time_keyboard=True)
        await wait.edit_text(f"✅ <b>Successfully {'Edited in' if mode == 'edit' else 'Posted to'} {dest}!</b>", parse_mode="HTML")
        await update.message.reply_text("Done.", reply_markup=kb)
    except Exception as e:
        await wait.edit_text(f"❌ Critical error sending post: {e}")
        
    context.user_data.pop("pb_data", None)
    return ConversationHandler.END

# ── Handler Maps ──────────────────────────────────────────────────────────────
post_builder_handler = ConversationHandler(
    allow_reentry=True,
    entry_points=[CommandHandler("JeetX", start_builder)],
    states={
        STATE_MODE: [MessageHandler(filters.TEXT, handle_post_mode)],
        STATE_EDIT_LINK: [MessageHandler(filters.TEXT, handle_edit_link)],
        STATE_FORMAT: [MessageHandler(filters.TEXT, handle_format)],
        STATE_NAME: [MessageHandler(filters.TEXT, handle_name)],
        STATE_PLATFORM: [MessageHandler(filters.TEXT, handle_platform)],
        STATE_PLATFORM_CUST: [MessageHandler(filters.TEXT, handle_platform_cust)],
        STATE_GENRE: [MessageHandler(filters.TEXT, handle_genre)],
        STATE_GENRE_CUST: [MessageHandler(filters.TEXT, handle_genre_cust)],
        STATE_STATUS: [MessageHandler(filters.TEXT, handle_status)],
        STATE_IMG_UPLOAD: [MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.TEXT, handle_image)],
        STATE_DESC_MODE: [MessageHandler(filters.TEXT, handle_desc_mode)],
        STATE_DESC_OCR: [MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.TEXT, handle_desc_ocr)],
        STATE_DESC_CHOICE: [MessageHandler(filters.TEXT, handle_desc_choice)],
        STATE_DESC_ENTER: [MessageHandler(filters.TEXT, handle_desc_enter)],
        STATE_EXTRA_INFO: [MessageHandler(filters.TEXT, handle_extra_info)],
        STATE_DESTINATION: [MessageHandler(filters.TEXT, handle_destination)],
        STATE_CUSTOM_DEST: [MessageHandler(filters.TEXT, handle_custom_dest)],
        STATE_CONFIRM: [MessageHandler(filters.TEXT, handle_final)],
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)]
)
