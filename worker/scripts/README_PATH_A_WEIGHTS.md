# Скачивание весов Path A

Простой скрипт: кладёт официальные веса моделей Path A в папку (обычно `/weights`).
GPU не арендует. Секреты не нужны.

Канон: LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → DDColor (опц.).
CodeFormer сюда не входит (legacy, не коммерческий default).

## Что качает (ядро / smoke)

По умолчанию `SKIP_LARGE=1`:

1. LaMa (`big-lama.pt`) — ~200 MB
2. GFPGAN v1.4 — ~340 MB
3. Real-ESRGAN x2plus — ~64 MB

Опционально:
- `--with-ddcolor` / `ENABLE_DDCOLOR=1` / `SKIP_LARGE=0` — цвет (~870 MB)
- `--with-rfpp` / `FACE_MODEL=restoreformer` — RestoreFormer++ рядом с GFPGAN

Диск: smoke ~0.6 GB; типичный Path A ~0.8–1.5 GB; full+RF++ до ~1.8 GB.

## Как запустить

Из корня репо **на GPU-машине** (после аренды; не в sandbox Portal ~100 MB):

```bash
# bash (предпочт. на Vast)
WEIGHTS_DIR=/weights ./scripts/download_path_a_weights.sh
# план без скачивания:
./scripts/download_path_a_weights.sh --dry-run

# python helper
python3 worker/scripts/download_path_a_weights.py --dest /weights
python3 worker/scripts/download_path_a_weights.py --dry-run --list
```

В песочнице Portal только dry-run / smoke-план — полный пакет не влезет.

## ENV (как у pipeline)

| ENV | Default | Смысл |
|-----|---------|--------|
| `WEIGHTS_DIR` | `/weights` | куда класть файлы |
| `SKIP_LARGE` | `1` | `1`=smoke без DDColor; `0`=full |
| `ENABLE_DDCOLOR` | `0` | `1` → скачать DDColor (снимает SKIP_LARGE) |
| `FACE_MODEL` | `gfpgan` | `restoreformer` → ещё RF++ |
| `FAIL_SOFT` | `1` | optional/large miss → WARN + exit 0; core miss → exit 1 |
| `DRY_RUN` / `--dry-run` | off | только план |
| `FORCE` / `--force` | off | перекачать даже если размер ок |

Примеры:

```bash
ENABLE_DDCOLOR=1 ./scripts/download_path_a_weights.sh
FACE_MODEL=restoreformer ./scripts/download_path_a_weights.sh
FAIL_SOFT=0 ./scripts/download_path_a_weights.sh --full   # строгий exit на soft-fail
```

## One-liner для Vast

Только после явного «GPU да» + $cap + второго подтверждения у Романа:

```bash
mkdir -p /weights && WEIGHTS_DIR=/weights ./scripts/download_path_a_weights.sh
```

## Проверка размера и checksum

- Скрипт проверяет размер ±2% от verified Content-Length (2026-08-06).
- После скачивания пишет `weights.sha256.txt` (у upstream sidecar часто нет).
- Повторный запуск пропускает уже лежащие нормальные файлы (`--force` перекачает).

## Связь с Docker / ночью

Те же URL в `Dockerfile.gpu.patha` и `path_a_pipeline.py`.
Ops-чеклист: `obsidian/09_СЕРВИС/12_PATH_A_НОЧЬ.md` (rent → weights на GPU → warm → batch → DESTROY).
