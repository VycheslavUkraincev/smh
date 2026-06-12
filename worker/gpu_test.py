#!/usr/bin/env python3
"""SaveMyHistory — тест GPU endpoint (RunPod) на одном фото.
Запуск (когда есть endpoint+ключ):
  RUNPOD_ENDPOINT=https://api.runpod.ai/v2/<id>/runsync \
  RUNPOD_KEY=rpa_xxx \
  python gpu_test.py <image_url> [fidelity]

Что делает:
  - шлёт фото в GPU-хендлер (контракт worker/generate.py)
  - принимает image_base64, сохраняет gpu_result.jpg
  - печатает время ответа (важно для cold-start замера)
"""
import os, sys, json, time, base64, urllib.request

ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "")
KEY = os.environ.get("RUNPOD_KEY", "")

def main():
    if not ENDPOINT or not KEY:
        print("ERR: задай RUNPOD_ENDPOINT и RUNPOD_KEY в окружении"); return 1
    if len(sys.argv) < 2:
        print("usage: python gpu_test.py <image_url> [fidelity]"); return 1
    image_url = sys.argv[1]
    fidelity = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

    payload = json.dumps({"input": {
        "image_url": image_url,
        "prompt": "Restore old family photo, preserve exact identity, no beautify.",
        "face_fidelity": fidelity,
        "preserve_identity": True,
    }}).encode()

    req = urllib.request.Request(ENDPOINT, data=payload, method="POST",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})

    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        body = json.loads(resp.read().decode())
    except Exception as e:
        print(f"ERR запрос упал: {str(e)[:200]}"); return 1
    dt = time.time() - t0

    out = (body or {}).get("output") or {}
    if "image_base64" in out:
        data = base64.b64decode(out["image_base64"])
        with open("gpu_result.jpg", "wb") as f:
            f.write(data)
        print(f"OK за {dt:.1f}с | результат: gpu_result.jpg ({len(data)} байт) | fidelity={fidelity}")
        print("→ открой gpu_result.jpg и проверь глазами: лицо осталось собой? фон чистый? нет идеализации?")
    elif "image_url" in out:
        print(f"OK за {dt:.1f}с | результат-URL: {out['image_url']}")
    else:
        print(f"НЕТ результата за {dt:.1f}с | ответ: {json.dumps(body)[:300]}")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
