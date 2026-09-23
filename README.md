# Autonomous Wind Forecast Operator

Проект команды JFn для энергетического трека HackAlem AI: почасовой прогноз ВЭС на 24–48 часов, исторический Forecast Replay без использования будущих данных и оператор на OpenAI Responses API с ограниченным набором инструментов.

## Что работает

- FastAPI, SQLite, фоновый worker, идемпотентные запуски и аудит событий.
- Строгая проверка времени доступности NOAA GFS для исторического replay.
- OpenAI-оператор выбирает входы, запускает численный движок, проверяет и публикует результат. Числа мощности вычисляет только backend.
- React-dashboard с двумя режимами: явно помеченный `Demo` и подключение к API.
- Безопасная загрузка зарегистрированных model bundles; отсутствующие, изменённые или несовместимые входы блокируются.

В репозитории нет опубликованной обученной модели для рабочих GFS-входов. Поэтому API выдаёт численный прогноз только с совместимым bundle, который оператор сервиса отдельно поместил в `data/runtime/models/`. Старые метрики LightGBM на наблюдаемой SCADA-погоде не являются оценкой рабочего прогноза по GFS и не используются как заявление о точности.

## Быстрая проверка

Требуется Python 3.13. Команды запускаются из корня репозитория:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements-backend.txt
python -m unittest discover -s tests -v
```

Тесты используют маленькую линейную service fixture и имитированный Responses tool-call loop. Они проверяют полный путь до 96 сохранённых точек, но не изображают fixture как обученную модель или измеренную точность.

Офлайн replay проверяет погодный snapshot и временные ограничения. Он не строит прогноз мощности:

```bash
python replay.py \
  --as-of "2026-02-06T00:00:00Z" \
  --horizon 48 \
  --snapshot data/fixtures/noaa-gfs-20260206T000000Z-h48.json \
  --output-dir artifacts/replay-2026-02-06
```

Для загрузки из архива NOAA установите `requirements-weather.txt` и запустите ту же команду без `--snapshot`. Подробности: [исторический replay погоды](docs/weather-replay.md).

## Backend и worker

Настоящий оператор требует ключ OpenAI. Перед запуском зарегистрируйте совместимый snapshot и model bundle под выбранными ID:

```text
data/runtime/snapshots/<weather_snapshot_id>.json
data/runtime/models/<model_id>.json
```

Затем запустите сервис с фоновым worker:

```bash
export OPENAI_API_KEY="..."
FORECAST_DATA_DIR=data/runtime .venv/bin/uvicorn backend.api.app:app --host 127.0.0.1 --port 8011
```

Если ключ не задан, обычный API-run завершится блокировкой; автоматической подмены реального OpenAI-вызова fixture-ответом нет. Контракт запросов и пример `POST /forecast-runs` приведены в [документации API](docs/backend-api.md). Отдельная команда live/recorded smoke и значения execution labels описаны в [документации оператора](backend/agent/README.md).

## Frontend

Dashboard проксирует `/api` на `http://127.0.0.1:8011`. Для режима API обязательно передайте ID реально зарегистрированной модели:

```bash
cd frontend
npm ci
VITE_MODEL_ID="<registered-model-id>" VITE_WEATHER_SNAPSHOT_ID="gfs-feb6" npm run dev
```

`gfs-feb6` — ID включённого погодного fixture, а не обученная модель. Для просмотра интерфейса без backend выберите `Demo`: экран явно помечает синтетические значения и не переключается на них при ошибке API.

## Документация

- [Архитектура и анализ данных](docs/architecture.md)
- [План реализации и критерии готовности](docs/implementation-plan.md)
- [План обучения в NVIDIA Brev](docs/brev-training.md)
- [Forecast API, worker и SQLite](docs/backend-api.md)
- [Guarded OpenAI operator](backend/agent/README.md)
