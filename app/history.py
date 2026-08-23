from collections import defaultdict, deque
import logging
import time

logger = logging.getLogger(__name__)

MAX_HISTORY = 20
MAX_CHARS_PER_MSG = 4000


class ChatHistory:
    def __init__(self, max_history: int = MAX_HISTORY):
        self._store: dict[int, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.max_history = max_history

    def add(self, chat_id: int, role: str, content: str | list, name: str | None = None) -> None:
        content_str = content if isinstance(content, str) else str(content)[:MAX_CHARS_PER_MSG]
        if isinstance(content_str, str) and len(content_str) > MAX_CHARS_PER_MSG:
            content_str = content_str[:MAX_CHARS_PER_MSG] + "…"
        entry: dict = {"role": role, "content": content_str, "ts": time.time()}
        if name:
            entry["name"] = name
        self._store[chat_id].append(entry)
        logger.debug("history add chat=%s role=%s len=%s", chat_id, role, len(self._store[chat_id]))

    def get(self, chat_id: int) -> list[dict]:
        return list(self._store[chat_id])

    def get_openai_messages(self, chat_id: int, system_prompt: str | None = None) -> list[dict]:
        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        for entry in self._store[chat_id]:
            msgs.append({"role": entry["role"], "content": entry["content"]})
        return msgs

    def clear(self, chat_id: int) -> None:
        self._store[chat_id].clear()

    def size(self, chat_id: int) -> int:
        return len(self._store[chat_id])


history = ChatHistory()
