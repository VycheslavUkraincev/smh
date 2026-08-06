#!/usr/bin/env python3
"""SaveMyHistory — СТАДИЯ 2: генерация.
Берёт restorations.status='analyzed' (промпт уже готов от ИИ-глаз).

Провайдеры (GEN_PROVIDER):
  api    — demo/R&D: fal nano-banana + CodeFormer (НЕ коммерческий overnight default).
  gpu    — RunPod/Vast serverless; контракт image_url+prompt → image_base64.
  local  — прямой вызов на машине с весами (Vast box / offline dry-run).

Стек (GEN_STACK, default legacy — prod-safe до GPU smoke):
  legacy — CodeFormer path (R&D / demo; D4 ≠ commercial default).
  path_a — LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → [DDColor]
           via path_a_pipeline.restore_path_a (или RunPod handler при GEN_PROVIDER=gpu).

Product canon Path A (commercial overnight):
  LaMa → GFPGAN/RestoreFormer++ → Real-ESRGAN → DDColor.
  CodeFormer ≠ paid default until commercial license OK.
  Do NOT flip live GEN_PROVIDER / GEN_STACK=path_a from this file until Roman GPU smoke.

Кладёт результат в Spaces, status='generated'.
Запуск: python generate.py [batch]
"""
import sys, os, json, tempfile, subprocess, urllib.request
from common import log, claim, update_row, presigned_get, s3, BUCKET

FAL = os.environ.get("FAL_KEY", "")
# Переключатель провайдера: "api" (fal, старт) | "gpu" (RunPod) | "local" (прямой Path A)
GEN_PROVIDER = os.environ.get("GEN_PROVIDER", "api").lower()
# Стек на GPU/local: default LEGACY until Path A smoke green.
GEN_STACK = (os.environ.get("GEN_STACK") or "legacy").strip().lower()
RUNPOD_ENDPOINT = os.environ.get("RUNPOD_ENDPOINT", "")  # https://api.runpod.ai/v2/<id>/runsync
RUNPOD_KEY = os.environ.get("RUNPOD_KEY", "")

_PATH_A_ALIASES = ("path_a", "path-a", "patha", "a")
_LOCAL_PROVIDERS = ("local", "path_a", "patha")


def resolve_gen_stack(raw=None) -> str:
    """Normalize GEN_STACK → 'path_a' | 'legacy'. Default legacy (prod-safe)."""
    s = (raw if raw is not None else (os.environ.get("GEN_STACK") or GEN_STACK or "legacy"))
    s = str(s or "legacy").strip().lower()
    if s in _PATH_A_ALIASES:
        return "path_a"
    return "legacy"


def is_path_a_stack(raw=None) -> bool:
    return resolve_gen_stack(raw) == "path_a"


def resolve_path_a_mode(row_or_mode) -> str:
    """Map restorations.mode (restore|revive) → Path A mode (authentic|modern)."""
    if isinstance(row_or_mode, dict):
        m = row_or_mode.get("mode") or "restore"
    else:
        m = row_or_mode or "restore"
    m = str(m).strip().lower()
    if m in ("authentic", "modern"):
        return m
    if m in ("revive", "ready", "gotovy", "готовый", "colorize", "modernize"):
        return "modern"
    # restore / подлинный / default
    return "authentic"


def select_generate_backend(provider=None, stack=None) -> str:
    """Pure selection for tests + main.

    Returns one of: 'gpu' | 'path_a_local' | 'fal_legacy'.
    Default (api + legacy) → fal_legacy — live DO unchanged.
    """
    p = (provider if provider is not None else GEN_PROVIDER or "api").strip().lower()
    st = resolve_gen_stack(stack)
    if p == "gpu":
        return "gpu"
    if p in _LOCAL_PROVIDERS or (st == "path_a" and p != "api"):
        # Direct Path A on GPU box / explicit local. Never hijack default api.
        return "path_a_local"
    if st == "path_a" and p == "api":
        # Safety: GEN_STACK=path_a alone on DO api worker must NOT silently stub.
        # Require GEN_PROVIDER=local|gpu after smoke. Fall back to fal_legacy.
        return "fal_legacy"
    return "fal_legacy"


def path_a_run(src_path, out_path, *, mode="authentic", prompt="", fidelity=0.5):
    """Local/direct Path A via path_a_pipeline (LaMa→face→ESRGAN→[DDColor])."""
    from path_a_pipeline import restore_path_a

    res = restore_path_a(
        src_path,
        out_path,
        mode=mode,
        prompt=prompt,
        fidelity=fidelity,
        preserve_identity=True,
    )
    # path API returns PipelineResult; bytes API returns bytes
    if hasattr(res, "ok"):
        if not res.ok:
            raise RuntimeError(res.error or "path_a_failed")
        if not os.path.isfile(out_path):
            raise RuntimeError("path_a_failed: no output file")
    elif isinstance(res, (bytes, bytearray)):
        open(out_path, "wb").write(res)
    return out_path


def gpu_run(src_url, prompt, out_path, *, mode="authentic", gen_stack=None, fidelity=0.5):
    """GPU-режим: RunPod Serverless. Передаёт mode + gen_stack (handler ENV тоже читает)."""
    if not RUNPOD_ENDPOINT or not RUNPOD_KEY:
        raise RuntimeError("GPU режим включён, но RUNPOD_ENDPOINT/RUNPOD_KEY не заданы")
    stack = resolve_gen_stack(gen_stack)
    payload = json.dumps({
        "input": {
            "image_url": src_url,
            "prompt": prompt,
            "face_fidelity": float(fidelity),
            "preserve_identity": True,
            "mode": mode,
            "gen_stack": stack,
        }
    }).encode()
    req = urllib.request.Request(
        RUNPOD_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {RUNPOD_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
    out = (resp.get("output") or {})
    if out.get("image_url"):
        download(out["image_url"], out_path)
    elif out.get("image_base64"):
        import base64
        open(out_path, "wb").write(base64.b64decode(out["image_base64"]))
    else:
        raise RuntimeError(f"RunPod без результата: {json.dumps(resp)[:160]}")
    return out_path


def fal_run(model, args, out_path):
    """Вызов fal через готовый CLI скилла (тот же, что мы юзали в тестах)."""
    cmd = ["python3", "skills/fal-api/fal_api.py", "--model", model, "--output", out_path,
           "--args", json.dumps(args)]
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
    stack = resolve_gen_stack()
    backend = select_generate_backend()
    log("generate", f"взято {len(rows)} на генерацию (provider={GEN_PROVIDER} stack={stack} backend={backend})")
    ok = 0
    for r in rows:
        rid = r["id"]; uid = r["user_id"]
        with tempfile.TemporaryDirectory() as tmp:
            try:
                orig_url = presigned_get(r["original_key"], ttl=1800)
                src = download(orig_url, f"{tmp}/src.jpg")
                prompt = r.get("prompt") or "Restore this old family photo, preserve exact identity, no beautify."
                mode = resolve_path_a_mode(r)
                fidelity = 0.5 if stack == "path_a" else 0.85
                be = select_generate_backend()
                if be == "gpu":
                    # GPU: RunPod; handler dispatches by GEN_STACK (ENV or payload gen_stack)
                    final = gpu_run(
                        orig_url, prompt, f"{tmp}/final.jpg",
                        mode=mode, gen_stack=stack, fidelity=fidelity,
                    )
                elif be == "path_a_local":
                    # Direct Path A on GPU box / local (not DO api default)
                    final = path_a_run(
                        src, f"{tmp}/final.jpg",
                        mode=mode, prompt=prompt, fidelity=fidelity,
                    )
                else:
                    # API demo path (Nano/fal): NOT commercial overnight default.
                    # CodeFormer here = chat/R&D / interim API only (not paid default).
                    gen = fal_run("nano-banana-2-edit",
                                  {"image_url": src, "prompt": prompt}, f"{tmp}/gen.jpg")
                    final = fal_run("fal-ai/codeformer",
                                    {"image_url": gen, "fidelity": 0.85, "upscale_factor": 1},
                                    f"{tmp}/final.jpg")
                key = upload_result(final, uid, rid)
                update_row(rid, {"result_key": key, "status": "generated", "generated_at": "now()", "error": None})
                ok += 1
                log("generate", f"{rid[:8]} ✓ сгенерировано → {key} [{be}/{stack}/{mode}]")
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
