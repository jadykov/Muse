import base64
import json
import logging
import mimetypes
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.history import history
from app import tools as app_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_FALLBACK = settings.system_prompt

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    return _client


def _messages_for_chat(chat_id: int, user_text: str, system_prompt: str | None = None, extra_content: list[dict] | None = None) -> list[dict[str, Any]]:
    prompt = system_prompt or SYSTEM_PROMPT_FALLBACK
    base = history.get_openai_messages(chat_id, system_prompt=prompt)
    if extra_content:
        base.append({"role": "user", "content": extra_content})
    elif user_text:
        base.append({"role": "user", "content": user_text})
    return base


async def chat_completion(
    chat_id: int,
    user_text: str,
    system_prompt: str | None = None,
    use_tools: bool = True,
) -> str:
    client = get_client()
    messages = _messages_for_chat(chat_id, user_text, system_prompt=system_prompt)
    logger.info("muse chat chat_id=%s msgs=%s model=%s", chat_id, len(messages), settings.muse_model)
    kwargs: dict[str, Any] = {"model": settings.muse_model, "messages": messages, "max_tokens": 1024}
    if use_tools:
        kwargs["tools"] = app_tools.TOOLS  # type: ignore[assignment]
        kwargs["tool_choice"] = "auto"
    resp = await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
    msg = resp.choices[0].message
    # Tool calling loop
    if use_tools and getattr(msg, "tool_calls", None):
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})  # type: ignore[union-attr]
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            result = app_tools.execute_tool(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        resp2 = await client.chat.completions.create(model=settings.muse_model, messages=messages, max_tokens=1024)  # type: ignore[arg-type]
        text = resp2.choices[0].message.content or ""
        return text.strip()
    text = msg.content or ""
    return text.strip()


async def chat_completion_stream(
    chat_id: int,
    user_text: str,
    system_prompt: str | None = None,
):
    client = get_client()
    messages = _messages_for_chat(chat_id, user_text, system_prompt=system_prompt)
    # Streaming intentionally without tools (tools need non-stream for tool_calls)
    stream = await client.chat.completions.create(
        model=settings.muse_model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


# --- Multimodal helpers ---

def encode_image_base64(data: bytes, mime: str) -> str:
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def guess_mime(filename: str, fallback: str = "image/jpeg") -> str:
    mt, _ = mimetypes.guess_type(filename)
    return mt or fallback


async def chat_with_image(
    chat_id: int,
    image_bytes: bytes,
    mime: str,
    caption: str | None = None,
    system_prompt: str | None = None,
) -> str:
    client = get_client()
    prompt = system_prompt or SYSTEM_PROMPT_FALLBACK
    image_url = encode_image_base64(image_bytes, mime)
    user_content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    text = caption or "Опиши что на фото. Если есть текст — распознай его (OCR)."
    user_content.append({"type": "text", "text": text})

    messages: list[dict[str, Any]] = []
    # include history as text-only prefix; current turn is multimodal
    hist = history.get_openai_messages(chat_id, system_prompt=prompt)
    messages.extend(hist)
    messages.append({"role": "user", "content": user_content})

    logger.info("muse image chat_id=%s mime=%s caption=%r", chat_id, mime, caption)
    resp = await client.chat.completions.create(
        model=settings.muse_model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
    )
    return (resp.choices[0].message.content or "").strip()


async def chat_with_audio(
    chat_id: int,
    audio_bytes: bytes,
    mime: str,
    caption: str | None = None,
    system_prompt: str | None = None,
) -> str:
    client = get_client()
    prompt = system_prompt or SYSTEM_PROMPT_FALLBACK
    # OpenRouter/Muse expects audio as base64 data url in multimodal content
    # Fallback: if model doesn't support audio input yet, send as transcription request
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    audio_url = f"data:{mime};base64,{b64}"
    user_content: list[dict[str, Any]] = [
        {"type": "input_audio", "input_audio": {"data": b64, "format": mime.split("/")[-1]}},
        {"type": "text", "text": caption or "Расшифруй аудио дословно, затем кратко перескажи смысл."},
    ]
    # Some providers use image_url-style for audio; keep both compat attempts
    # Primary: input_audio, secondary fallback handled by plain text if rejected
    hist = history.get_openai_messages(chat_id, system_prompt=prompt)
    messages: list[dict[str, Any]] = []
    messages.extend(hist)
    messages.append({"role": "user", "content": user_content})

    logger.info("muse audio chat_id=%s mime=%s bytes=%s", chat_id, mime, len(audio_bytes))
    try:
        resp = await client.chat.completions.create(
            model=settings.muse_model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=1024,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("audio input_audio failed, fallback: %s", e)
        # Fallback: ask model to handle as generic audio transcription request
        fallback_messages = history.get_openai_messages(chat_id, system_prompt=prompt)
        fallback_messages.append({"role": "user", "content": caption or "Расшифруй голосовое сообщение."})
        resp2 = await client.chat.completions.create(
            model=settings.muse_model,
            messages=fallback_messages,  # type: ignore[arg-type]
            max_tokens=1024,
        )
        return (resp2.choices[0].message.content or "").strip()


# --- Structured output helper ---
import pydantic  # noqa: F401

async def chat_structured(
    chat_id: int,
    user_text: str,
    schema: dict[str, Any],
    system_prompt: str | None = None,
) -> dict[str, Any]:
    client = get_client()
    prompt = system_prompt or SYSTEM_PROMPT_FALLBACK
    messages = _messages_for_chat(chat_id, user_text, system_prompt=prompt)
    logger.info("muse structured chat_id=%s schema_keys=%s", chat_id, list(schema.keys()))
    resp = await client.chat.completions.create(
        model=settings.muse_model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
        response_format={"type": "json_object"},  # type: ignore[arg-type]
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("structured parse failed raw=%r", raw[:500])
        return {"raw": raw}


# --- Live search ---

async def chat_with_search(
    chat_id: int,
    user_text: str,
    system_prompt: str | None = None,
) -> str:
    # web_search через OpenRouter на muse-spark зависает (stream body не закрывается,
    # проверено в логах: 12s timeout). Не тратим токены — сразу обычный chat.
    # Если модель поддерживает web_search (другая), можно вернуть extra_body путь.
    if "muse-spark" in settings.muse_model:
        prompt = (system_prompt or SYSTEM_PROMPT_FALLBACK) + (
            "\nПользователь просит свежие новости/поиск. Ответь исходя из знаний, "
            "укажи дату (МСК) и честно предупреди что без live-поиска данные могут быть неполными. "
            "Предложи уточнить в источниках."
        )
        return await chat_completion(chat_id, user_text, system_prompt=prompt)

    import asyncio

    client = get_client()
    prompt = system_prompt or SYSTEM_PROMPT_FALLBACK
    messages = _messages_for_chat(chat_id, user_text, system_prompt=prompt)
    logger.info("muse search chat_id=%s query=%r", chat_id, user_text[:120])
    tools = [{"type": "web_search"}]
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.muse_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=1024,
                extra_body={"tools": tools},  # type: ignore[arg-type]
            ),
            timeout=12,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
        logger.warning("web_search empty result, fallback plain")
    except asyncio.TimeoutError:
        logger.warning("web_search timeout 12s, fallback plain")
    except Exception as e:
        logger.warning("web_search not supported, fallback plain: %s", e)
    fallback_prompt = prompt + "\nПользователь просит свежие новости/поиск. Ответь максимально актуально, укажи что данные на сейчас и предложи уточнить."
    return await chat_completion(chat_id, user_text, system_prompt=fallback_prompt)


async def fetch_bytes(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(url)
        r.raise_for_status()
        mime = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        return r.content, mime
