# Muse — Telegram Bot для группы 5 чел

Групповой Telegram бот на `meta/muse-spark-1.2` (через OpenRouter, мультимодальный).

## Что умеет
- **Текст** — чат с историей группы (до 20 сообщений), стриминг ответов
- **Фото** — `Image Understanding` + `OCR` (file_id -> base64 -> Muse)
- **Документы** — картинки/PDF/текст
- **Голосовые** — `Audio Understanding` нативно (без Whisper) -> расшифровка + пересказ
- **Live Search** — `web_search` (триггеры: "найди", "поиск", "новости" или `/search`)
- **Tools / Function Calling** — `get_current_time`, `calculate` (+ `/calc`), `Structured Output (JSON)` (`/json`)

## Быстрый старт (локально без Docker)
```bash
cp .env.example .env  # заполни TELEGRAM_TOKEN и OPENROUTER_API_KEY
# требует python 3.12 + pip
pip install -r requirements.txt
python -m app.main
```

## Docker (рекомендуется)
```bash
cp .env.example .env  # заполни ключи
docker compose up -d --build
docker compose logs -f
```

## Деплой на другой VPS
```bash
git clone https://github.com/jadykov/Muse.git
cd Muse
cp .env.example .env
nano .env  # TELEGRAM_TOKEN, OPENROUTER_API_KEY
docker compose up -d --build
# проверка
docker compose ps
docker compose logs -f bot
```

## ENV
| Переменная | Обязательно | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | да | от @BotFather |
| `OPENROUTER_API_KEY` | да | ключ OpenRouter |
| `MUSE_MODEL` | нет | по умолчанию `meta/muse-spark-1.2-contributor` |
| `ALLOWED_CHAT_IDS` | нет | фильтр чатов, напр. `-100123, -100456` |
| `ALLOWED_USER_IDS` | нет | фильтр юзеров |
| `SYSTEM_PROMPT` | нет | переопределение system prompt |

## Команды бота
`/start` `/help` `/clear` `/search <запрос>` `/calc <выражение>` `/json <запрос>`

## Структура
```
app/
  main.py      # Telegram handlers + стриминг + групповой фильтр
  muse.py      # OpenAI client -> Muse 1.2 (text/image/audio/search/structured)
  tools.py     # Function Calling (time, calculator) + TOOLS схемы
  history.py   # in-memory история на 20 сообщ. на чат
  config.py    # pydantic-settings
Dockerfile
docker-compose.yml
requirements.txt
```