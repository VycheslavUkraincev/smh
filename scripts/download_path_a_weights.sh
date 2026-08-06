#!/usr/bin/env bash
# SaveMyHistory — скачать веса Path A на машине Vast/GPU.
#
# Канон: LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → DDColor (опц.)
# CodeFormer ≠ commercial default (не скачиваем).
#
# ЗАПУСКАТЬ ТОЛЬКО на GPU-машине (Vast/RunPod), НЕ в sandbox Portal
# (лимит ~100 MB; диск весов ~0.8–1.5 GB).
# Этот скрипт НЕ арендует GPU и НЕ трогает live GEN_PROVIDER.
#
# Примеры:
#   ./scripts/download_path_a_weights.sh --dry-run
#   WEIGHTS_DIR=/weights ./scripts/download_path_a_weights.sh          # smoke (SKIP_LARGE=1)
#   ENABLE_DDCOLOR=1 ./scripts/download_path_a_weights.sh              # + DDColor
#   FACE_MODEL=restoreformer ./scripts/download_path_a_weights.sh      # + RF++
#   ./scripts/download_path_a_weights.sh --full
#   ./scripts/download_path_a_weights.sh --with-rfpp
#
set -euo pipefail

WEIGHTS_DIR="${WEIGHTS_DIR:-/weights}"
DRY_RUN="${DRY_RUN:-0}"
# Default SKIP_LARGE=1 → smoke (core без DDColor). SKIP_LARGE=0 → full.
SKIP_LARGE="${SKIP_LARGE:-1}"
WITH_DDCOLOR="${WITH_DDCOLOR:-0}"
WITH_RFPP="${WITH_RFPP:-0}"
FORCE="${FORCE:-0}"
# Align with worker/path_a_pipeline.py env names.
FACE_MODEL="${FACE_MODEL:-gfpgan}"
ENABLE_DDCOLOR="${ENABLE_DDCOLOR:-0}"
# Fail-soft: optional/large URL miss → WARN continue; core fail → exit 1.
FAIL_SOFT="${FAIL_SOFT:-1}"
LARGE_THRESHOLD_BYTES="${LARGE_THRESHOLD_BYTES:-500000000}"

# Disk estimate (approx, verified Content-Length 2026-08-06):
#   core (LaMa+GFPGAN+ESRGAN) ≈ 592 MB
#   + RF++ ≈ 872 MB
#   + DDColor ≈ 1.46 GB
#   full + RF++ ≈ 1.74 GB  → ops band ~0.8–1.5 GB typical
DISK_CORE_MB=592
DISK_FULL_MB=1461

usage() {
  cat <<'USAGE'
download_path_a_weights.sh — веса Path A для Vast GPU

Диск (порядок): smoke/core ~0.6 GB; типичный Path A ~0.8–1.5 GB;
full + DDColor ~1.5 GB; +RF++ до ~1.8 GB. Не качать в sandbox Portal (~100 MB).

По умолчанию SKIP_LARGE=1 (smoke: LaMa + GFPGAN + Real-ESRGAN).
SKIP_LARGE=0 / --full / ENABLE_DDCOLOR=1 — ещё DDColor (~870 MB).

Флаги:
  --dry-run          только показать план, ничего не качать
  --skip-large       SKIP_LARGE=1 (default)
  --full             SKIP_LARGE=0 + DDColor
  --with-ddcolor     скачать DDColor (также снимает SKIP_LARGE)
  --with-rfpp        скачать RestoreFormer++ (.ckpt) дополнительно к GFPGAN
  --force            перекачать даже если файл уже есть и размер ок
  --weights-dir DIR  каталог весов (default: /weights или $WEIGHTS_DIR)
  -h, --help         эта справка

ENV:
  WEIGHTS_DIR DRY_RUN=1 SKIP_LARGE=0|1 WITH_DDCOLOR=1 WITH_RFPP=1 FORCE=1
  FACE_MODEL=gfpgan|restoreformer   (restoreformer ⇒ качает RF++)
  ENABLE_DDCOLOR=0|1                (1 ⇒ WITH_DDCOLOR + full)
  FAIL_SOFT=1                       (default: optional/large miss не валит exit;
                                     core miss всё равно exit 1)

Checksum: размер-файл ±2% от verified Content-Length (2026-08-06 HEAD 200).
Официальных sha256 sidecar у релизов нет; после скачивания:
  sha256sum "$WEIGHTS_DIR"/* > "$WEIGHTS_DIR/weights.sha256.txt"
USAGE
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

# ENABLE_DDCOLOR (pipeline env) → full DDColor fetch
case "${ENABLE_DDCOLOR,,}" in
  1|true|yes|on) WITH_DDCOLOR=1; SKIP_LARGE=0 ;;
esac

# FACE_MODEL=restoreformer|rfpp → also fetch RF++ weights
case "${FACE_MODEL,,}" in
  restoreformer|rfpp|restoreformer++|restoreformer_plusplus) WITH_RFPP=1 ;;
  gfpgan|"") ;;
  *) log_pre="WARN неизвестный FACE_MODEL=$FACE_MODEL — использую gfpgan" ;;
esac

# Если SKIP_LARGE=0 — full pack включает DDColor
if [ "$SKIP_LARGE" != "1" ]; then
  WITH_DDCOLOR=1
fi

# Каталог: ключ|файл|ожидаемый_размер_байт|URL|группа(core|optional|large)
# Размеры проверены HEAD Content-Length 2026-08-06 (HTTP 200).
# Синхрон с worker/path_a_pipeline.py WEIGHTS.
CATALOG=$(cat <<'CAT'
lama|big-lama.pt|205669692|https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt|core
gfpgan|GFPGANv1.4.pth|348632874|https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth|core
realesrgan|RealESRGAN_x2plus.pth|67061725|https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth|core
ddcolor|ddcolor_modelscope.pth|911950059|https://huggingface.co/piddnad/DDColor-models/resolve/main/ddcolor_modelscope.pth|large
rfpp|RestoreFormer++.ckpt|294028815|https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/v1.0.0/RestoreFormer%2B%2B.ckpt|optional
CAT
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

if [ -n "${log_pre:-}" ]; then
  log "$log_pre"
fi

bytes_human() {
  local b="$1"
  printf '%s (~%s MB)' "$b" "$((b / 1024 / 1024))"
}

verify_size() {
  local path="$1" expected="$2"
  local actual
  actual=$(wc -c < "$path" | tr -d ' ')
  local lo=$((expected * 98 / 100))
  local hi=$((expected * 102 / 100))
  if [ "$actual" -lt "$lo" ] || [ "$actual" -gt "$hi" ]; then
    echo "FAIL размер $path: got=$actual expected≈$expected" >&2
    return 1
  fi
  return 0
}

url_reachable() {
  local url="$1"
  # HEAD; fail-soft if remote missing / 404
  local code
  code=$(curl -sI -o /dev/null -w '%{http_code}' --retry 2 --retry-delay 1 -L "$url" 2>/dev/null || echo "000")
  case "$code" in
    200|301|302) return 0 ;;
    *) return 1 ;;
  esac
}

download_one() {
  local key="$1" file="$2" expected="$3" url="$4" group="$5"
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

  if ! url_reachable "$url"; then
    log "WARN URL недоступен ($key): $url — fail-soft skip"
    return 2
  fi

  log "Скачиваю $key → $dest ($(bytes_human "$expected"))"
  if ! curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
    rm -f "$tmp"
    log "WARN curl failed ($key) — fail-soft"
    return 2
  fi
  if ! verify_size "$tmp" "$expected"; then
    rm -f "$tmp"
    return 1
  fi
  mv -f "$tmp" "$dest"
  log "OK $file"
  return 0
}

should_fetch() {
  local group="$1"
  case "$group" in
    core) return 0 ;;
    large)
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
log "FACE_MODEL=$FACE_MODEL ENABLE_DDCOLOR=$ENABLE_DDCOLOR FAIL_SOFT=$FAIL_SOFT"
log "Диск оценка: core~${DISK_CORE_MB}MB full+DDColor~${DISK_FULL_MB}MB (ops ~0.8–1.5GB)"
log "Канон: LaMa + GFPGAN|RF++ + Real-ESRGAN; DDColor при ENABLE_DDCOLOR/SKIP_LARGE=0"
log "CodeFormer НЕ скачивается (legacy only)"

FAILED_CORE=0
FAILED_SOFT=0
PLANNED=0
SKIPPED=0

while IFS='|' read -r key file expected url group; do
  [ -n "$key" ] || continue
  if ! should_fetch "$group"; then
    log "SKIP $key ($file, group=$group) — smoke default; full: SKIP_LARGE=0 / ENABLE_DDCOLOR=1 / --with-rfpp / FACE_MODEL=restoreformer"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  PLANNED=$((PLANNED + 1))
  rc=0
  download_one "$key" "$file" "$expected" "$url" "$group" || rc=$?
  if [ "$rc" -ne 0 ]; then
    if [ "$group" = "core" ]; then
      FAILED_CORE=$((FAILED_CORE + 1))
    else
      FAILED_SOFT=$((FAILED_SOFT + 1))
      log "WARN soft-fail $key (group=$group) rc=$rc — продолжаю"
    fi
  fi
done <<< "$CATALOG"

log "Итого: planned=$PLANNED skipped=$SKIPPED failed_core=$FAILED_CORE failed_soft=$FAILED_SOFT"

if [ "$FAILED_CORE" -ne 0 ]; then
  log "FAIL: не скачались core веса"
  exit 1
fi

if [ "$FAILED_SOFT" -ne 0 ]; then
  if [ "$FAIL_SOFT" = "1" ]; then
    log "WARN: optional/large пропущены (FAIL_SOFT=1) — exit 0"
  else
    log "FAIL: optional/large ошибки и FAIL_SOFT=0"
    exit 1
  fi
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
log "Дальше: GEN_STACK=path_a FACE_MODEL=$FACE_MODEL ENABLE_DDCOLOR=$WITH_DDCOLOR WEIGHTS_DIR=$WEIGHTS_DIR"
