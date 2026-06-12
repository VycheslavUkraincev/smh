#!/usr/bin/env python3
"""SaveMyHistory — RunPod Serverless handler (GPU inference).
Это код, который запускается ВНУТРИ GPU-образа на RunPod.
Контракт совпадает с тем, что ждёт worker/generate.py в GPU-режиме:
  вход:  {"input": {"image_url": str, "prompt": str, "face_fidelity": float, "preserve_identity": bool}}
  выход: {"output": {"image_url": str}}  ИЛИ  {"output": {"image_base64": str}}

Двухслойная реставрация на своём железе (дёшево, 11× выгоднее API при загрузке):
  (А) генератив фон/цвет/повреждения по промпту (SUPIR / SD-img2img)
  (Б) CodeFormer по ОРИГИНАЛУ → честные лица → склейка маской поверх (А)

NB: тяжёлые модели (supir, codeformer) грузятся ОДИН раз при холодном старте
    в COLD-блоке ниже, не на каждый запрос — иначе платим за загрузку весов.
"""
import os, io, base64, tempfile, urllib.request

# ---------- COLD START: модели грузим один раз ----------
_MODELS = {}

def _load_models():
    """Ленивая загрузка тяжёлых весов при первом запросе (held warm в контейнере)."""
    if _MODELS:
        return _MODELS
    # TODO(GPU): подключить реальные пайплайны, когда соберём образ:
    #   from codeformer_pipeline import CodeFormer
    #   from supir_pipeline import SUPIR
    #   _MODELS["codeformer"] = CodeFormer(device="cuda")
    #   _MODELS["supir"] = SUPIR(device="cuda")
    _MODELS["ready"] = True
    return _MODELS

def _download(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()

def _restore(img_bytes, prompt, fidelity, preserve_identity):
    """СЕРДЦЕ GPU-реставрации. Сейчас — заглушка-проброс, чтобы контракт был рабочим
    до сборки образа. Когда модели подключены — здесь два слоя + склейка."""
    m = _load_models()
    # --- слой А: генератив (SUPIR/img2img) по prompt ---  [GPU TODO]
    gen = img_bytes
    # --- слой Б: CodeFormer по оригиналу, честные лица --- [GPU TODO]
    #     faces = m["codeformer"].restore(img_bytes, fidelity=fidelity)
    #     final = blend_faces(gen, faces, mask)
    final = gen
    return final

def handler(event):
    """Точка входа RunPod Serverless."""
    inp = (event or {}).get("input") or {}
    image_url = inp.get("image_url")
    if not image_url:
        return {"error": "no image_url"}
    prompt = inp.get("prompt") or "Restore this old family photo, preserve exact identity, no beautify."
    fidelity = float(inp.get("face_fidelity", 0.85))
    preserve = bool(inp.get("preserve_identity", True))
    try:
        src = _download(image_url)
        out = _restore(src, prompt, fidelity, preserve)
        return {"output": {"image_base64": base64.b64encode(out).decode()}}
    except Exception as e:
        return {"error": f"gpu_restore_failed: {str(e)[:200]}"}

# RunPod serverless bootstrap
if __name__ == "__main__":
    try:
        import runpod
        runpod.serverless.start({"handler": handler})
    except ImportError:
        # локальный smoke-тест без runpod
        import json, sys
        ev = {"input": {"image_url": sys.argv[1]}} if len(sys.argv) > 1 else {"input": {}}
        print(json.dumps(handler(ev))[:200])
