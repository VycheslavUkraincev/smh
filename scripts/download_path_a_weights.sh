#!/usr/bin/env bash
# SaveMyHistory — скачать веса Path A на машине Vast/GPU.
#
# Канон: LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → DDColor (опц.)
# CodeFormer ≠ commercial default (не скачиваем).
#
# ЗАПУСКАТЬ ТОЛЬКО на GPU-машине (Vast/RunPod), НЕ в sandbox Portal
# (лимит ~100 MB; полный пакет ~0.6–1.8 GB).
# Этот скрипт НЕ арендует GPU и НЕ трогает live GEN_PROVIDER.
#
# Примеры:
#   ./scripts/download_path_a_weights.sh --dry-run
#   WEIGHTS_DIR=/weights ./scripts/download_path_a_weights.sh          # smoke (SKIP_LARGE=1)
#   SKIP_LARGE=0 ./scripts/download_path_a_weights.sh                  # full + DDColor
#   ./scripts/download_path_a_weights.sh --full
#   ./scripts/download_path_a_weights.sh --with-rfpp                   # + RestoreFormer++
#
set -euo pipefail

WEIGHTS_DIR="${WEIGHTS_DIR:-/weights}"
DRY_RUN="${DRY_RUN:-0}"
# Default SKIP_LARGE=1 → tiny/smoke (core без DDColor). SKIP_LARGE=0 → full.
SKIP_LARGE="${SKIP_LARGE:-1}"
WITH_DDCOLOR="${WITH_DDCOLOR:-0}"
WITH_RFPP="${WITH_RFPP:-0}"
FORCE="${FORCE:-0}"
# Файлы больше порога (байты) считаются LARGE; DDColor ~870 MB.
LARGE_THRESHOLD_BYTES="${LARGE_THRESHOLD_BYTES:-500000000}"

usage() {
  cat <<'EOF'
download_path_a_weights.sh — веса Path A для Vast GPU

По умолчанию SKIP_LARGE=1 (smoke: LaMa + GFPGAN + Real-ESRGAN).
SKIP_LARGE=0 или --full — ещё DDColor (~870 MB).

Флаги:
  --dry-run          только показать план, ничего не качать
  --skip-large       SKIP_LARGE=1 (default)
  --full             SKIP_LARGE=0 + DDColor
  --with-ddcolor     скачать DDColor (также снимает SKIP_LARGE)
  --with-rfpp        скачать RestoreFormer++ (.ckpt) дополнительно к GFPGAN
  --force            перекачать даже если файл уже есть и размер ок
  --weights-dir DIR  каталог весов (default: /weights или $WEIGHTS_DIR)
  -h, --help         эта справка

ENV: WEIGHTS_DIR DRY_RUN=1 SKIP_LARGE=0|1 WITH_DDCOLOR=1 WITH_RFPP=1 FORCE=1

Checksum: размер-файл ±2% от verified Content-Length (2026-08-06 HEAD 200).
Официальных sha256 sidecar у релизов нет; после скачивания можно:
  sha256sum "$WEIGHTS_DIR"/* > "$WEIGHTS_DIR/weights.sha256.txt"
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-large) SKIP_LARGE=1; shift ;;
    --full) SKIP_LARGE=0; WITH_DDCOLOR=1; shift ;;
    --with-ddcolor) WITH_DDCOLOR=1; SKIP_LARGE=0; shift ;;
    --with-rfpp) WITH_RFPP=1; shift ;;
    --force) FORCE=1; shift ;;
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Неизвестный аргумент: $1" >&2; usage; exit 2 ;;
  esac
done

# Если SKIP_LARGE=0 — full pack включает DDColor
if [ "$SKIP_LARGE" != "1" ]; then
  WITH_DDCOLOR=1
fi

# Каталог: ключ|файл|ожидаемый_размер_байт|URL|группа(core|optional|large)
# Размеры проверены HEAD Content-Length 2026-08-06 (HTTP 200).
# Синхрон с worker/path_a_pipeline.py WEIGHTS.
CATALOG=$(cat <<'EOF'
lama|big-lama.pt|205669692|https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt|core
gfpgan|GFPGANv1.4.pth|348632874|https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth|core
realesrgan|RealESRGAN_x2plus.pth|67061725|https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth|core
ddcolor|ddcolor_modelscope.pth|911950059|https://huggingface.co/piddnad/DDColor-models/resolve/main/ddcolor_modelscope.pth|large
rfpp|RestoreFormer++.ckpt|294028815|https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/v1.0.0/RestoreFormer%2B%2B.ckpt|optional
EOF
)

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Нужна команда: $1" >&2
    exit 1
  }
}

if [ "$DRY_RUN" != "1" ]; then
  need_cmd curl
  need_cmd mkdir
fi

log() { printf '[patha-weights] %s\n' "$*"; }

bytes_human() {
  local b="$1"
  printf '%s (~%s MB)' "$b" "$((b / 1024 / 1024))"
}

verify_size() {
  local path="$1" expected="$2"
  local actual
  actual=$(wc -c < "$path" | tr -d ' ')
  # Допуск ±2% на редкие расхождения CDN
  local lo=$((expected * 98 / 100))
  local hi=$((expected * 102 / 100))
  if [ "$actual" -lt "$lo" ] || [ "$actual" -gt "$hi" ]; then
    echo "FAIL размер $path: got=$actual expected≈$expected" >&2
    return 1
  fi
  return 0
}

download_one() {
  local key="$1" file="$2" expected="$3" url="$4"
  local dest="$WEIGHTS_DIR/$file"
  local tmp="${dest}.part"

  if [ -f "$dest" ] && [ "$FORCE" != "1" ]; then
    if verify_size "$dest" "$expected"; then
      log "OK уже есть: $file ($(bytes_human "$expected"))"
      return 0
    fi
    log "WARN размер не совпал, перекачиваю: $file"
  fi

  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN скачал бы: $file ← $url ($(bytes_human "$expected"))"
    return 0
  fi

  log "Скачиваю $key → $dest ($(bytes_human "$expected"))"
  curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"
  if ! verify_size "$tmp" "$expected"; then
    rm -f "$tmp"
    return 1
  fi
  mv -f "$tmp" "$dest"
  log "OK $file"
}

should_fetch() {
  local group="$1"
  case "$group" in
    core) return 0 ;;
    large)
      # DDColor: только full / --with-ddcolor / SKIP_LARGE=0
      if [ "$WITH_DDCOLOR" = "1" ] && [ "$SKIP_LARGE" != "1" ]; then
        return 0
      fi
      return 1
      ;;
    optional)
      [ "$WITH_RFPP" = "1" ]
      ;;
    *) return 1 ;;
  esac
}

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: каталог $WEIGHTS_DIR не создаём"
else
  mkdir -p "$WEIGHTS_DIR" "$WEIGHTS_DIR/optional"
fi

log "WEIGHTS_DIR=$WEIGHTS_DIR dry_run=$DRY_RUN skip_large=$SKIP_LARGE with_ddcolor=$WITH_DDCOLOR with_rfpp=$WITH_RFPP"
log "Канон: LaMa + GFPGAN + Real-ESRGAN; DDColor при SKIP_LARGE=0; RF++ по --with-rfpp"
log "CodeFormer НЕ скачивается (legacy only)"

FAILED=0
PLANNED=0
SKIPPED=0

while IFS='|' read -r key file expected url group; do
  [ -n "$key" ] || continue
  if ! should_fetch "$group"; then
    log "SKIP $key ($file, group=$group) — smoke default; full: SKIP_LARGE=0 или --full / --with-rfpp"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  PLANNED=$((PLANNED + 1))
  if ! download_one "$key" "$file" "$expected" "$url"; then
    FAILED=$((FAILED + 1))
  fi
done <<< "$CATALOG"

log "Итого: planned=$PLANNED skipped=$SKIPPED failed=$FAILED"
if [ "$FAILED" -ne 0 ]; then
  exit 1
fi

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN завершён (ничего не скачано)"
  exit 0
fi

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$WEIGHTS_DIR" && sha256sum big-lama.pt GFPGANv1.4.pth RealESRGAN_x2plus.pth ddcolor_modelscope.pth "RestoreFormer++.ckpt" 2>/dev/null) \
    > "$WEIGHTS_DIR/weights.sha256.txt" || true
  log "checksums → $WEIGHTS_DIR/weights.sha256.txt"
fi

log "Готово. Проверка:"
ls -lh "$WEIGHTS_DIR"/* 2>/dev/null || true
log "Дальше: GEN_STACK=path_a FACE_MODEL=gfpgan WEIGHTS_DIR=$WEIGHTS_DIR"
