# Autonomous Wind Forecast Operator — план реализации

> Для исполнителя: выполнять последовательно по задачам с проверкой результата и отдельными небольшими коммитами. Для агентного исполнения применять навык executing-plans. Этот документ описывает будущие изменения; приложение пока не реализовано.

**Цель:** воспроизводимый автономный прогноз почасовой нормализованной мощности двух турбин на 24–48 часов, replay февраля 2026 и проверяемый audit без будущей информации.

**Архитектура:** FastAPI и worker используют общий численный движок и immutable run context. OpenAI вызывает ограниченные tools; deterministic gate проверяет время, качество и разрешение публикации. PostgreSQL хранит задания и runs, snapshots и модели хранятся файлами.

**Стек:** Python, pandas/NumPy, LightGBM, ecCodes, FastAPI/Pydantic, PostgreSQL, React/TypeScript/Vite, Docker Compose. Обучение — NVIDIA Brev; inference — CPU.

## Приоритет и правила работы

- Сначала работающий вертикальный сценарий replay, затем интерфейс и дополнительные модели.
- Автоматический пересчёт входит в MUST HAVE: это прямое требование ТЗ.
- Каждый этап заканчивается конкретным артефактом, проверкой и коммитом; отправлять завершённые изменения в remote.
- Не выдавать архив observations/reanalysis за forecast; не использовать февральские факты для выбора модели.
- Команды ниже — целевые интерфейсы ещё не созданных компонентов. Они станут исполнимыми после соответствующих задач.

## 0. Зафиксировать входной контракт

**Ответственный:** team lead + ML. **Файлы:** `docs/data-contract.md`, `configs/site.yaml`, `configs/replay.yaml`.

- [ ] Получить у организаторов timezone исходных CSV и семантику начала/конца 10-минутного интервала.
- [ ] Уточнить задержку доступности SCADA, способ нормализации, номиналы турбин, ожидаемую единицу агрегата ВЭС.
- [ ] Уточнить время ежедневного origin, формат submission, доступность февральского ground truth и разрешение новых SCADA.
- [ ] Зафиксировать ответы; неизвестные параметры пометить явно. В строгом режиме отсутствие timezone/семантики времени блокирует запуск, а не заменяется молчаливым default.
- [ ] Задать полуоткрытые интервалы target и часы горизонта 1–48. Сохранить original timestamps и UTC.
- [ ] Разделить runtime `executed_at` и историческое `as_of`, фактическое `trained_at` и `training_cutoff`.

**Готово:** контракт задаёт однозначные часы и доступность. Пока ответы ожидаются, weather spike можно делать в явно указанном UTC.
**Коммит:** `docs: define temporal and target data contracts`.

## 1. Проверить погодный архив до большой разработки

**Ответственный:** data/backend. **Файлы:** `backend/weather/gfs.py`, `backend/weather/schema.py`, `scripts/weather_probe.py`, `tests/integration/test_gfs_snapshot.py`, `data/manifests/weather-probe.json`.

- [ ] Проверить конкретный цикл `2026-02-05T18:00Z` и его GRIB/IDX provenance; исходный f048 найден, но одного файла недостаточно.
- [ ] Скачать только U/V 100 м, U/V 10 м и TMP 2 м через IDX + HTTP Range; декодировать для координат обеих турбин.
- [ ] Собрать один horizon 48 часов относительно origin: учитывать возраст цикла, получать необходимые lead times и точки для интерполяции.
- [ ] Сохранить available_at каждого использованного объекта, источник времени, HTTP metadata, checksum, единицы, grid coordinates и метод интерполяции.
- [ ] Проверить нужные дни февраля и предфевральские обучающие периоды; измерить время, объём и пропуски.
- [ ] Если цикл неполный, выбрать более старый доступный цикл, только если он покрывает весь горизонт. Отсутствие допустимого цикла заканчивается явным BLOCKED.

**Проверка:** `pytest tests/integration/test_gfs_snapshot.py -q`; fixture содержит полный горизонт, finite значения, правильные единицы. Отдельный тест отвергает доступность после origin.
**Готово:** реальные погодные значения и manifest, а не только URL/индекс. При неудаче пересмотреть источник до остальных этапов.
**Коммит:** `feat: acquire auditable archived GFS snapshots`.

## 2. Подготовить SCADA и качество данных

**Ответственный:** ML/data. **Файлы:** `backend/data/scada.py`, `backend/data/quality.py`, `scripts/profile_scada.py`, `tests/unit/test_scada_hourly.py`.

- [ ] Прочитать CSV с явной схемой, сохранить raw hashes; ID не использовать как признак.
- [ ] Построить 10-минутную сетку отдельно для турбин; missing timestamps не считать нулевой мощностью.
- [ ] Агрегировать power/wind/temperature по согласованным часовым интервалам; вычислить coverage.
- [ ] Полные target требуют шесть валидных интервалов; неполные исключаются с причиной, длинные разрывы не интерполируются.
- [ ] Сохранить флаги низкой мощности при ветре и застывания датчиков, не удаляя эксплуатационные события автоматически.

**Проверка:** `pytest tests/unit/test_scada_hourly.py -q`; проверить 6/6, 5/6, пустой час, границу суток, доступность конца интервала и отсутствие дубликатов.
**Готово:** profile подтверждает 142360/149499 строк, конец 31.01.2026, hourly coverage и исходные hashes.
**Коммит:** `feat: validate and aggregate SCADA history`.

## 3. Создать Replay Engine и запреты утечки

**Ответственный:** backend. **Файлы:** `backend/domain/run.py`, `backend/replay/context.py`, `backend/replay/selectors.py`, `backend/validation/temporal.py`, `tests/temporal/test_as_of.py`.

- [ ] Определить immutable context: run_id, as_of, horizon, timezone policy, data policy, model ID.
- [ ] Отбирать SCADA по interval_end и available_at; погоду по available_at всех используемых объектов.
- [ ] Проверять training cutoff, preprocessing/calibration cutoff, feature provenance.
- [ ] Запретить агенту задавать иной cutoff и произвольные пути к данным.
- [ ] Сохранить статусы PASS/FAIL/ASSUMPTIONS; неизвестную историческую доступность не выдавать за VERIFIED.

**Обязательные тесты:** поздний выпуск отвергается; init_time до origin недостаточен; SCADA на границе недоступна до окончания интервала; scaler с поздним cutoff отвергается; изменение будущих SCADA не меняет forecast inputs; `target-24h` недопустим для +48h; origin с разными offset обозначает один и тот же момент UTC.
**Команда:** `pytest tests/temporal -q`.
**Коммит:** `feat: enforce point-in-time replay boundaries`.

## 4. Baselines, folds и обучающая выборка

**Ответственный:** ML в Brev. **Файлы:** `backend/features/build.py`, `backend/forecasting/baselines.py`, `backend/replay/evaluation.py`, `scripts/build_dataset.py`, `configs/train.yaml`, `configs/backtest.yaml`, `tests/unit/test_splits.py`.

- [ ] Создать origin × turbine × horizon dataset из допустимых snapshots и target.
- [ ] Реализовать persistence и простой weather baseline.
- [ ] Задать expanding folds до февраля; purge target по фактической доступности на cutoff.
- [ ] Зафиксировать одинаковые evaluation masks и отдельно считать долю успешно выданных прогнозов.
- [ ] Подготовить MAE/RMSE/bias/skill по турбинам и горизонтам; агрегат считать только по определённому контракту.

**Проверка:** `pytest tests/unit/test_splits.py -q`; ни один train label не пересекает cutoff, февраль отсутствует, masks одинаковые.
**Готово:** baseline metrics JSON + dataset manifest с реальным покрытием архива.
**Коммит:** `feat: build leakage-safe training and baseline evaluation`.

## 5. Обучить и экспортировать LightGBM в Brev

**Ответственный:** ML. **Файлы:** `scripts/train.py`, `scripts/evaluate.py`, `backend/forecasting/model.py`, `backend/forecasting/intervals.py`, `tests/unit/test_model_bundle.py`. **Инструкция:** [brev-training.md](brev-training.md).

- [ ] Обучить weather-only pooled LightGBM: turbine ID, NWP ветер/температура, календарь, horizon и возраст выпуска.
- [ ] Подбирать параметры только на предфевральских folds; зафиксировать seed, versions, feature order и cutoff.
- [ ] Сравнить с baseline на одинаковых target; сохранить и ухудшения, и улучшения.
- [ ] При наличии времени калибровать интервалы по out-of-sample residuals; иначе явно вернуть unavailable для интервалов, а не ложную уверенность.
- [ ] Экспортировать model bundle, manifests и metrics; проверить CPU inference в backend.

**Проверка:** `pytest tests/unit/test_model_bundle.py -q`; несовместимая feature schema блокируется, fixture предсказаний повторяется в заданном допуске.
**Готово:** экспортированная модель и измеренные метрики. Февральские метрики отсутствуют до получения факта.
**Коммит:** `feat: train and package wind forecasting model` (большие артефакты — вне Git, в Git manifest).

## 6. Закончить вертикальный CLI replay

**Ответственный:** backend. **Файлы:** `replay.py`, `backend/workflow/runner.py`, `backend/validation/forecast.py`, `backend/storage/artifacts.py`, `tests/integration/test_replay.py`.

- [ ] Соединить context → weather → validation → features → model → output validation → forecast/audit export.
- [ ] На каждую турбину вернуть ровно horizon интервалов с target_start/end, model ID и run ID.
- [ ] Проверить диапазон, finite, completeness; логировать clipping и блокировать крупные нарушения.
- [ ] Сохранить hashes всех входов и конфигурации. Выходной audit не содержит ключей API.

**Целевая команда:** `python replay.py --as-of "2026-02-06T00:00:00+05:00" --horizon 48` (offset используется только после согласования контракта).
**Проверка:** `pytest tests/integration/test_replay.py -q`; cached replay повторяет числа без сети; изменение будущего датасета не меняет результат; stale SCADA видны в audit.
**Коммит:** `feat: deliver reproducible forecast replay CLI`.

## 7. Подключить OpenAI Operator

**Ответственный:** agent/backend. **Файлы:** `backend/agent/operator.py`, `backend/agent/tools.py`, `backend/agent/prompts.py`, `tests/integration/test_agent_guards.py`.

- [ ] Вызовы через Responses API, строгие tool schemas: context, weather, input validation, forecast, output validation, publish, recalculation.
- [ ] Передавать агенту ID артефактов и диагностические сводки; не передавать будущие факты или секреты.
- [ ] Publish gate повторно проверяет deterministic invariants независимо от решения LLM.
- [ ] Ограничить число tool calls, время и разрешённые модели; записывать tool trace и причины действий.
- [ ] Продемонстрировать реальный happy path и разрешённое восстановление при недоступном свежем выпуске. Введённый для демо сбой обозначить явно.

**Проверка:** `pytest tests/integration/test_agent_guards.py -q`; модель не может отменить FAIL, менять as_of, публиковать неизвестный artifact. Live smoke с ключом выполняется отдельно от offline tests.
**Коммит:** `feat: orchestrate forecasts with guarded OpenAI tools`.

## 8. API, jobs и обязательный автоматический пересчёт

**Ответственный:** backend. **Файлы:** `backend/api/main.py`, `backend/storage/models.py`, `backend/workflow/worker.py`, `backend/workflow/scheduler.py`, `tests/integration/test_recalculation.py`.

- [ ] Создать PostgreSQL entities runs, points, snapshots, models, events, validation, jobs.
- [ ] API create/status/forecast/audit/events/recalculate/backtests; create возвращает 202 и run ID.
- [ ] Worker атомарно забирает job; повторная обработка идемпотентна.
- [ ] Scheduler проверяет новые входы; изменившийся snapshot hash создаёт новый run с parent_run_id.
- [ ] Повтор того же события не создаёт дублей. Старый forecast не перезаписывается.
- [ ] В live новый run получает новый as_of. В replay события идут по simulated available_at; новые данные не подмешиваются в старый origin.

**Проверка:** `pytest tests/integration/test_recalculation.py -q`; новый вход создаёт одну ревизию, повтор события — ноль, старый прогноз неизменен, падение worker не теряет задание.
**Коммит:** `feat: persist forecast runs and automatically recalculate`.

## 9. Минимальный dashboard

**Ответственный:** frontend. **Файлы:** `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/components/ReplayControls.tsx`, `ForecastChart.tsx`, `AuditPanel.tsx`, `AgentTimeline.tsx`.

- [ ] Дата с timezone, horizon 24/48, запуск и polling статуса.
- [ ] График турбин и допустимого агрегата, интервал при его наличии.
- [ ] Audit с init_time, available_at, SCADA cutoff, моделью и статусом временных проверок.
- [ ] Tool timeline и предупреждения; показать связь пересчитанного run с предыдущим.
- [ ] Февральские факты не изображать; оценочные actuals, когда появятся, получает отдельный evaluator.

**Проверка:** `npm --prefix frontend run build`; вручную проверить запуск, ошибку источника, stale SCADA и revison через реальные API responses.
**Коммит:** `feat: add forecast replay and audit dashboard`.

## 10. Февральский пакет и воспроизводимая сдача

**Ответственный:** вся команда; integration owner проверяет чистый запуск. **Файлы:** `compose.yaml`, backend/frontend Dockerfiles, lockfiles, `.env.example`, `README.md`, `docs/evaluation.md`, `data/fixtures/`, `tests/integration/test_offline_demo.py`.

- [ ] Зафиксировать модель до первого origin; выполнить origin 31 января и ежедневные февральские origin по контракту.
- [ ] Хранить прогнозы по run ID и горизонту; не усреднять пересекающиеся origin молча. Для оценки исключить target вне тестового февраля и сообщить coverage.
- [ ] Сохранить forecasts и audit manifests; отсутствие ground truth обозначить, оценки accuracy не выдумывать.
- [ ] Подготовить небольшой offline fixture, model bundle retrieval, hashes, инструкции live OpenAI и offline trace.
- [ ] Проверить `docker compose up --build`, CLI и UI с чистого checkout.
- [ ] Выполнить `pytest tests/temporal tests/integration -q`; live network tests вынести в отдельную opt-in группу, чтобы offline replay не зависел от сети.
- [ ] Проверить README: prerequisites, data placement, configuration, commands, expected artifacts, limitations, external services, no-leakage assumptions.
- [ ] Записать короткий сценарий защиты: replay → provenance → agent tools → обновление входов → новая ревизия → воспроизведение.

**Коммит:** `docs: finalize reproducible hackathon submission`.

## Матрица критериев жюри

| Критерий | Баллы | Задачи | Доказательство |
|---|---:|---|---|
| Соответствие и работоспособность | 25 | 0–3, 6–8, 10 | Реальный 48h replay и автоматическая ревизия |
| Техническая реализация | 25 | 1–8 | ML metrics, temporal tests, guarded agent, error recovery |
| README и воспроизводимость | 25 | 5–6, 10 | Чистый запуск, hashes, model bundle, offline fixture |
| Ценность и применимость | 15 | 0, 4–6, 9 | Forecast станции, baseline comparison, понятный audit |
| Развитие и оригинальность | 10 | 3, 7–9 | Проверяемая временная доступность и решения оператора |

## Распределение команды и зависимости

- Lead закрывает контракт и integration; data/backend делает архив и replay; ML обучает в Brev; frontend строит UI по фиксированному API.
- Weather schema и immutable context согласовать до одновременной работы. UI можно разрабатывать по явно помеченным fixtures, но финальная сдача использует реальные endpoints.
- Критический путь: 0 → 1 → 2/3 → 4 → 5 → 6 → 7/8 → 10. Frontend подключается после определения контрактов, не блокирует CLI.
- При нехватке времени убрать второй источник, SHAP, ансамбли и сложные интервалы. Не убирать temporal checks, автоматический пересчёт, реальную агентность и воспроизводимость.
