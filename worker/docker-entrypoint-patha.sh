#!/usr/bin/env bash
# Path A image entrypoint.
# If DOWNLOAD_WEIGHTS_AT=entrypoint, fetch missing core weights before handler start.
# Safe prep — does not rent GPU. Used only by Dockerfile.gpu.patha.
set -euo pipefail
WEIGHTS_DIR="${WEIGHTS_DIR:-/weights}"
mkdir -p "$WEIGHTS_DIR" "$WEIGHTS_DIR/optional"

if [ "${DOWNLOAD_WEIGHTS_AT:-build}" = "entrypoint" ]; then
  echo "[patha] entrypoint weight check in $WEIGHTS_DIR"
  if [ ! -f "$WEIGHTS_DIR/big-lama.pt" ]; then
    wget -q -O "$WEIGHTS_DIR/big-lama.pt" \
      https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt
  fi
  if [ ! -f "$WEIGHTS_DIR/GFPGANv1.4.pth" ]; then
    wget -q -O "$WEIGHTS_DIR/GFPGANv1.4.pth" \
      https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth
  fi
  if [ ! -f "$WEIGHTS_DIR/RealESRGAN_x2plus.pth" ]; then
    wget -q -O "$WEIGHTS_DIR/RealESRGAN_x2plus.pth" \
      https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
  fi
  # Optional: DDColor / RF++ if ENABLE_DDCOLOR=1 or FACE_MODEL=restoreformer
  if [ "${ENABLE_DDCOLOR:-0}" = "1" ] && [ ! -f "$WEIGHTS_DIR/ddcolor_modelscope.pth" ]; then
    wget -q -O "$WEIGHTS_DIR/ddcolor_modelscope.pth" \
      https://huggingface.co/piddnad/DDColor-models/resolve/main/ddcolor_modelscope.pth || true
  fi
  if [ "${FACE_MODEL:-gfpgan}" = "restoreformer" ] && [ ! -f "$WEIGHTS_DIR/RestoreFormer++.ckpt" ]; then
    wget -q -O "$WEIGHTS_DIR/RestoreFormer++.ckpt" \
      "https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/v1.0.0/RestoreFormer%2B%2B.ckpt" || true
  fi
fi

export GEN_STACK="${GEN_STACK:-path_a}"
export FACE_MODEL="${FACE_MODEL:-gfpgan}"
exec python -u /app/runpod_handler.py
