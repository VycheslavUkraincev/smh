#!/usr/bin/env bash
# deploy_gpu.sh — переключает SMH из intake-only в GPU-режим
set -e

ENDPOINT_ID="$1"
if [ -z "$ENDPOINT_ID" ]; then
  echo "Usage: bash scripts/deploy_gpu.sh <runpod_endpoint_id>"
  exit 1
fi

APP_ID="f622ac8f-d9b7-448e-8953-12f948064a68"
TOKEN=$(cat ../../.secrets/do_token.txt)
RP_KEY=$(cat ../../.secrets/runpod_key.txt)

echo "→ Переключаю SMH на GPU-режим..."

cat > /tmp/do_app_spec.json << JSONEOF
{
  "spec": {
    "workers": [
      {
        "name": "smh-worker",
        "git": {"branch": "main"},
        "envs": [
          {"key": "INTAKE_ONLY", "value": "0"},
          {"key": "GEN_PROVIDER", "value": "gpu"},
          {"key": "GEN_MIN_BATCH", "value": "1"},
          {"key": "RUNPOD_ENDPOINT", "value": "https://api.runpod.io/v2/${ENDPOINT_ID}/runsync"},
          {"key": "RUNPOD_KEY", "value": "${RP_KEY}", "type": "SECRET"}
        ]
      }
    ]
  }
}
JSONEOF

echo "→ Spec подготовлен. Для применения:"
echo "   Использовать DO API или dashboard: App Platform → smh-worker → env vars"
echo "   Поменять: INTAKE_ONLY=0, GEN_PROVIDER=gpu, RUNPOD_ENDPOINT, RUNPOD_KEY"
echo ""
echo "RunPod Endpoint ID: $ENDPOINT_ID"
