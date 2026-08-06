#!/usr/bin/env python3
"""SaveMyHistory — RunPod Serverless handler (GPU inference).
Запускается ВНУТРИ GPU-образа на RunPod. Контракт совпадает с worker/generate.py (GPU-режим):
  вход:  {"input": {"image_url": str, "prompt": str, "face_fidelity": float, "preserve_identity": bool}}
  выход: {"output": {"image_base64": str}}

GEN_STACK switch (ENV, default legacy until Path A smoke):
  legacy  — Real-ESRGAN + CodeFormer (R&D / demo; NOT commercial default — D4)
  path_a  — LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → DDColor
            (see path_a_pipeline.py; requires Dockerfile.gpu.patha image)

FACE_MODEL=gfpgan|restoreformer (Path A only; default gfpgan).
CodeFormer ≠ commercial overnight default.

NB: модели грузятся ОДИН раз при холодном старте (_load_models держится тёплым в контейнере),
    не на каждый запрос — иначе платим за загрузку весов каждый раз.
"""
import os, io, base64, tempfile, urllib.request

# Default LEGACY until Path A image + smoke. Do not flip live GEN_PROVIDER from here.
GEN_STACK = (os.environ.get("GEN_STACK") or "legacy").strip().lower()

_MODELS = {}

def _load_models():
    """Ленивая загрузка весов при первом запросе (warm в контейнере между запросами)."""
    if _MODELS:
        return _MODELS
    import torch
    from realesrgan import RealESRGANer
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from codeformer.codeformer_arch import CodeFormer  # из пакета codeformer-pip
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    weights = os.environ.get("WEIGHTS_DIR", "/weights")

    # --- Real-ESRGAN (фон) ---
    rrdb = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
    bg = RealESRGANer(scale=2, model_path=os.path.join(weights, "RealESRGAN_x2plus.pth"),
                      model=rrdb, half=(device == "cuda"), device=device)

    # --- CodeFormer (лица) — LEGACY only ---
    cf = CodeFormer(dim_embd=512, codebook_size=1024, n_head=8, n_layers=9,
                    connect_list=["32", "64", "128", "256"]).to(device)
    ckpt = torch.load(os.path.join(weights, "codeformer.pth"), map_location=device)["params_ema"]
    cf.load_state_dict(ckpt); cf.eval()

    helper = FaceRestoreHelper(upscale_factor=1, face_size=512, det_model="retinaface_resnet50",
                               use_parse=True, device=device)

    _MODELS.update({"device": device, "bg": bg, "cf": cf, "helper": helper, "torch": torch})
    return _MODELS

def _download(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()

def _restore_legacy(img_bytes, prompt, fidelity, preserve_identity):
    """LEGACY: фон (Real-ESRGAN) + лица (CodeFormer). Not commercial Path A."""
    import cv2, numpy as np
    m = _load_models()
    torch = m["torch"]; device = m["device"]

    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("decode_failed")

    # --- слой А: фон/повреждения ---
    try:
        bg_out, _ = m["bg"].enhance(img, outscale=2)
    except Exception:
        bg_out = img  # если апскейл упал — работаем по оригиналу

    # --- слой Б: лица CodeFormer по ОРИГИНАЛУ (честная личность) ---
    helper = m["helper"]; helper.clean_all()
    helper.read_image(img)
    helper.get_face_landmarks_5(only_center_face=False, resize=640, eye_dist_threshold=5)
    helper.align_warp_face()

    w = max(0.0, min(1.0, float(fidelity)))  # CodeFormer fidelity weight
    for cropped in helper.cropped_faces:
        from torchvision.transforms.functional import normalize
        from basicsr.utils import img2tensor, tensor2img
        t = img2tensor(cropped / 255.0, bgr2rgb=True, float32=True)
        normalize(t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
        t = t.unsqueeze(0).to(device)
        with torch.no_grad():
            out = m["cf"](t, w=w, adain=True)[0]
            restored = tensor2img(out, rgb2bgr=True, min_max=(-1, 1))
        helper.add_restored_face(restored.astype("uint8"))

    # склейка лиц обратно на улучшенный фон
    helper.get_inverse_affine(None)
    final = helper.paste_faces_to_input_image(upsample_img=bg_out)

    ok, enc = cv2.imencode(".jpg", final, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("encode_failed")
    return enc.tobytes()

def _restore_path_a(img_bytes, prompt, fidelity, preserve_identity, mode="authentic"):
    """Commercial Path A via path_a_pipeline (real stages; stub backends if no GPU libs)."""
    from path_a_pipeline import restore_path_a
    colorize = None
    cenv = (os.environ.get("COLORIZE") or "auto").strip().lower()
    if cenv in ("1", "true", "yes", "on"):
        colorize = True
    elif cenv in ("0", "false", "no", "off"):
        colorize = False
    # Bytes contract → JPEG bytes. GEN_STACK default stays legacy until smoke.
    return restore_path_a(
        img_bytes=img_bytes,
        mode=mode or "authentic",
        prompt=prompt,
        fidelity=fidelity,
        preserve_identity=preserve_identity,
        colorize=colorize,
    )

def resolve_stack(raw=None):
    """Normalize stack → path_a|legacy. Request gen_stack overrides ENV; default legacy."""
    s = (raw if raw is not None else (os.environ.get("GEN_STACK") or GEN_STACK or "legacy"))
    s = str(s or "legacy").strip().lower()
    if s in ("path_a", "path-a", "patha", "a"):
        return "path_a"
    return "legacy"

def _restore(img_bytes, prompt, fidelity, preserve_identity, mode="authentic", gen_stack=None):
    """Dispatch by GEN_STACK (default legacy). Optional per-request gen_stack from generate.py."""
    stack = resolve_stack(gen_stack)
    if stack == "path_a":
        return _restore_path_a(img_bytes, prompt, fidelity, preserve_identity, mode=mode)
    return _restore_legacy(img_bytes, prompt, fidelity, preserve_identity)

def handler(event):
    """Точка входа RunPod Serverless."""
    inp = (event or {}).get("input") or {}
    image_url = inp.get("image_url")
    if not image_url:
        return {"error": "no image_url"}
    prompt = inp.get("prompt") or "Restore old family photo, preserve exact identity, no beautify."
    # дефолт fidelity 0.5 = бережно (наш вывод: высокий fidelity идеализирует)
    fidelity = float(inp.get("face_fidelity", 0.5))
    preserve = bool(inp.get("preserve_identity", True))
    mode = (inp.get("mode") or "authentic").strip().lower()
    if mode not in ("authentic", "modern"):
        mode = "authentic"
    # Per-request override from generate.py; else ENV GEN_STACK (default legacy).
    req_stack = inp.get("gen_stack") or inp.get("stack")
    try:
        src = _download(image_url)
        out = _restore(src, prompt, fidelity, preserve, mode=mode, gen_stack=req_stack)
        return {"output": {"image_base64": base64.b64encode(out).decode()}}
    except Exception as e:
        return {"error": f"gpu_restore_failed: {str(e)[:200]}"}

# RunPod serverless bootstrap
if __name__ == "__main__":
    try:
        import runpod
        runpod.serverless.start({"handler": handler})
    except ImportError:
        import json, sys
        ev = {"input": {"image_url": sys.argv[1]}} if len(sys.argv) > 1 else {"input": {}}
        print(json.dumps(handler(ev))[:300])
