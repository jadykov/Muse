from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_token: str = Field("", alias="TELEGRAM_TOKEN")
    openrouter_api_key: str = Field("", alias="OPENROUTER_API_KEY")
    muse_model: str = Field("meta/muse-spark-1.2-contributor", alias="MUSE_MODEL")
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    allowed_chat_ids: str = Field("", alias="ALLOWED_CHAT_IDS")
    allowed_user_ids: str = Field("", alias="ALLOWED_USER_IDS")
    system_prompt: str = Field(
        "Ты — Muse Spark 1.2, помощник в групповом Telegram-чате на 5 человек. "
        "Отвечай кратко, по делу, на русском (если спрашивают на другом — отвечай на нем). "
        "Умеешь описывать фото, расшифровывать голосовые, искать в интернете. "
        "Форматируй ответы в Telegram MarkdownV2: жирный как *жирный*, курсив как _курсив_, `код`, ```блок кода```, [текст](url). "
        "НИКОГДА не используй Markdown-заголовки (#, ##, ###) — вместо них используй *жирный*. "
        "Списки делай через • или - без заголовков. Не используй HTML.",
        alias="SYSTEM_PROMPT",
    )
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    def get_allowed_chat_ids(self) -> set[int]:
        if not self.allowed_chat_ids.strip():
            return set()
        return {int(x.strip()) for x in self.allowed_chat_ids.split(",") if x.strip().lstrip("-").isdigit()}

    def get_allowed_user_ids(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        return {int(x.strip()) for x in self.allowed_user_ids.split(",") if x.strip()}


settings = Settings()
