    elif _tagged_user_mention:
        # plain @username mention — keep as string mention
        target_mention = _tagged_user_mention

    mention = target_mention

    story_name = clean_story(result["text"])
    story_key = result.get("name") or clean_story(result["text"]).lower()

    # Update trending
    if "trending" not in stats_db: stats_db["trending"] = {}
    stats_db["trending"][story_key] = stats_db["trending"].get(story_key, 0) + 1
    save_stats(stats_db)

    # Prefer pre‑computed story_type from the scanner, fallback to regex
    story_type = result.get("story_type")

    if not story_type:
        caption_text = result.get("caption", "")
        story_type = extract_story_type(caption_text)

    if not story_type:
        story_type = "Not specified"

    # Background checking interception:
    lf = load_link_flags()
    is_broken = lf.get(story_key, {}).get("broken", False)

    if is_broken:
        lang = get_chat_lang(update.effective_chat.id)
        if lang == "hi":
            broken_msg = f"<b>☆ लिंक अस्थायी रूप से अनुपलब्ध है</b>\n\n<i>{story_name}</i>\n\nइस स्टोरी के लिंक में वर्तमान में कोई समस्या है (जैसे कॉपीराइट या डिलीट होना) और एडमिन्स को सूचित कर दिया गया है। कृपया समस्या के ठीक होने तक प्रतीक्षा करें।"
        else:
            broken_msg = f"<b>☆ Link Temporarily Unavailable</b>\n\n<i>{story_name}</i>\n\nThere is currently an issue with this story's link (like copyright or deletion) and admins have been notified. Please wait until it is fixed."
        
        sent = await msg.reply_text(broken_msg, parse_mode="HTML")
        async def _del_broken():
            await asyncio.sleep(30)
            try: await sent.delete()
            except: pass
        asyncio.create_task(_del_broken())
        return

    chat_id = update.effective_chat.id

    # ── LIGHT FORMAT: premium two-message response ──────────────────────────────
    if result.get("format") == "LIGHT":
        light_link    = result.get("link", "")
        light_name    = result.get("text", story_name)
        light_status  = result.get("status", "Unknown")
        light_platform = result.get("platform", "Unknown")
        light_genre   = result.get("genre", "Unknown")
        light_photo   = result.get("photo") or result.get("image") or "https://files.catbox.moe/i59f4o.jpg"

        # Step 1 — header text message
        header_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"Hey {mention} 👋\n<b>✫ I found this story</b> ➴",
            parse_mode="HTML"
        )

        # Step 2 — photo with Light caption, standard search keyboard (no Play/Backup)
        light_name     = result.get("text", story_name)
        light_status   = result.get("status", "Unknown")
        light_platform = result.get("platform", "Unknown")
        light_genre    = result.get("genre", "Unknown")
        light_photo    = result.get("photo") or result.get("image") or "https://files.catbox.moe/i59f4o.jpg"

        light_caption = (
            f"♨️<b>Story</b> : {html.escape(light_name)}\n"
            f"🔰<b>Status</b> : <b>{html.escape(light_status)}</b>\n"
            f"🖥<b>Platform</b> : <b>{html.escape(light_platform)}</b>\n"
            f"🗓<b>Genre</b> : <b>{html.escape(light_genre)}</b>"
        )

        photo_msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=light_photo,
            caption=light_caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        message_owner[photo_msg.message_id] = user.id

        async def _delete_light():
            await asyncio.sleep(300)
            for m in (header_msg, photo_msg):
                try:
                    await m.delete()
                except Exception:
                    pass

        asyncio.create_task(_delete_light())
        await log(context, f"SEARCH HIT (LIGHT) | user_id={user.id} username={user.username} title={light_name}")
        return
    # ── END LIGHT ────────────────────────────────────────────────────────────────

    # Non-Light path (all other formats)
    photo = result.get("photo") or result.get("image")
    story_type_line = f"\n<b>✽ Story Type:-</b> <i>{story_type}</i>" if story_type != "Not specified" else ""
    caption = (
        f"Hey {mention} 👋\n"
        f"<b>✫ I found this story</b> ➴\n\n"
        f"<i>❁ Name:-</i> <b>{story_name}</b>{story_type_line}\n\n"
        f"<tg-spoiler>◒ This reply will be deleted automatically in 5 minutes.</tg-spoiler>"
    )

    if photo:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo="https://files.catbox.moe/i59f4o.jpg",
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    message_owner[msg.message_id] = user.id

    # delete the reply later without blocking the handler
    async def _delete_later():

        await asyncio.sleep(300)

        try:
            await msg.delete()
        except:
            pass

        # user message already deleted above

    asyncio.create_task(_delete_later())

    await log(
        context,
        f"SEARCH HIT | user_id={user.id} username={user.username} title={story_name}"
    )


# -----------------------
# buttons