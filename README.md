# Autonomous Wind Forecast Operator

Проект команды JFn для энергетического трека HackAlem AI: автономный почасовой прогноз ВЭС на 24–48 часов и исторический Forecast Replay без использования будущих данных.

## Текущий статус

Реализован вертикальный сценарий: точечная загрузка архивного NOAA GFS, строгие временные проверки, воспроизводимый audit и CPU inference сохранённого LightGBM bundle через FastAPI worker. Прогнозы с GFS помечены экспериментальными: модель обучалась на наблюдаемых почасовых SCADA wind/temperature, а точность на архивной прогнозной погоде ещё не измерена.

- [Архитектура и полный анализ данных](docs/architecture.md)
- [План реализации и критерии готовности](docs/implementation-plan.md)
- [План обучения в NVIDIA Brev](docs/brev-training.md)
- [Запуск исторического replay погоды](docs/weather-replay.md)
- [Forecast API, worker и SQLite](docs/backend-api.md)
- [Исходное обсуждение архитектуры — issue #1](https://github.com/BAITC-Hacks/hack-ee5834af-jfn/issues/1)

## Ключевые решения

- Численный прогноз: LightGBM, обучение в NVIDIA Brev; inference должен работать на CPU.
- OpenAI: оператор с вызовами инструментов, без генерации численной мощности.
- Погода: архивные оперативные выпуски NOAA GFS с проверкой времени доступности.
- Backend: FastAPI, worker, PostgreSQL; frontend: React/TypeScript.
- Автоматический пересчёт при обновлении входов — обязательная часть MVP.
- CSV заканчиваются 31 января 2026 года. Февральские метрики нельзя публиковать без получения ground truth.

## Приоритет по ТЗ

Работоспособность — 25 баллов; техническая реализация — 25; README и воспроизводимость — 25; применимость — 15; потенциал развития и оригинальность — 10.

Первыми реализуются получение архивной погоды, временные проверки, baseline и воспроизводимый replay. Dashboard подключается к уже работающему процессу.

## Быстрый запуск

Офлайн-проверка готового snapshot не требует дополнительных пакетов:

```bash
python replay.py \
  --as-of "2026-02-06T00:00:00Z" \
  --horizon 48 \
  --snapshot data/fixtures/noaa-gfs-20260206T000000Z-h48.json \
  --output-dir artifacts/replay-2026-02-06
```

Для загрузки из архива NOAA установите `requirements-weather.txt` и уберите параметр `--snapshot`. Отдельно зарегистрируйте weather snapshot и модель в backend по инструкции [Forecast API](docs/backend-api.md).
