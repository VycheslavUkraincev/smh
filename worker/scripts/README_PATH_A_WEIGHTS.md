# Скачивание весов Path A

Простой скрипт: кладёт официальные веса моделей Path A в папку (обычно `/weights`).
GPU не арендует. Секреты не нужны.

## Что качает (ядро)

1. LaMa (`big-lama.pt`) — ~200 MB
2. GFPGAN v1.4 — ~340 MB
3. Real-ESRGAN x2plus — ~64 MB

Опционально:
- `--with-ddcolor` — цвет
- `--with-rfpp` — RestoreFormer++ вместо/рядом с GFPGAN

CodeFormer сюда не входит (это legacy, не коммерческий default).

## Как запустить

Из корня репо:

```bash
python3 worker/scripts/download_path_a_weights.py --dest /weights
```

Только посмотреть план (без скачивания):

```bash
python3 worker/scripts/download_path_a_weights.py --dry-run --list
```

В песочнице Portal файлы >~100 MB не влезают. Там:

```bash
SKIP_LARGE=1 (default smoke; SKIP_LARGE=0 full) python3 worker/scripts/download_path_a_weights.py --dest ./weights --dry-run
```

Полные веса качать на Vast / GPU-машине.

## One-liner для Vast

Только после явного «GPU да» + $cap + второго подтверждения у Романа:

```bash
mkdir -p /weights && python3 worker/scripts/download_path_a_weights.py --dest /weights
```

## Проверка размера и checksum

- Скрипт отказывается принимать пустые/слишком маленькие файлы.
- После скачивания пишет `weights.sha256.txt` (SHA256 записанный у нас; у upstream sidecar часто нет).
- Повторный запуск пропускает уже лежащие нормальные файлы (`--force` перекачает).

## Связь с Docker

Те же URL зашиты в `Dockerfile.gpu.patha` и `path_a_pipeline.py`.
Этот скрипт удобен, когда образ собран с `BUILD_SKIP_WEIGHTS=1` или веса тянут на уже живой машине.
