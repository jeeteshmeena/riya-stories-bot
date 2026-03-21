import html
import asyncio
import io
import logging

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
    OCR_AVAILABLE = True
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        OCR_AVAILABLE = False
except ImportError:
    OCR_AVAILABLE = False

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InputMediaDocument,
    InputMediaPhoto,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

_log = logging.getLogger(__name__)

# ── States ────────────────────────────────────────────────────────────────────
(
    STATE_MODE,
    STATE_EDIT_LINK,
    STATE_FORMAT,
    STATE_NAME,
    STATE_PLATFORM,
    STATE_DESC_MODE,
    STATE_DESC_ENTER,
    STATE_DESC_OCR,
    STATE_DESC_CHOICE,
    STATE_IMG_UPLOAD,
    STATE_GENRE,
    STATE_LINK,
    STATE_EPISODES,
    STATE_STATUS,
    STATE_USERNAME,
    STATE_DESTINATION,
    STATE_CUSTOM_DEST,
    STATE_CONFIRM,
) = range(18)

DEFAULT_JOIN_USERNAME = "@StoriesByJeetXNew"
DEFAULT_FORMAT_1_EMOJI = "🫠"
DEFAULT_FORMAT_1_JOIN_EMOJI = "🦊"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _kb(rows, one_time=True):
    """Shorthand for ReplyKeyboardMarkup."""
    return ReplyKeyboardMarkup(
        rows, resize_keyboard=True, one_time_keyboard=one_time
    )

def _is_admin(user_id):
    try:
        from config import ADMIN_ID, OWNER_ID
        from database import load_config
        if user_id in (OWNER_ID, ADMIN_ID):
            return True
        cfg = load_config()
        mods = cfg.get("moderators", [])
        return str(user_id) in mods or user_id in mods
    except Exception:
        return False

# ── Format Builders ───────────────────────────────────────────────────────────
def build_format_1(data):
    name = data.get("name", "Unknown")
    status = data.get("status", "Ongoing")
    genre = data.get("genre", "Unknown")
    link = data.get("link", "")
    username = data.get("username", DEFAULT_JOIN_USERNAME)
    t  = f"{DEFAULT_FORMAT_1_EMOJI} <b>Name :-</b>  <b>{html.escape(name)}</b> ( <b>{status}</b> )\n\n"
    t += f"<b>Story Type :-</b> {html.escape(genre)}\n\n"
    t += f"<b>Link 🖇:-</b> {link}\n\n"
    t += f"{DEFAULT_FORMAT_1_JOIN_EMOJI}<b>JOIN FOR ALL EPISODES.</b>\n\nJoin - {username}"
    return t

def build_format_2(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    desc = data.get("desc", "")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    avail = data.get("availability", "All Stories Available on Stories🫶🏻.")
    desc_block = f"<blockquote expandable><i>{html.escape(desc)}</i></blockquote>\n\n" if desc else ""
    t  = f"<b>{html.escape(name)} • {html.escape(platform)}</b>\n\n"
    t += desc_block
    t += f"<b>Episodes -</b> <b>{episodes}</b>\n"
    t += f"<b>Status -</b> <b>{status}</b>\n\n"
    t += f"{html.escape(avail)}\n\n{link}\n{link}"
    return t

def build_format_3(data):
    name = data.get("name", "Unknown")
    genre = data.get("genre", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    return (
        f"✨ <b>{html.escape(name)}</b> ✨\n\n"
        f"◃ <b>Genre:</b> {html.escape(genre)}\n"
        f"◃ <b>Status:</b> <b>{status}</b>\n\n"
        f"🔗 <b>Download / Read:</b>\n{link}"
    )

def build_format_4(data):
    name = data.get("name", "Unknown")
    platform = data.get("platform", "Unknown")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    return (
        f"╔════════════════════╗\n"
        f"  <b>{html.escape(name)}</b>\n"
        f"╚════════════════════╝\n\n"
        f"» <b>Platform:</b> {html.escape(platform)}\n"
        f"» <b>Status:</b> {status}\n\n"
        f"📥 <b>Get it here:</b>\n{link}"
    )

def build_format_5(data):
    name = data.get("name", "Unknown")
    episodes = data.get("episodes", "0")
    status = data.get("status", "Ongoing")
    link = data.get("link", "")
    return f"📱 <b>{html.escape(name)}</b> ◂ <b>[ {status} • {episodes} ]</b>\n\n{link}"

def _build_previews(data):
    fmt = data.get("format", "1")
    fmts = ["1", "2"] if fmt == "both" else [fmt]
    builders = {"1": build_format_1, "2": build_format_2, "3": build_format_3,
                "4": build_format_4, "5": build_format_5}
    return [builders[f](data) for f in fmts if f in builders]

# ── OCR ───────────────────────────────────────────────────────────────────────
def _ocr_clean(raw: str) -> str:
    """Clean OCR output; keep full text, remove garbage symbols."""
    import re
    text = re.sub(r"[€॥\[\]/|\\<>{}]", "", raw)
    lines = text.split("\n")
    paras, cur = [], []
    for ln in lines:
        s = re.sub(r" +", " ", ln.strip())
        if not s:
            if cur:
                paras.append(" ".join(cur))
                cur = []
        else:
            cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    result = "\n\n".join(paras)
    result = re.sub(r"([।?!]) (?=[A-Za-z0-9ऀ-ॿ])", r"\1\n", result)
    return result.strip()

# ── Background tasks ──────────────────────────────────────────────────────────
async def _bg_prefetch_img(context, name, platform):
    try:
        from advanced_scraper import extract_hd_image
        img = await asyncio.wait_for(extract_hd_image(name, platform), timeout=12)
        if img:
            context.user_data.get("pb_data", {})["temp_img_bytes"] = img
            _log.info("[IMG] Prefetch done")
    except Exception as e:
        _log.warning(f"[IMG] Prefetch failed: {e}")

# ── Entry ─────────────────────────────────────────────────────────────────────
async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_admin(user.id):
        msg = "⛔ Admin only."
        if update.callback_query:
            try: await update.callback_query.answer(msg, show_alert=True)
            except: pass
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    # Cancel any existing session
    if context.user_data.get("pb_data"):
        try:
            msg_target = update.message or (update.callback_query and update.callback_query.message)
            if msg_target:
                await msg_target.reply_text("⚠️ Previous session cancelled.", reply_markup=ReplyKeyboardRemove())
        except: pass

    context.user_data["pb_data"] = {}

    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass

    kb = _kb([["🆕 New", "✏️ Edit"], ["/cancel"]])
    await (update.message or update.callback_query.message).reply_text(
        "🛠 <b>Post Builder</b>\n\nSelect mode:",
        reply_markup=kb, parse_mode="HTML"
    )
    return STATE_MODE

# ── Mode ──────────────────────────────────────────────────────────────────────
async def handle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🆕 New":
        context.user_data["pb_data"]["post_mode"] = "new"
    elif text == "✏️ Edit":
        context.user_data["pb_data"]["post_mode"] = "edit"
        await update.message.reply_text(
            "📝 Paste the Telegram post link to edit:\n(e.g. https://t.me/channel/123)",
            reply_markup=ReplyKeyboardRemove()
        )
        return STATE_EDIT_LINK
    else:
        await update.message.reply_text("❌ Use the buttons below.", reply_markup=_kb([["🆕 New", "✏️ Edit"], ["/cancel"]]))
        return STATE_MODE

    # Format selection
    kb = _kb([
        ["Format 1", "Format 2"],
        ["Both 1+2", "Format 3"],
        ["Format 4", "Format 5"],
        ["/cancel"],
    ])
    await update.message.reply_text("📋 Select format:", reply_markup=kb)
    return STATE_FORMAT

# ── Edit link ─────────────────────────────────────────────────────────────────
async def handle_edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "t.me/" not in text:
        await update.message.reply_text("❌ Invalid link. Send a valid Telegram post link:")
        return STATE_EDIT_LINK
    try:
        parts = text.split("t.me/")[1].split("/")
        if parts[0] == "c":
            chat_id = "-100" + parts[1]
            msg_id = int(parts[2].split("?")[0])
        else:
            chat_id = "@" + parts[0].lstrip("@")
            msg_id = int(parts[1].split("?")[0])
        context.user_data["pb_data"]["edit_chat_id"] = chat_id
        context.user_data["pb_data"]["edit_msg_id"] = msg_id
    except Exception:
        await update.message.reply_text("❌ Could not parse. Try again:")
        return STATE_EDIT_LINK

    kb = _kb([["Format 1", "Format 2"], ["Format 3", "Format 4"], ["Format 5"], ["/cancel"]])
    await update.message.reply_text("📋 Select format:", reply_markup=kb)
    return STATE_FORMAT

# ── Format ────────────────────────────────────────────────────────────────────
_FORMAT_MAP = {
    "Format 1": "1", "Format 2": "2", "Both 1+2": "both",
    "Format 3": "3", "Format 4": "4", "Format 5": "5",
}

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    fmt = _FORMAT_MAP.get(text)
    if not fmt:
        await update.message.reply_text("❌ Select a format from the buttons.")
        return STATE_FORMAT
    context.user_data["pb_data"]["format"] = fmt
    await update.message.reply_text("📖 Enter story name:", reply_markup=ReplyKeyboardRemove())
    return STATE_NAME

# ── Name ──────────────────────────────────────────────────────────────────────
async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["name"] = update.message.text.strip()
    kb = _kb([["📱 Pocket FM", "🎧 Kuku FM"], ["🎵 Headfone", "➕ Custom"], ["/cancel"]])
    await update.message.reply_text("🎵 Select platform:", reply_markup=kb)
    return STATE_PLATFORM

# ── Platform ──────────────────────────────────────────────────────────────────
_PLATFORM_MAP = {
    "📱 Pocket FM": "Pocket FM",
    "🎧 Kuku FM": "Kuku FM",
    "🎵 Headfone": "Headfone",
}

async def handle_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ Custom":
        await update.message.reply_text("Type the platform name:", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_awaiting_custom_platform"] = True
        return STATE_PLATFORM
    plat = _PLATFORM_MAP.get(text, text if context.user_data["pb_data"].pop("_awaiting_custom_platform", False) else None)
    if not plat:
        await update.message.reply_text("❌ Use the buttons or type a custom name.")
        return STATE_PLATFORM

    context.user_data["pb_data"]["platform"] = plat
    # Start image prefetch in background
    name = context.user_data["pb_data"].get("name", "")
    asyncio.create_task(_bg_prefetch_img(context, name, plat))
    return await _go_to_desc(update, context)

async def _go_to_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = _kb([["✍️ Manual", "📸 OCR"], ["/cancel"]])
    await update.message.reply_text("📝 Description mode:", reply_markup=kb)
    return STATE_DESC_MODE

# ── Description mode ──────────────────────────────────────────────────────────
async def handle_desc_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "✍️ Manual":
        await update.message.reply_text("✍️ Type the description:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_ENTER
    elif text == "📸 OCR":
        if not OCR_AVAILABLE:
            await update.message.reply_text(
                "❌ OCR not available on server.\n\n"
                "Install:\n<code>sudo apt install tesseract-ocr tesseract-ocr-hin -y</code>\n"
                "<code>pip install pytesseract</code>\n\n"
                "✍️ Enter description manually:",
                parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
            )
            return STATE_DESC_ENTER
        await update.message.reply_text("📸 Send the screenshot:", reply_markup=ReplyKeyboardRemove())
        return STATE_DESC_OCR
    else:
        await update.message.reply_text("❌ Use the buttons.", reply_markup=_kb([["✍️ Manual", "📸 OCR"], ["/cancel"]]))
        return STATE_DESC_MODE

# ── Description manual entry ──────────────────────────────────────────────────
async def handle_desc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["desc"] = update.message.text.strip()
    return await _go_to_img(update, context)

# ── OCR handler ───────────────────────────────────────────────────────────────
async def handle_desc_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Send an image file.")
        return STATE_DESC_OCR

    wait = await update.message.reply_text("⏳ Scanning…")
    try:
        tg_file = (
            await update.message.photo[-1].get_file()
            if update.message.photo
            else await update.message.document.get_file()
        )
        raw_bytes = await tg_file.download_as_bytearray()
        img = Image.open(io.BytesIO(raw_bytes))

        def _run_ocr(image):
            image = image.convert("L")
            image = ImageEnhance.Contrast(image).enhance(2.0)
            image = image.filter(ImageFilter.SHARPEN)
            w, h = image.size
            image = image.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
            return pytesseract.image_to_string(image, lang="eng+hin", config="--oem 3 --psm 6")

        raw_text = await asyncio.to_thread(_run_ocr, img)
        cleaned = _ocr_clean(raw_text)
        await wait.delete()

        if len(cleaned) < 10:
            await update.message.reply_text("❌ Try clearer or cropped image.\n\n✍️ Enter manually:")
            return STATE_DESC_ENTER

        context.user_data["pb_data"]["temp_found_desc"] = cleaned
        preview = html.escape(cleaned)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Use", callback_data="pb_dc|use"),
            InlineKeyboardButton("✍️ Edit", callback_data="pb_dc|manual"),
            InlineKeyboardButton("🔁 Retry", callback_data="pb_dc|retry"),
        ]])
        await update.message.reply_text(
            f"★ <b>Extracted:</b>\n\n<blockquote>{preview}</blockquote>",
            reply_markup=kb, parse_mode="HTML"
        )
        return STATE_DESC_CHOICE

    except Exception as e:
        _log.error(f"[OCR] {e}", exc_info=True)
        try: await wait.edit_text(f"❌ OCR error. Enter manually:")
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
        return await _go_to_img_from_query(update, context, query)
    elif choice == "retry":
        await query.message.reply_text("📸 Send another screenshot:")
        return STATE_DESC_OCR
    else:
        await query.message.reply_text("✍️ Type the description:")
        return STATE_DESC_ENTER

# ── Image step ────────────────────────────────────────────────────────────────
async def _go_to_img(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    cached = data.get("temp_img_bytes")
    if cached:
        try:
            sent = await context.bot.send_document(
                chat_id=update.message.chat_id,
                document=cached,
                filename=f"{data.get('name','cover')}_cover.jpg",
                caption="✅ <b>Cover auto-fetched.</b>",
                parse_mode="HTML"
            )
            data["photo_ids"] = [{"id": sent.document.file_id, "type": "doc"}]
            return await _go_to_genre(update, context)
        except Exception as e:
            _log.warning(f"[IMG] send prefetch failed: {e}")

    await update.message.reply_text(
        "📷 <b>Send image</b>\n<i>Type /skip to skip.</i>",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )
    return STATE_IMG_UPLOAD

async def _go_to_img_from_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    data = context.user_data["pb_data"]
    cached = data.get("temp_img_bytes")
    msg = query.message
    if cached:
        try:
            sent = await context.bot.send_document(
                chat_id=msg.chat_id,
                document=cached,
                filename=f"{data.get('name','cover')}_cover.jpg",
                caption="✅ <b>Cover auto-fetched.</b>",
                parse_mode="HTML"
            )
            data["photo_ids"] = [{"id": sent.document.file_id, "type": "doc"}]
            return await _go_to_genre_from_query(update, context, query)
        except Exception as e:
            _log.warning(f"[IMG] send prefetch failed: {e}")

    await msg.reply_text(
        "📷 <b>Send image</b>\n<i>Type /skip to skip.</i>",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )
    return STATE_IMG_UPLOAD

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    if update.message.text and update.message.text.strip() == "/skip":
        data.setdefault("photo_ids", [])
        return await _go_to_genre(update, context)

    if update.message.photo:
        fid, mtype = update.message.photo[-1].file_id, "photo"
    elif update.message.document:
        fid, mtype = update.message.document.file_id, "doc"
    else:
        await update.message.reply_text("❌ Please send image or type /skip")
        return STATE_IMG_UPLOAD

    data["photo_ids"] = [{"id": fid, "type": mtype}]
    return await _go_to_genre(update, context)

# ── Genre ─────────────────────────────────────────────────────────────────────
async def _go_to_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = _kb([
        ["Romance", "Thriller"],
        ["Crime", "Horror"],
        ["Suspense", "Drama"],
        ["➕ Custom", "/cancel"],
    ])
    await update.message.reply_text("🎭 Select genre:", reply_markup=kb)
    return STATE_GENRE

async def _go_to_genre_from_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    kb = _kb([
        ["Romance", "Thriller"],
        ["Crime", "Horror"],
        ["Suspense", "Drama"],
        ["➕ Custom", "/cancel"],
    ])
    await query.message.reply_text("🎭 Select genre:", reply_markup=kb)
    return STATE_GENRE

_GENRES = {"Romance", "Thriller", "Crime", "Horror", "Suspense", "Drama"}

async def handle_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ Custom":
        await update.message.reply_text("Type the custom genre:", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_awaiting_custom_genre"] = True
        return STATE_GENRE
    genre = text if context.user_data["pb_data"].pop("_awaiting_custom_genre", False) else (text if text in _GENRES else None)
    if not genre:
        await update.message.reply_text("❌ Use the buttons or type a custom genre.")
        return STATE_GENRE
    context.user_data["pb_data"]["genre"] = genre
    await update.message.reply_text("🔗 Paste the story link (or /skip):", reply_markup=ReplyKeyboardRemove())
    return STATE_LINK

# ── Link ──────────────────────────────────────────────────────────────────────
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["link"] = "" if update.message.text.strip() == "/skip" else update.message.text.strip()
    await update.message.reply_text("🔢 Total episodes number:")
    return STATE_EPISODES

# ── Episodes ──────────────────────────────────────────────────────────────────
async def handle_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pb_data"]["episodes"] = update.message.text.strip()
    kb = _kb([["✅ Completed", "🔄 Ongoing"], ["☠️ RIP"], ["/cancel"]])
    await update.message.reply_text("📊 Select status:", reply_markup=kb)
    return STATE_STATUS

# ── Status ────────────────────────────────────────────────────────────────────
_STATUS_MAP = {"✅ Completed": "Completed", "🔄 Ongoing": "Ongoing", "☠️ RIP": "RIP"}

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    status = _STATUS_MAP.get(text)
    if not status:
        await update.message.reply_text("❌ Use the buttons.", reply_markup=_kb([["✅ Completed", "🔄 Ongoing"], ["☠️ RIP"], ["/cancel"]]))
        return STATE_STATUS
    context.user_data["pb_data"]["status"] = status
    # Username step
    kb = _kb([["Default Username", "➕ Custom Username"], ["/cancel"]])
    await update.message.reply_text("👤 Join username:", reply_markup=kb)
    return STATE_USERNAME

# ── Username ──────────────────────────────────────────────────────────────────
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "Default Username":
        context.user_data["pb_data"]["username"] = DEFAULT_JOIN_USERNAME
        return await _go_to_dest(update, context)
    elif text == "➕ Custom Username":
        await update.message.reply_text("Type the username (e.g. @MyChannel):", reply_markup=ReplyKeyboardRemove())
        context.user_data["pb_data"]["_awaiting_custom_username"] = True
        return STATE_USERNAME
    elif context.user_data["pb_data"].pop("_awaiting_custom_username", False):
        context.user_data["pb_data"]["username"] = text
        return await _go_to_dest(update, context)
    else:
        await update.message.reply_text("❌ Use the buttons.", reply_markup=_kb([["Default Username", "➕ Custom Username"], ["/cancel"]]))
        return STATE_USERNAME

# ── Destination ───────────────────────────────────────────────────────────────
async def _go_to_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data["pb_data"].get("post_mode") == "edit":
        return await _show_preview(update.message, context)
    try:
        from database import load_config
        channels = load_config().get("post_channels", [])
    except Exception:
        channels = []
    rows = [[c] for c in channels[:5]]
    rows.append(["➕ New Channel"])
    rows.append(["/cancel"])
    kb = _kb(rows)
    await update.message.reply_text("📢 Where to post?", reply_markup=kb)
    return STATE_DESTINATION

async def handle_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ New Channel":
        await update.message.reply_text("Send channel username or ID:", reply_markup=ReplyKeyboardRemove())
        return STATE_CUSTOM_DEST
    context.user_data["pb_data"]["destination"] = text
    msg = await update.message.reply_text("✅ Channel set.", reply_markup=ReplyKeyboardRemove())
    return await _show_preview(msg, context)

async def handle_custom_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip()
    try:
        from database import load_config, save_config
        cfg = load_config()
        channels = cfg.setdefault("post_channels", [])
        if dest not in channels:
            channels.append(dest)
            save_config(cfg)
    except Exception: pass
    context.user_data["pb_data"]["destination"] = dest
    msg = await update.message.reply_text(f"✅ Saved {dest}.", reply_markup=ReplyKeyboardRemove())
    return await _show_preview(msg, context)

# ── Preview & Confirm ─────────────────────────────────────────────────────────
async def _show_preview(message, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    previews = _build_previews(data)
    data["cached_previews"] = previews
    photo_ids = data.get("photo_ids", [])

    await message.reply_text("🔎 <b>Preview:</b>", parse_mode="HTML")

    for p in previews:
        try:
            if photo_ids:
                item = photo_ids[0]
                if item["type"] == "doc":
                    await context.bot.send_document(chat_id=message.chat_id, document=item["id"], caption=p, parse_mode="HTML")
                else:
                    await context.bot.send_photo(chat_id=message.chat_id, photo=item["id"], caption=p, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            _log.error(f"[PREVIEW] {e}")
            await context.bot.send_message(chat_id=message.chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)

    kb = _kb([["✅ Post", "✏️ Re-edit"], ["❌ Cancel"]])
    await message.reply_text("Confirm and post?", reply_markup=kb)
    return STATE_CONFIRM

# ── Confirm ───────────────────────────────────────────────────────────────────
async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Cancel":
        await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END
    elif text == "✏️ Re-edit":
        # Restart flow
        context.user_data.pop("pb_data", None)
        return await start_builder(update, context)
    elif text == "✅ Post":
        return await _do_post(update, context)
    else:
        await update.message.reply_text("❌ Use the buttons.", reply_markup=_kb([["✅ Post", "✏️ Re-edit"], ["❌ Cancel"]]))
        return STATE_CONFIRM

async def _do_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["pb_data"]
    post_mode = data.get("post_mode", "new")
    dest = data.get("destination")
    edit_chat_id = data.get("edit_chat_id")
    edit_msg_id = data.get("edit_msg_id")
    chat_id = edit_chat_id if post_mode == "edit" else dest

    if post_mode == "new" and not dest:
        await update.message.reply_text("❌ No destination. Cancelled.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("pb_data", None)
        return ConversationHandler.END

    previews = data.get("cached_previews", [])
    photo_ids = data.get("photo_ids", [])
    working = await update.message.reply_text("⏳ Posting…", reply_markup=ReplyKeyboardRemove())

    try:
        if post_mode == "edit":
            p = previews[0]
            if photo_ids:
                item = photo_ids[0]
                media = InputMediaDocument(media=item["id"], caption=p, parse_mode="HTML") if item["type"] == "doc" else InputMediaPhoto(media=item["id"], caption=p, parse_mode="HTML")
                await asyncio.wait_for(context.bot.edit_message_media(chat_id=chat_id, message_id=edit_msg_id, media=media), timeout=15)
            else:
                try:
                    await asyncio.wait_for(context.bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=p, parse_mode="HTML", disable_web_page_preview=True), timeout=15)
                except Exception:
                    await asyncio.wait_for(context.bot.edit_message_caption(chat_id=chat_id, message_id=edit_msg_id, caption=p, parse_mode="HTML"), timeout=15)
            await working.edit_text(f"✅ Edited in {chat_id}!")

        else:
            for p in previews:
                if photo_ids and len(photo_ids) == 1:
                    item = photo_ids[0]
                    if item["type"] == "doc":
                        await asyncio.wait_for(context.bot.send_document(chat_id=chat_id, document=item["id"], caption=p, parse_mode="HTML"), timeout=15)
                    else:
                        await asyncio.wait_for(context.bot.send_photo(chat_id=chat_id, photo=item["id"], caption=p, parse_mode="HTML"), timeout=15)
                elif photo_ids:
                    docs, photos = [], []
                    for i, item in enumerate(photo_ids):
                        cap, pm = (p, "HTML") if i == 0 else ("", None)
                        if item["type"] == "doc": docs.append(InputMediaDocument(media=item["id"], caption=cap, parse_mode=pm))
                        else: photos.append(InputMediaPhoto(media=item["id"], caption=cap, parse_mode=pm))
                    if docs: await asyncio.wait_for(context.bot.send_media_group(chat_id=chat_id, media=docs), timeout=15)
                    if photos: await asyncio.wait_for(context.bot.send_media_group(chat_id=chat_id, media=photos), timeout=15)
                else:
                    await asyncio.wait_for(context.bot.send_message(chat_id=chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True), timeout=15)

            # Save to DB
            try:
                from database import save_story
                save_story(
                    name=data.get("name"), genre=data.get("genre"),
                    link=data.get("link"), episodes=data.get("episodes"),
                    status=data.get("status"),
                    description_original=data.get("desc", ""),
                    description_short=data.get("desc", ""),
                    image_url=photo_ids[0]["id"] if photo_ids else "",
                    extra_images=[i["id"] for i in photo_ids[1:]] if len(photo_ids) > 1 else [],
                    format_number=int(data.get("format")) if data.get("format") not in (None, "both") else 1,
                )
            except Exception as e:
                _log.warning(f"[POST] save_story failed: {e}")

            await working.edit_text(f"✅ Posted to {chat_id}!")

    except Exception as e:
        _log.error(f"[POST] {e}", exc_info=True)
        try: await working.edit_text(f"❌ Error: {html.escape(str(e))}")
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
        CommandHandler("JeetX", start_builder),
        CallbackQueryHandler(start_builder, pattern=r"^menu\|createpost"),
    ],
    states={
        STATE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mode)],
        STATE_EDIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_link)],
        STATE_FORMAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_format)],
        STATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        STATE_PLATFORM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_platform)],
        STATE_DESC_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_mode)],
        STATE_DESC_ENTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_desc_text)],
        STATE_DESC_OCR: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_desc_ocr)],
        STATE_DESC_CHOICE: [CallbackQueryHandler(handle_desc_choice, pattern=r"^pb_dc\|")],
        STATE_IMG_UPLOAD: [
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image),
            CommandHandler("skip", handle_image),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_image),
        ],
        STATE_GENRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_genre)],
        STATE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        STATE_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_episodes)],
        STATE_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_status)],
        STATE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username)],
        STATE_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_destination)],
        STATE_CUSTOM_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_dest)],
        STATE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_handler),
        CommandHandler("JeetX", start_builder),
    ],
    per_message=False,
)
