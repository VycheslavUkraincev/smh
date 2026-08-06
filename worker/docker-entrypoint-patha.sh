#!/usr/bin/env bash
# Path A image entrypoint (draft).
# If DOWNLOAD_WEIGHTS_AT=entrypoint, fetch missing core weights before handler start.
# Safe prep — does not rent GPU. Used only by Dockerfile.gpu.patha.
set -euo pipefail
WEIGHTS_DIR="${WEIGHTS_DIR:-/weights}"
mkdir -p "$WEIGHTS_DIR"

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
fi

export GEN_STACK="${GEN_STACK:-path_a}"
export FACE_MODEL="${FACE_MODEL:-gfpgan}"
exec python -u /app/runpod_handler.py
