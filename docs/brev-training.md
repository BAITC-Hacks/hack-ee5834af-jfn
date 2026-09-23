# Обучение численной модели в NVIDIA Brev

Статус: 23 сентября 2026 года на Brev обучена pooled LightGBM на двух CSV с фактическими SCADA wind/temperature. `model.txt`, схема входа, манифест и проверочный CPU fixture находятся в `artifacts/models/brev-scada-pooled-lgbm-20260201/`; код обучения, конфигурация и тесты — в `ml/`; исходные CSV — в `data/training/`. Общая MAE на 4 402 validation-часах равна 0,030807 при **наблюдаемой погоде**. Это не backtest архивного GFS и не оценка точности рабочего API с прогнозной погодой. Backend запускает эту модель только как экспериментальную с предупреждением; отдельное обучение на архивном GFS и его результаты описаны в [gfs-training.md](gfs-training.md).

## Назначение

Brev — вычислительная среда для обработки погоды, подготовки признаков и обучения LightGBM. OpenAI вызывается через API в agent layer. NVIDIA foundation models и модели других AI-провайдеров не используются.

Создание среды: https://brev.nvidia.com/environment/new

Для начала достаточно одной машины с 16–32 ГБ RAM и диском порядка 50 ГБ для выбранных GRIB-полей, кэша и артефактов. Это стартовая оценка, которую нужно уточнить по weather spike; глобальный архив целиком не скачивать. GPU для табличного baseline не обязателен. Если команда получает GPU-кредиты, можно использовать доступную однопроцессорную GPU-машину, но начать с CPU LightGBM и не тратить время на CUDA-сборку до измерения bottleneck. Конкретный тип и стоимость выбирать по фактически доступным квотам.

## Подготовка среды

1. Создать environment через Brev, проверить выбранные ресурсы и квоту команды.
2. Подключиться через поддерживаемый SSH/IDE; использовать постоянный workspace, проверить фактическое имя пользователя.
3. Клонировать `https://github.com/BAITC-Hacks/hack-ee5834af-jfn.git` и переключиться на конкретный commit SHA.
4. Сверить исходные CSV в `data/training/` по SHA-256 с `data_manifest.json`. Ключи не коммитить; новые крупные наборы данных выносить во внешнее хранилище.
5. Установить зависимости из `ml/requirements-brev.txt` и системный `libgomp1`; сохранить версии Python и LightGBM.
6. Держать notebooks только для исследования. Официальный train запускается скриптом из репозитория.

Согласно документации Brev, workspace по умолчанию `/home/ubuntu/workspace` сохраняется между остановками. Всё равно экспортировать финальные артефакты вне instance перед удалением. [Environments](https://docs.nvidia.com/brev/concepts/environments), [Instance management](https://docs.nvidia.com/brev/cli/instance-management).

## Протокол обучения без утечки

- После уточнения timezone перевести события в UTC, сохранив исходные timestamp и правило преобразования.
- Привести SCADA к полным часам; неполные target исключить, причины сохранить.
- Сформировать строки `origin × turbine × horizon`. Weather snapshot обязан быть доступен на origin; target завершён и доступен к training cutoff.
- Основная модель использует прогнозную погоду и календарь, без обязательных свежих SCADA. Это необходимо из-за отсутствия февраля в CSV.
- Начальная схема: expanding train; validation на ноябре и декабре 2025; calibration на январе 2026 с cutoff, предшествующим первому финальному origin. В каждом fold исключить train-примеры с label availability после границы. При недостаточном покрытии архива уменьшить train-период и записать фактический диапазон.
- Гиперпараметры и выбор признаков зафиксировать до финального февраля. Не выбирать модель по скрытым февральским фактам.
- Для первого origin 31 января cutoff определяется точным временем запуска и задержкой SCADA. Финальная фиксированная модель может использовать только доступные к этому origin данные. Поздние часы 31 января не включать в неё задним числом.
- Базовые сравнения: persistence и простая модель по архивному прогнозному ветру. Все сравнения на одинаковых target и origin.
- Метрики: MAE, RMSE, bias, skill относительно baseline; interval score и coverage, когда интервалы готовы.
- Основной point forecast сравнивать по MAE. Если LightGBM не лучше baseline, опубликовать честный результат и использовать лучшую проверенную модель; не обещать улучшение заранее.

## Контракт будущих команд

Эти команды — цели реализации, сейчас скриптов ещё нет:

```bash
python scripts/profile_scada.py --config configs/site.yaml
python scripts/build_dataset.py --config configs/train.yaml
python scripts/train.py --config configs/train.yaml
python scripts/evaluate.py --config configs/backtest.yaml
```

Ожидаемые результаты соответственно: profile JSON; train/validation manifests и Parquet; model bundle; отчёт с метриками по турбинам и горизонтам.

## Model bundle для передачи backend

`artifacts/models/<model_id>/` должен содержать:

- `model.txt` — LightGBM model;
- `metadata.json` — model ID, реальное trained_at, training cutoff, git SHA, версии, seed, train range;
- `feature_schema.json` — имена, порядок, типы, единицы и версия признаков;
- `metrics.json` — folds, sample counts, MAE/RMSE/bias/skill, coverage;
- `data_manifest.json` — hashes SCADA, weather snapshots, split policy;
- `calibration.json` — если интервалы включены, параметры и cutoff калибровки;
- `checksums.sha256` — целостность bundle.

Передача: скопировать bundle и fixtures в доступное команде хранилище; небольшой manifest и путь получения зафиксировать в Git. Проверить загрузку модели в CPU backend и численное совпадение на фиксированном fixture с заранее заданным допуском (например, 1e-8 для того же CPU runtime).

## Критерий завершения обучения

- [ ] Архивный weather dataset и его покрытие подтверждены.
- [ ] Temporal tests проходят до обучения.
- [ ] Baseline и LightGBM сравниваются на одинаковых folds.
- [ ] Февраль не использовался для обучения/подбора/калибровки.
- [ ] Bundle экспортирован и проверен в backend на CPU.
- [ ] Эксперимент воспроизводится по git SHA, конфигурации и manifests.
- [ ] Результаты сохранены вне instance; ненужные вычисления остановлены командой.
