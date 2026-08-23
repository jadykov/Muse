import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.config import settings
from app.logging_config import setup_logging
from app import muse
from app.history import history
from app import tools as app_tools

logger = logging.getLogger(__name__)

# --- Access helpers ---

def is_allowed(update: Update) -> bool:
    allowed_chats = settings.get_allowed_chat_ids()
    allowed_users = settings.get_allowed_user_ids()
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    if allowed_chats and chat_id not in allowed_chats:
        return False
    if allowed_users and user_id not in allowed_users:
        return False
    return True


def chat_label(update: Update) -> str:
    c = update.effective_chat
    if not c:
        return "unknown"
    return c.title or c.username or str(c.id)


SEARCH_TRIGGERS = ("найди", "поиск", "погугли", "search", "гугл", "интернет", "что нового", "новости")

def needs_search(text: str) -> bool:
    low = text.lower()
    return any(t in low for t in SEARCH_TRIGGERS)


# --- Commands ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Привет! Я Muse 1.2 бот для группы (5 чел).\n"
        "Что умею:\n"
        "• Текст — просто пиши (стриминг)\n"
        "• Фото/документ — опишу и сделаю OCR\n"
        "• Голосовые — расшифрую (audio input нативно)\n"
        "• Поиск — напиши «найди ...» или /search\n"
        "• Инструменты — спроси время, /calc 2+2, /json запрос\n"
        "Команды: /help /clear /search /calc /json"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/clear — очистить историю чата\n"
        "/search <запрос> — живой поиск в интернете\n"
        "/calc <выражение> — калькулятор\n"
        "/json <запрос> — structured output (JSON)\n\n"
        "Просто отправь текст/фото/голосовое — отвечу через Muse 1.2.\n"
        "Триггеры времени: «который час», «время», «дата» -> tool calling."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    history.clear(update.effective_chat.id)
    await update.message.reply_text("История чата очищена.")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    query = " ".join(context.args) if context.args else ""
    if not query and update.message.reply_to_message and update.message.reply_to_message.text:
        query = update.message.reply_to_message.text
    if not query:
        await update.message.reply_text("Использование: /search <запрос>")
        return
    chat_id = update.effective_chat.id
    history.add(chat_id, "user", query)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        answer = await muse.chat_with_search(chat_id, query)
    except Exception as e:
        logger.exception("search failed")
        answer = f"Ошибка поиска: {e}"
    history.add(chat_id, "assistant", answer)
    await update.message.reply_text(answer[:4000] or "—")


async def cmd_calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    expr = " ".join(context.args) if context.args else ""
    if not expr:
        await update.message.reply_text("Использование: /calc <выражение>  например: /calc (12+5)*3")
        return
    result = app_tools.safe_calc(expr)
    await update.message.reply_text(f"{expr} = {result}")


async def cmd_json(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Использование: /json <запрос> — вернет structured JSON")
        return
    chat_id = update.effective_chat.id
    history.add(chat_id, "user", text)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        data = await muse.chat_structured(chat_id, text, schema={"type": "object"})
        import json as _json
        out = _json.dumps(data, ensure_ascii=False, indent=2)
        await update.message.reply_text(f"```json\n{out[:3800]}\n```", parse_mode="Markdown")
        history.add(chat_id, "assistant", out)
    except Exception as e:
        logger.exception("json failed")
        await update.message.reply_text(f"Ошибка: {e}"[:4000])


# --- Streaming helper for text ---

async def reply_streaming(update: Update, chat_id: int, user_text: str) -> None:
    # If message likely needs tools (time/calc), bypass streaming — use tool loop
    low = user_text.lower()
    needs_tools = any(k in low for k in ("который час", "сколько время", "время", "дата", "посчитай", "вычисли", "сколько будет"))
    if needs_tools:
        await context_bot_send_typing(update, chat_id)
        try:
            text = await muse.chat_completion(chat_id, user_text)
            history.add(chat_id, "assistant", text)
            await update.message.reply_text(text[:4000] or "—")
            return
        except Exception:
            logger.exception("tool chat failed, fallback to stream")

    msg = await update.message.reply_text("…")
    buffer = ""
    last_edit = 0.0
    import time

    try:
        async for delta in muse.chat_completion_stream(chat_id, user_text):
            buffer += delta
            now = time.time()
            if now - last_edit > 0.9 and len(buffer) > 10:
                try:
                    await msg.edit_text(buffer[:4000])
                    last_edit = now
                except Exception:
                    pass
        final = buffer.strip() or "—"
        history.add(chat_id, "assistant", final)
        try:
            await msg.edit_text(final[:4000])
        except Exception:
            if final != buffer.strip():
                await update.message.reply_text(final[:4000])
    except Exception as e:
        logger.exception("stream failed, fallback")
        try:
            text = await muse.chat_completion(chat_id, user_text)
            history.add(chat_id, "assistant", text)
            await msg.edit_text(text[:4000] or "—")
        except Exception as e2:
            logger.exception("fallback also failed")
            await msg.edit_text(f"Ошибка: {e2}"[:4000])


async def context_bot_send_typing(update: Update, chat_id: int):
    try:
        await update.get_bot().send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        pass


# --- Message handlers ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update) or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name if update.effective_user else None
    logger.info("text chat=%s (%s) user=%s: %r", chat_id, chat_label(update), user_name, text[:120])

    history.add(chat_id, "user", text, name=user_name)

    # Live search trigger
    if needs_search(text):
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            answer = await muse.chat_with_search(chat_id, text)
            history.add(chat_id, "assistant", answer)
            await update.message.reply_text(answer[:4000] or "—")
            return
        except Exception:
            logger.exception("search trigger fallback to chat")

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await reply_streaming(update, chat_id, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update) or not update.message:
        return
    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption
    logger.info("photo chat=%s caption=%r file_id=%s", chat_id, caption, photo.file_id)
    history.add(chat_id, "user", f"[фото] {caption or ''}".strip())

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    status = await update.message.reply_text("Смотрю фото…")
    try:
        f = await context.bot.get_file(photo.file_id)
        data, mime = await muse.fetch_bytes(f.file_path)  # type: ignore[arg-type]
        # fallback mime
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        answer = await muse.chat_with_image(chat_id, data, mime, caption=caption)
        history.add(chat_id, "assistant", answer)
        await status.edit_text(answer[:4000] or "—")
    except Exception as e:
        logger.exception("photo handle failed")
        await status.edit_text(f"Не смог обработать фото: {e}"[:4000])


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update) or not update.message or not update.message.document:
        return
    doc = update.message.document
    chat_id = update.effective_chat.id
    caption = update.message.caption
    logger.info("document chat=%s name=%s mime=%s", chat_id, doc.file_name, doc.mime_type)
    history.add(chat_id, "user", f"[документ {doc.file_name}] {caption or ''}".strip())
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    status = await update.message.reply_text("Читаю документ…")
    try:
        f = await context.bot.get_file(doc.file_id)
        data, fetched_mime = await muse.fetch_bytes(f.file_path)  # type: ignore[arg-type]
        mime = doc.mime_type or fetched_mime
        if mime and mime.startswith("image/"):
            answer = await muse.chat_with_image(chat_id, data, mime, caption=caption or f"Опиши документ {doc.file_name}")
        elif mime == "application/pdf":
            # Muse can handle PDF as image-like or text; send as image input (first page not converted here — send as generic)
            # We pass as image/jpeg fallback; better: send bytes as image and ask OCR
            answer = await muse.chat_with_image(chat_id, data, "application/pdf", caption=caption or "Распознай текст из PDF и перескажи.")
        else:
            # try decode as text
            try:
                text_preview = data[:8000].decode("utf-8", errors="ignore")
            except Exception:
                text_preview = f"<бинарный файл {len(data)} байт>"
            answer = await muse.chat_completion(chat_id, f"Пользователь прислал документ {doc.file_name} ({mime}). Caption: {caption}\nСодержимое (превью):\n{text_preview[:3000]}")
        history.add(chat_id, "assistant", answer)
        await status.edit_text(answer[:4000] or "—")
    except Exception as e:
        logger.exception("document handle failed")
        await status.edit_text(f"Не смог обработать документ: {e}"[:4000])


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update) or not update.message:
        return
    voice = update.message.voice or update.message.audio
    if not voice:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption
    logger.info("voice chat=%s file_id=%s duration=%s", chat_id, voice.file_id, getattr(voice, "duration", "?"))
    history.add(chat_id, "user", f"[голосовое {getattr(voice,'duration', '?')}с] {caption or ''}".strip())
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    status = await update.message.reply_text("Слушаю…")
    try:
        f = await context.bot.get_file(voice.file_id)
        data, fetched_mime = await muse.fetch_bytes(f.file_path)  # type: ignore[arg-type]
        mime = getattr(voice, "mime_type", None) or fetched_mime or "audio/ogg"
        # normalize ogg/opus -> audio/ogg
        if "ogg" in mime:
            mime = "audio/ogg"
        elif "mp3" in mime or "mpeg" in mime:
            mime = "audio/mpeg"
        answer = await muse.chat_with_audio(chat_id, data, mime, caption=caption)
        history.add(chat_id, "assistant", answer)
        await status.edit_text(answer[:4000] or "—")
    except Exception as e:
        logger.exception("voice handle failed")
        await status.edit_text(f"Не смог обработать аудио: {e}"[:4000])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("unhandled error: %s", context.error)


def build_app() -> Application:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN не задан (проверь .env)")
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY не задан (проверь .env)")
    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("calc", cmd_calc))
    app.add_handler(CommandHandler("json", cmd_json))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)
    return app


def main() -> None:
    setup_logging()
    app = build_app()
    logger.info("bot starting model=%s", settings.muse_model)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
