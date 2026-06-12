#!/usr/bin/env python3
"""SaveMyHistory — СТАДИЯ 2: генерация.
Берёт restorations.status='analyzed' (промпт уже готов от ИИ-глаз),
делает двухслойную реставрацию:
  (А) генератив по готовому промпту (фон/цвет/повреждения) — nano-banana через fal
  (Б) CodeFormer по ОРИГИНАЛУ (честные лица)
  склейка: лица из (Б) поверх (А)  [на старте — упрощённо: CodeFormer поверх результата
           с высоким fidelity; полноценная маска-склейка — на GPU-этапе]
Кладёт результат в Spaces, status='generated'.
API-режим (fal). GPU-режим заменит этот шаг локальным inference (тот же контракт статусов).
Запуск: python worker_generate.py [batch]
"""
import sys, os, json, tempfile, subprocess, urllib.request
from worker_common import log, claim, update_row, presigned_get, s3, BUCKET

FAL = os.environ.get("FAL_KEY", "")

def fal_run(model, image=None, prompt=None, extra=None, out_path=None):
    """Вызов fal через CLI скилла с КАНОНИЧНЫМИ флагами (--image/--prompt),
    чтобы сработал contract-rewriting (image→image_urls) и авто-заливка локальных файлов на fal CDN.
    extra — доп.поля (fidelity, upscale_factor) через --args (overlay)."""
    cmd = ["python3", "skills/fal-api/fal_api.py", "--model", model, "--output", out_path]
    if image is not None:
        cmd += ["--image", image]
    if prompt is not None:
        cmd += ["--prompt", prompt]
    if extra:
        cmd += ["--args", json.dumps(extra)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"fal {model} failed: {r.stderr[:160]}")
    return out_path

def download(url, path):
    urllib.request.urlretrieve(url, path)
    return path

def upload_result(local, user_id, rid):
    key = f"results/{user_id}/{rid}.jpg"
    s3().upload_file(local, BUCKET, key, ExtraArgs={"ContentType": "image/jpeg"})
    return key

def main(batch=4):
    # маленький батч: генерация тяжёлая
    rows = claim("analyzed", "processing", batch)
    if not rows:
        log("generate", "нет фото на генерацию (analyzed)"); return 0
    log("generate", f"взято {len(rows)} на генерацию")
    ok = 0
    for r in rows:
        rid = r["id"]; uid = r["user_id"]
        with tempfile.TemporaryDirectory() as tmp:
            try:
                orig_url = presigned_get(r["original_key"], ttl=1800)
                src = download(orig_url, f"{tmp}/src.jpg")
                prompt = r.get("prompt") or "Restore this old family photo, preserve exact identity, no beautify."
                # (А) ГЛАВНАЯ ГЕНЕРАЦИЯ — чистый nano-banana по бережному промпту.
                # Эксперимент (2026-06-12) доказал: чистый nano-banana стабильно проходит verify
                # (лица честные), а CodeFormer ПОВЕРХ нестабильно ИДЕАЛИЗИРУЕТ лица.
                final = fal_run("nano-banana-2-edit", image=src, prompt=prompt,
                                out_path=f"{tmp}/gen.jpg")
                # (Б) CodeFormer ТОЛЬКО для очень размытых лиц (very_blurry) и НИЗКИМ fidelity=0.3,
                # где восстановление черт реально нужно. Иначе — не трогаем лица.
                analysis = r.get("analysis") or {}
                if isinstance(analysis, str):
                    try: analysis = json.loads(analysis)
                    except Exception: analysis = {}
                if analysis.get("face_clarity") == "very_blurry":
                    try:
                        final = fal_run("fal-ai/codeformer",
                                        extra={"image_url": final, "fidelity": 0.3, "upscale_factor": 1},
                                        out_path=f"{tmp}/final.jpg")
                        log("generate", f"{rid[:8]} CodeFormer применён (very_blurry, fid=0.3)")
                    except Exception as ce:
                        log("generate", f"{rid[:8]} CodeFormer skip ({str(ce)[:60]}) — идём с чистым nano-banana")
                key = upload_result(final, uid, rid)
                update_row(rid, {"result_key": key, "status": "generated", "generated_at": "now()", "error": None})
                ok += 1
                log("generate", f"{rid[:8]} ✓ сгенерировано → {key}")
            except Exception as e:
                attempts = (r.get("attempts") or 0) + 1
                st = "failed" if attempts >= 3 else "analyzed"
                update_row(rid, {"status": st, "attempts": attempts, "error": f"gen_err: {str(e)[:120]}"})
                log("generate", f"{rid[:8]} ОШИБКА (#{attempts}) → {st}: {str(e)[:80]}")
    log("generate", f"готово: {ok}/{len(rows)}")
    return ok

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    main(n)
