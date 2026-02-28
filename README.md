# CRM Bot — Telegram + FastAPI

Система управления заявками. Telegram-бот + REST API на одном сервере.

## Структура проекта

```

## Quality Checks (Windows PowerShell)

```powershell
# install dev tools
pip install -r requirements-dev.txt

# lint
python -m ruff check .

# typecheck
python -X utf8 -m mypy app main.py

# encoding (UTF-8 without BOM)
python -m scripts.check_encoding

# migrations (offline SQL render)
python -m scripts.migrations_check

# smoke
python -m scripts.smoke

# all checks
python -m scripts.check
```
crm_bot/
├── main.py                         # Точка входа — запускает бота и API
├── .env.example                    # Шаблон переменных окружения
├── requirements.txt
├── docker-compose.yml
│
└── app/
    ├── core/
    │   └── config.py               # Конфиг через pydantic-settings
    │
    ├── db/
    │   ├── database.py             # Подключение к БД, get_db()
    │   ├── models/
    │   │   └── lead.py             # Lead, Manager, LeadHistory, LeadComment
    │   └── repositories/
    │       └── lead_repository.py  # Все запросы к БД
    │
    ├── services/
    │   └── lead_service.py         # Бизнес-логика + отправка в Telegram
    │
    ├── bot/
    │   ├── handlers/
    │   │   └── lead_callbacks.py   # Обработка кнопок под карточками
    │   ├── keyboards/
    │   │   └── lead_keyboards.py   # Inline-кнопки для карточек
    │   └── utils/
    │       └── card.py             # Форматирование карточки клиента
    │
    └── api/
        ├── deps.py                 # Зависимости FastAPI (auth, bot)
        ├── routes/
        │   └── leads.py            # Эндпоинты /api/v1/leads
        └── schemas/
            └── lead_schemas.py     # Pydantic-схемы запросов и ответов
```

## Быстрый старт

```bash
# 1. Копируем конфиг
cp .env.example .env
# Заполняем .env — токен бота, ID группы и топиков

# 2. Запускаем через Docker
docker-compose up -d

# 3. Или локально
pip install -r requirements.txt
python main.py
```

## API

Документация доступна по адресу: `http://localhost:8000/api/docs`

Пример создания заявки:
```bash
curl -X POST http://localhost:8000/api/v1/leads \
  -H "X-API-Key: your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"name":"Иван","phone":"+7 999 123-45-67","source":"website"}'
```

## Поток заявки

```
Любой источник
   → POST /api/v1/leads
      → БД (статус: new)
      → Карточка в TG топик "Первичные"
         → Менеджер нажимает кнопку
            → Статус меняется
            → Карточка переезжает в нужный топик
```
