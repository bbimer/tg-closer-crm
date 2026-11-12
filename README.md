# ⚡ TG Closer CRM & Community Intelligence Engine (v2.0)

Data-driven маркетинговый и разведывательный движок для Telegram-клоузеров арбитражных связок и институциональных продуктов, построенный по тактическому плейбуку **Syndicate Intelligence** (`Fast Track Closer`).

Система осуществляет поиск лидов по болевым точкам трейдеров в независимых крипточатах, отслеживает микротесты на токсичность модерации, синхронизирует личные переписки и ведет сквозную аналитику конверсий и причин отвалов (`drop_reason`).

---

## 🏗 Архитектура системы

```
tg-closer-crm/
├── config/
│   ├── __init__.py
│   └── settings.py              # Конфигурация (.env, задержки, пути)
├── data/
│   └── crm.db                   # SQLite база данных (WAL режим)
├── src/
│   ├── collector/
│   │   ├── session_pool.py       # Пул Telethon сессий с ротацией и кулдаунами
│   ├── storage/
│   │   ├── models.py             # DDL схемы, Pydantic модели и стадии воронок
│   │   └── repository.py         # Thread-safe репозиторий SQLite (стандартная библиотека)
│   ├── intel/
│   │   ├── chat_harvester.py     # Дамп сообщений через OffsetID, трекинг сленга и тредов
│   │   ├── pain_miner.py         # Извлечение лидов по болям (ликвидации, стопы, CEX)
│   │   └── micro_tester.py       # Форматы тестов A/B/C и проверка выживаемости (is_alive)
│   ├── crm/
│   │   ├── dm_sync.py            # Read-only синхронизация личных DM-диалогов
│   │   └── pipeline_tracker.py   # Ведение стадий воронок, история переходов, причины отвалов
│   └── utils/
│       └── formatting.py         # Rich-таблицы, визуализация воронок и метрик
├── tests/                        # Полный тестовый набор pytest
├── cli.py                        # Единый CLI интерфейс управления
├── requirements.txt
└── .env
```

---

## 🎯 Поддерживаемые воронки конверсии

### 1. `fast_track` (Розничные трейдеры, чеки $20 $\to$ $1,000–$2,000+)
* `In_DM` — Первое касание в личке после триггерного комментария в чате.
* `Legend_&_Media` — Презентация бэкграунда (8 лет в крипте, ферма физических iPhone, Apple Secure Enclave vs AdsPower, лимит 4 круга по цифровой гигиене).
* `Test_20` — Закрытие на проверочный круг $20 со счета лида (15 минут, +$0.75).
* `Passed` — Фиксация прибыли лидом на его балансе.
* `Volume_500_2k` — Контраст нулевой рефки с двадцатки и открытого окна в азиатской ноде ($15k–$25k), перевод на рабочий объем $1,000–$2,000.
* `Closed` — Сделка закрыта (Won).
* `Lost` — Отвал с фиксацией причины (`drop_reason`).

### 2. `delta_neutral` (Крупный капитал, $50,000+)
* `In_DM` $\to$ `Unit_Econ_Breakdown` $\to$ `Risk_Framework_Proof` $\to$ `Tech_Session_Booked` $\to$ `Closed` / `Lost`.

---

## 🚀 Быстрый старт

### 1. Инициализация базы данных
```bash
python cli.py init-db
```

### 2. Разведка и сбор сообщений из крипточата
```bash
# Дамп сообщений из целевого чата
python cli.py harvest --chat cryptonafft --limit 500 --batch 100
```

### 3. Майнинг болей и поиск квалифицированных лидов
```bash
# Анализ выбитых стопов, сливов депо, манипуляций бирж (Bybit, OKX, Binance)
python cli.py mine-pains --chat cryptonafft --limit 1000

# Просмотр найденных кандидатов
python cli.py leads --segment clean_retail
```

### 4. Синхронизация лички клоузера в CRM
```bash
python cli.py sync-dms --limit 50 --msgs 25
```

### 5. Управление воронкой и статусами лидов
```bash
# Продвижение лида по стадиям
python cli.py pipeline advance --lead 12345678 --stage "Legend_&_Media" --note "Скинул видео фермы"
python cli.py pipeline advance --lead 12345678 --stage Test_20 --note "Инструкция отправлена"
python cli.py pipeline advance --lead 12345678 --stage Passed --note "Зафиксировал +$0.75"

# Закрытие сделки
python cli.py pipeline close --lead 12345678 --note "Зашел на $2,000 Bybit"

# Если лид отвалился — фиксация точной причины
python cli.py pipeline drop --lead 98765432 --reason refused_test_20 --note "Испугался заводить 20 USDT"

# Вывод аналитики конверсии и узких мест
python cli.py pipeline stats
```

### 6. Микротестирование чатов на токсичность (Форматы A/B/C)
```bash
# Отправка проверочного вопроса (Format A)
python cli.py micro-test send --chat target_chat --format Format_A

# Проверка выживаемости (is_alive, ответы, удаление админами)
python cli.py micro-test check --id 1

# Список всех тестов
python cli.py micro-test list
```

---

## 🧪 Запуск тестов

```bash
python -m pytest -v
```
Все 10 тестов проверяют:
* CRUD и индексы SQLite (Group Messages, CRM Dialogues, Micro Tests, Leads).
* Алгоритм извлечения сленга и горячих веток.
* Regex-майнер болей, детекцию бирж и сегментацию Retail vs Whale.
* Жизненный цикл воронки, расчет конверсий и учет узких мест отвала.
