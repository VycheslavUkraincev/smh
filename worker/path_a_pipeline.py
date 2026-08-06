#!/usr/bin/env python3
"""SaveMyHistory — Path A commercial pipeline (D4).

Canon order (ONE photo):
  1) LaMa              — inpaint scratches / holes / stains
  2) GFPGAN | RF++     — face restore (gentle, no beautify)
  3) Real-ESRGAN       — upscale / background
  4) DDColor           — only if color / faded color needed

CodeFormer ≠ commercial default. Keep it on GEN_STACK=legacy only.

This module implements real stage interfaces with ImportError-safe backends.
When torch/weights/libs are missing (sandbox / CI), stages fall back to
honest offline stubs (identity copy + stage log) so orchestration is
unit-testable. On a Path A GPU image with weights, real libs are called.

ENV
----
  WEIGHTS_DIR          default /weights
  FACE_MODEL           gfpgan | restoreformer   (default: gfpgan)
  ENABLE_DDCOLOR       0|1                      (default: 0)
  PATH_A_STRICT        0|1  if 1, missing weights/libs → hard error
  PATH_A_ALLOW_STUB    0|1  if 1, force stub backends even if libs present
  PATH_A_DRYRUN        0|1  alias of PATH_A_ALLOW_STUB (offline I/O dry-run)
  PATH_A_DOWNLOAD      0|1  if 1, download_missing_weights() may fetch

Disk budget (weights only, GFPGAN path): ~0.8–1.5 GB
Full CUDA image (torch+CUDA): typically 8–15 GB — not counted below.

Do NOT rent GPU from this module. Do NOT flip live GEN_PROVIDER.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Stages (product canon)
# ---------------------------------------------------------------------------
STAGES: List[str] = [
    "lama",            # inpaint
    "face",            # GFPGAN or RestoreFormer++
    "realesrgan",      # upscale / bg
    "ddcolor",         # optional color
]

# ---------------------------------------------------------------------------
# Weight catalog — URLs are public release assets (verify at build time).
# Sizes are order-of-magnitude; expect ~0.8–1.5 GB for GFPGAN path.
# ---------------------------------------------------------------------------
WEIGHTS: Dict[str, Dict[str, object]] = {
    "lama": {
        "file": "big-lama.pt",
        "approx_mb": 200,
        "url": (
            "https://github.com/Sanster/models/releases/download/"
            "add_big_lama/big-lama.pt"
        ),
        "required": True,
    },
    "gfpgan": {
        "file": "GFPGANv1.4.pth",
        "approx_mb": 340,
        "url": (
            "https://github.com/TencentARC/GFPGAN/releases/download/"
            "v1.3.4/GFPGANv1.4.pth"
        ),
        "required": True,  # when FACE_MODEL=gfpgan
    },
    "restoreformer": {
        # Official RF++ release asset is .ckpt (verified 200 @ v1.0.0).
        "file": "RestoreFormer++.ckpt",
        "approx_mb": 280,
        "url": (
            "https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/"
            "v1.0.0/RestoreFormer%2B%2B.ckpt"
        ),
        # GFPGAN-compatible RestoreFormer (arch='RestoreFormer') fallback:
        "alt_file": "RestoreFormer.pth",
        "alt_url": (
            "https://github.com/TencentARC/GFPGAN/releases/download/"
            "v1.3.4/RestoreFormer.pth"
        ),
        "required": False,  # when FACE_MODEL=restoreformer
        "note": (
            "Prefer RestoreFormer++.ckpt; if only RestoreFormer.pth present, "
            "stage uses GFPGANer(arch='RestoreFormer') fallback."
        ),
    },
    "realesrgan": {
        "file": "RealESRGAN_x2plus.pth",
        "approx_mb": 64,
        "url": (
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.2.1/RealESRGAN_x2plus.pth"
        ),
        "required": True,
    },
    "ddcolor": {
        "file": "ddcolor_modelscope.pth",
        "approx_mb": 350,
        "url": (
            "https://huggingface.co/piddnad/DDColor-models/resolve/main/"
            "ddcolor_modelscope.pth"
        ),
        "required": False,
        "note": "Only if ENABLE_DDCOLOR=1; confirm URL before build.",
    },
}

# Alias for Docker comments / docs
WEIGHT_URLS = {k: str(v["url"]) for k, v in WEIGHTS.items()}


# ---------------------------------------------------------------------------
# ENV helpers
# ---------------------------------------------------------------------------
def face_model() -> str:
    """FACE_MODEL=gfpgan|restoreformer (default gfpgan)."""
    v = (os.environ.get("FACE_MODEL") or "gfpgan").strip().lower()
    if v in ("rfpp", "restoreformer++", "restoreformer_plusplus"):
        return "restoreformer"
    if v not in ("gfpgan", "restoreformer"):
        return "gfpgan"
    return v


def enable_ddcolor() -> bool:
    return (os.environ.get("ENABLE_DDCOLOR") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def weights_dir() -> str:
    return os.environ.get("WEIGHTS_DIR") or "/weights"


def path_a_strict() -> bool:
    return (os.environ.get("PATH_A_STRICT") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def path_a_allow_stub() -> bool:
    """Offline dry-run / forced stub backends.

    True when PATH_A_ALLOW_STUB=1 OR PATH_A_DRYRUN=1.
    PATH_A_DRYRUN is the product alias: validates file I/O + stage order
    without claiming restoration quality.
    """
    for key in ("PATH_A_ALLOW_STUB", "PATH_A_DRYRUN"):
        if (os.environ.get(key) or "0").strip().lower() in ("1", "true", "yes", "on"):
            return True
    return False



def path_a_download_enabled() -> bool:
    return (os.environ.get("PATH_A_DOWNLOAD") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def expected_weight_files() -> List[Tuple[str, Dict[str, object]]]:
    """Return (key, meta) for weights needed under current ENV."""
    out: List[Tuple[str, Dict[str, object]]] = []
    out.append(("lama", WEIGHTS["lama"]))
    fm = face_model()
    out.append((fm, WEIGHTS[fm]))
    out.append(("realesrgan", WEIGHTS["realesrgan"]))
    if enable_ddcolor():
        out.append(("ddcolor", WEIGHTS["ddcolor"]))
    return out


def estimate_disk_mb() -> Tuple[int, int]:
    """Rough (low, high) MB for selected Path A weights."""
    total = 0
    for _, meta in expected_weight_files():
        total += int(meta.get("approx_mb") or 0)
    low = max(800, int(total * 0.85))
    high = max(low, int(total * 1.15))
    return low, high


def weight_path(key: str) -> str:
    meta = WEIGHTS[key]
    return os.path.join(weights_dir(), str(meta["file"]))


def missing_weights() -> List[str]:
    """Keys whose weight files are absent on disk."""
    miss: List[str] = []
    for key, meta in expected_weight_files():
        p = os.path.join(weights_dir(), str(meta["file"]))
        # also check optional/ subdir (Docker draft layout)
        p_opt = os.path.join(weights_dir(), "optional", str(meta["file"]))
        if not os.path.isfile(p) and not os.path.isfile(p_opt):
            miss.append(key)
    return miss


def resolve_weight_file(key: str) -> Optional[str]:
    meta = WEIGHTS[key]
    candidates = [str(meta["file"])]
    alt = meta.get("alt_file")
    if alt:
        candidates.append(str(alt))
    # Legacy alias from earlier stub (.pth name for RF++)
    if key == "restoreformer":
        candidates.append("RestoreFormer++.pth")
    for name in candidates:
        p = os.path.join(weights_dir(), name)
        if os.path.isfile(p):
            return p
        p_opt = os.path.join(weights_dir(), "optional", name)
        if os.path.isfile(p_opt):
            return p_opt
    return None


# ---------------------------------------------------------------------------
# Weight download helpers
# ---------------------------------------------------------------------------
def download_weight(
    key: str,
    dest_dir: Optional[str] = None,
    *,
    force: bool = False,
    timeout: int = 300,
) -> str:
    """Download one catalog weight. Returns local path."""
    if key not in WEIGHTS:
        raise KeyError(f"unknown weight key: {key}")
    meta = WEIGHTS[key]
    ddir = dest_dir or weights_dir()
    os.makedirs(ddir, exist_ok=True)
    dest = os.path.join(ddir, str(meta["file"]))
    if os.path.isfile(dest) and not force and os.path.getsize(dest) > 0:
        return dest
    url = str(meta["url"])
    tmp = dest + ".part"
    try:
        urllib.request.urlretrieve(url, tmp)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError(f"empty download for {key}: {url}")
        os.replace(tmp, dest)
    except Exception:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return dest


def download_missing_weights(
    *,
    dest_dir: Optional[str] = None,
    keys: Optional[Sequence[str]] = None,
    force: bool = False,
) -> Dict[str, str]:
    """Download missing expected weights. Returns {key: path}.

    No-op unless PATH_A_DOWNLOAD=1 or force=True (explicit call with force).
    Callers that want silent prep should pass force=True after operator consent.
    """
    if not force and not path_a_download_enabled():
        return {}
    want = list(keys) if keys is not None else [k for k, _ in expected_weight_files()]
    out: Dict[str, str] = {}
    for key in want:
        if key not in WEIGHTS:
            continue
        existing = resolve_weight_file(key)
        if existing and not force:
            out[key] = existing
            continue
        out[key] = download_weight(key, dest_dir=dest_dir, force=force)
    return out


def describe() -> str:
    low, high = estimate_disk_mb()
    miss = missing_weights()
    backends = probe_backends()
    lines = [
        "Path A pipeline",
        f"  FACE_MODEL={face_model()}",
        f"  ENABLE_DDCOLOR={int(enable_ddcolor())}",
        f"  WEIGHTS_DIR={weights_dir()}",
        f"  PATH_A_STRICT={int(path_a_strict())}",
        f"  PATH_A_ALLOW_STUB={int(path_a_allow_stub())}",
        f"  stages: {' → '.join(STAGES)}",
        f"  expected weights disk: ~{low}–{high} MB (canon pack ~0.8–1.5 GB)",
        f"  missing weights: {miss or 'none'}",
        f"  backends: {backends}",
        "  files:",
    ]
    for key, meta in expected_weight_files():
        lines.append(
            f"    - {meta['file']}  (~{meta['approx_mb']} MB)  [{key}]"
        )
        lines.append(f"      {meta['url']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backend probe (ImportError-safe)
# ---------------------------------------------------------------------------
def probe_backends() -> Dict[str, str]:
    """Return availability map: real | stub | missing_weight | forced_stub."""
    if path_a_allow_stub():
        return {s: "forced_stub" for s in STAGES}

    status: Dict[str, str] = {}

    # LaMa
    try:
        import simple_lama_inpainting  # noqa: F401
        status["lama"] = "real" if resolve_weight_file("lama") else "missing_weight"
    except ImportError:
        try:
            import torch  # noqa: F401
            status["lama"] = "real" if resolve_weight_file("lama") else "missing_weight"
        except ImportError:
            status["lama"] = "stub"

    # Face
    fm = face_model()
    if fm == "gfpgan":
        try:
            import gfpgan  # noqa: F401
            status["face"] = "real" if resolve_weight_file("gfpgan") else "missing_weight"
        except ImportError:
            status["face"] = "stub"
    else:
        # RestoreFormer++ — no stable pip pin yet; weight presence only
        status["face"] = (
            "real" if resolve_weight_file("restoreformer") else "missing_weight"
        )
        try:
            import torch  # noqa: F401
        except ImportError:
            status["face"] = "stub"

    # Real-ESRGAN
    try:
        import realesrgan  # noqa: F401
        status["realesrgan"] = (
            "real" if resolve_weight_file("realesrgan") else "missing_weight"
        )
    except ImportError:
        status["realesrgan"] = "stub"

    # DDColor
    if not enable_ddcolor():
        status["ddcolor"] = "skipped"
    else:
        try:
            import torch  # noqa: F401
            status["ddcolor"] = (
                "real" if resolve_weight_file("ddcolor") else "missing_weight"
            )
        except ImportError:
            status["ddcolor"] = "stub"

    return status


# ---------------------------------------------------------------------------
# Result / stage report (unit-testable)
# ---------------------------------------------------------------------------
@dataclass
class StageResult:
    name: str
    backend: str  # real | stub | skipped | error
    ok: bool
    detail: str = ""
    in_path: str = ""
    out_path: str = ""


@dataclass
class PipelineResult:
    ok: bool
    mode: str
    stages: List[StageResult] = field(default_factory=list)
    out_path: str = ""
    face_model: str = ""
    used_ddcolor: bool = False
    backends: Dict[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Image I/O helpers (cv2 preferred, Pillow fallback)
# ---------------------------------------------------------------------------
def _read_bgr(path: str):
    try:
        import cv2
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"decode_failed: {path}")
        return img
    except ImportError:
        from PIL import Image
        import numpy as np
        im = Image.open(path).convert("RGB")
        arr = np.array(im)[:, :, ::-1].copy()  # RGB→BGR
        return arr


def _write_bgr(path: str, img, quality: int = 95) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    try:
        import cv2
        if ext in (".jpg", ".jpeg"):
            ok = cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        else:
            ok = cv2.imwrite(path, img)
        if not ok:
            raise RuntimeError(f"encode_failed: {path}")
        return
    except ImportError:
        from PIL import Image
        import numpy as np
        rgb = img[:, :, ::-1] if getattr(img, "ndim", 0) == 3 else img
        im = Image.fromarray(np.asarray(rgb).astype("uint8"))
        if ext in (".jpg", ".jpeg"):
            im.save(path, quality=quality)
        else:
            im.save(path)


def _copy_image(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    shutil.copy2(src, dst)


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


# ---------------------------------------------------------------------------
# Stage implementations — real backends (ImportError-safe)
# ---------------------------------------------------------------------------
def stage_lama(in_path: str, out_path: str) -> StageResult:
    """Inpaint scratches/holes/stains via LaMa."""
    if path_a_allow_stub():
        _copy_image(in_path, out_path)
        return StageResult("lama", "stub", True, "PATH_A_ALLOW_STUB", in_path, out_path)

    w = resolve_weight_file("lama")
    try:
        # Preferred: simple-lama-inpainting
        from simple_lama_inpainting import SimpleLama  # type: ignore
        from PIL import Image
        import numpy as np

        img = Image.open(in_path).convert("RGB")
        # Auto-mask: light damage heuristic — bright/dark speckles.
        # Without a user mask, apply mild denoise-via-inpaint on detected spots.
        arr = np.array(img)
        gray = (
            0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        ).astype(np.uint8)
        # scratch-like: high local contrast thin marks — approximate via threshold tails
        mask = ((gray < 15) | (gray > 245)).astype(np.uint8) * 255
        # If almost no mask, skip heavy inpaint — identity
        if mask.sum() < 50:
            _copy_image(in_path, out_path)
            return StageResult(
                "lama", "real", True, "no_damage_mask_skip", in_path, out_path
            )
        mask_img = Image.fromarray(mask, mode="L")
        lama = SimpleLama()
        out = lama(img, mask_img)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        out.convert("RGB").save(out_path)
        return StageResult("lama", "real", True, "simple_lama", in_path, out_path)
    except ImportError:
        pass
    except Exception as e:
        if path_a_strict():
            return StageResult("lama", "error", False, str(e)[:200], in_path, out_path)
        # fall through to stub

    if path_a_strict() and not w:
        return StageResult(
            "lama", "error", False, "missing_weight:big-lama.pt", in_path, out_path
        )

    # Offline stub
    _copy_image(in_path, out_path)
    detail = "stub_identity"
    if not w:
        detail += ";missing_weight"
    return StageResult("lama", "stub", True, detail, in_path, out_path)


def stage_face(
    in_path: str,
    out_path: str,
    *,
    fidelity: float = 0.5,
    model: Optional[str] = None,
) -> StageResult:
    """Face restore: GFPGAN (default) or RestoreFormer++."""
    fm = model or face_model()
    if path_a_allow_stub():
        _copy_image(in_path, out_path)
        return StageResult("face", "stub", True, f"PATH_A_ALLOW_STUB:{fm}", in_path, out_path)

    if fm == "gfpgan":
        return _stage_gfpgan(in_path, out_path, fidelity=fidelity)
    return _stage_restoreformer(in_path, out_path, fidelity=fidelity)


def _stage_gfpgan(in_path: str, out_path: str, *, fidelity: float) -> StageResult:
    w = resolve_weight_file("gfpgan")
    try:
        from gfpgan import GFPGANer  # type: ignore
        import cv2

        if not w:
            raise FileNotFoundError("GFPGANv1.4.pth missing")
        # upscale=1: face only; Real-ESRGAN handles global upscale next
        restorer = GFPGANer(
            model_path=w,
            upscale=1,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=None,
        )
        img = cv2.imread(in_path, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("decode_failed")
        # weight: higher → more fidelity to input (less "beautify")
        w_fid = max(0.0, min(1.0, float(fidelity)))
        _, _, output = restorer.enhance(
            img, has_aligned=False, only_center_face=False, paste_back=True, weight=w_fid
        )
        _write_bgr(out_path, output)
        return StageResult("face", "real", True, f"gfpgan:w={w_fid}", in_path, out_path)
    except ImportError:
        pass
    except Exception as e:
        if path_a_strict():
            return StageResult("face", "error", False, f"gfpgan:{e}"[:200], in_path, out_path)

    if path_a_strict() and not w:
        return StageResult(
            "face", "error", False, "missing_weight:GFPGANv1.4.pth", in_path, out_path
        )
    _copy_image(in_path, out_path)
    detail = "stub_identity:gfpgan"
    if not w:
        detail += ";missing_weight"
    return StageResult("face", "stub", True, detail, in_path, out_path)


def _stage_restoreformer(
    in_path: str, out_path: str, *, fidelity: float
) -> StageResult:
    """RestoreFormer++ backend.

    No stable pip package pinned yet — when weight+torch present, try common
    entrypoints; otherwise honest stub.
    """
    w = resolve_weight_file("restoreformer")
    _ = fidelity
    try:
        import torch  # noqa: F401
        # Optional package names vary; try a few without failing hard.
        rf = None
        try:
            from restoreformerplusplus import RestoreFormer  # type: ignore
            rf = RestoreFormer
        except ImportError:
            try:
                from restoreformer import RestoreFormer  # type: ignore
                rf = RestoreFormer
            except ImportError:
                rf = None
        if rf is not None and w:
            # Package APIs differ; if enhance exists, use it.
            model = rf(model_path=w) if callable(rf) else None
            if model is not None and hasattr(model, "enhance"):
                img = _read_bgr(in_path)
                out = model.enhance(img)
                _write_bgr(out_path, out)
                return StageResult(
                    "face", "real", True, "restoreformer++", in_path, out_path
                )
    except Exception as e:
        if path_a_strict():
            return StageResult(
                "face", "error", False, f"restoreformer:{e}"[:200], in_path, out_path
            )

    if path_a_strict() and not w:
        return StageResult(
            "face", "error", False, "missing_weight:RestoreFormer++.pth", in_path, out_path
        )
    _copy_image(in_path, out_path)
    detail = "stub_identity:restoreformer"
    if not w:
        detail += ";missing_weight"
    return StageResult("face", "stub", True, detail, in_path, out_path)


def stage_realesrgan(in_path: str, out_path: str, *, outscale: float = 2.0) -> StageResult:
    """Upscale / background via Real-ESRGAN x2."""
    if path_a_allow_stub():
        _copy_image(in_path, out_path)
        return StageResult(
            "realesrgan", "stub", True, "PATH_A_ALLOW_STUB", in_path, out_path
        )

    w = resolve_weight_file("realesrgan")
    try:
        from realesrgan import RealESRGANer  # type: ignore
        from basicsr.archs.rrdbnet_arch import RRDBNet  # type: ignore
        import cv2

        if not w:
            raise FileNotFoundError("RealESRGAN_x2plus.pth missing")
        device = _device()
        rrdb = RRDBNet(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_block=23, num_grow_ch=32, scale=2,
        )
        upsampler = RealESRGANer(
            scale=2,
            model_path=w,
            model=rrdb,
            half=(device == "cuda"),
            device=device,
        )
        img = cv2.imread(in_path, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("decode_failed")
        output, _ = upsampler.enhance(img, outscale=outscale)
        _write_bgr(out_path, output)
        return StageResult(
            "realesrgan", "real", True, f"x{outscale}", in_path, out_path
        )
    except ImportError:
        pass
    except Exception as e:
        if path_a_strict():
            return StageResult(
                "realesrgan", "error", False, str(e)[:200], in_path, out_path
            )

    if path_a_strict() and not w:
        return StageResult(
            "realesrgan", "error", False,
            "missing_weight:RealESRGAN_x2plus.pth", in_path, out_path,
        )
    _copy_image(in_path, out_path)
    detail = "stub_identity"
    if not w:
        detail += ";missing_weight"
    return StageResult("realesrgan", "stub", True, detail, in_path, out_path)


def stage_ddcolor(in_path: str, out_path: str) -> StageResult:
    """Optional colorization via DDColor."""
    if not enable_ddcolor():
        _copy_image(in_path, out_path)
        return StageResult("ddcolor", "skipped", True, "ENABLE_DDCOLOR=0", in_path, out_path)

    if path_a_allow_stub():
        _copy_image(in_path, out_path)
        return StageResult("ddcolor", "stub", True, "PATH_A_ALLOW_STUB", in_path, out_path)

    w = resolve_weight_file("ddcolor")
    try:
        import torch  # noqa: F401
        # Try known package entrypoints (APIs differ; best-effort).
        try:
            from ddcolor import DDColor  # type: ignore
            if w:
                model = DDColor(model_path=w)
                if hasattr(model, "colorize"):
                    img = _read_bgr(in_path)
                    out = model.colorize(img)
                    _write_bgr(out_path, out)
                    return StageResult(
                        "ddcolor", "real", True, "ddcolor", in_path, out_path
                    )
        except ImportError:
            pass
        try:
            from modelscope.pipelines import pipeline  # type: ignore
            pipe = pipeline("image-colorization")
            result = pipe(in_path)
            out_img = result.get("output_img") if isinstance(result, dict) else None
            if out_img is not None:
                _write_bgr(out_path, out_img)
                return StageResult(
                    "ddcolor", "real", True, "modelscope", in_path, out_path
                )
        except ImportError:
            pass
    except Exception as e:
        if path_a_strict():
            return StageResult("ddcolor", "error", False, str(e)[:200], in_path, out_path)

    if path_a_strict() and not w:
        return StageResult(
            "ddcolor", "error", False,
            "missing_weight:ddcolor_modelscope.pth", in_path, out_path,
        )
    _copy_image(in_path, out_path)
    detail = "stub_identity"
    if not w:
        detail += ";missing_weight"
    return StageResult("ddcolor", "stub", True, detail, in_path, out_path)


# ---------------------------------------------------------------------------
# Pure orchestration (unit-testable)
# ---------------------------------------------------------------------------
def plan_stages(
    mode: str = "authentic",
    *,
    colorize: Optional[bool] = None,
) -> List[str]:
    """Return ordered stage names for mode.

    authentic — LaMa → face → Real-ESRGAN; DDColor if ENABLE_DDCOLOR or colorize=True
    modern    — same order; DDColor on unless colorize explicitly False
    """
    m = (mode or "authentic").strip().lower()
    if m not in ("authentic", "modern"):
        m = "authentic"
    stages = ["lama", "face", "realesrgan"]
    if colorize is True:
        use_color = True
    elif colorize is False:
        use_color = False
    elif enable_ddcolor():
        use_color = True
    elif m == "modern":
        # modern + auto → include DDColor stage (may stub if no weights)
        use_color = True
    else:
        use_color = False
    if use_color:
        stages.append("ddcolor")
    return stages


def run_stages(
    input_path: str,
    out_path: str,
    stage_names: Sequence[str],
    *,
    fidelity: float = 0.5,
    work_dir: Optional[str] = None,
    stage_fns: Optional[Dict[str, Callable[..., StageResult]]] = None,
) -> PipelineResult:
    """Run named stages in order. Pure orchestration — injectable stage_fns for tests."""
    fns = stage_fns or {
        "lama": stage_lama,
        "face": lambda i, o: stage_face(i, o, fidelity=fidelity),
        "realesrgan": stage_realesrgan,
        "ddcolor": stage_ddcolor,
    }
    backends = probe_backends()
    results: List[StageResult] = []
    tmp_root = work_dir or tempfile.mkdtemp(prefix="patha_")
    own_tmp = work_dir is None
    try:
        os.makedirs(tmp_root, exist_ok=True)
        current = input_path
        for idx, name in enumerate(stage_names):
            if name not in fns:
                results.append(
                    StageResult(name, "error", False, "unknown_stage", current, "")
                )
                return PipelineResult(
                    ok=False, mode="", stages=results, out_path="",
                    face_model=face_model(), used_ddcolor="ddcolor" in stage_names,
                    backends=backends, error=f"unknown_stage:{name}",
                )
            next_path = (
                out_path if idx == len(stage_names) - 1
                else os.path.join(tmp_root, f"stage_{idx:02d}_{name}.png")
            )
            # Special-case face lambda already bound; others take (in, out)
            fn = fns[name]
            sr = fn(current, next_path)
            results.append(sr)
            if not sr.ok:
                return PipelineResult(
                    ok=False, mode="", stages=results, out_path="",
                    face_model=face_model(), used_ddcolor="ddcolor" in stage_names,
                    backends=backends, error=sr.detail or name,
                )
            current = next_path
        # Ensure final exists at out_path
        if current != out_path:
            _copy_image(current, out_path)
        return PipelineResult(
            ok=True, mode="", stages=results, out_path=out_path,
            face_model=face_model(), used_ddcolor="ddcolor" in stage_names,
            backends=backends,
        )
    finally:
        if own_tmp:
            shutil.rmtree(tmp_root, ignore_errors=True)


def restore_path_a(
    input_path: Optional[str] = None,
    out_path: Optional[str] = None,
    mode: str = "authentic",
    *,
    img_bytes: Optional[bytes] = None,
    prompt: str = "",
    fidelity: float = 0.5,
    preserve_identity: bool = True,
    colorize: Optional[bool] = None,
) -> Any:
    """Path A restore entrypoint.

    Primary (file) contract:
      restore_path_a(input_path, out_path, mode="authentic"|"modern") -> PipelineResult

    Bytes contract (RunPod handler):
      restore_path_a(img_bytes=..., ...) -> JPEG bytes
      Also accepts positional first-arg bytes for backward compat with
      runpod_handler: restore_path_a(img_bytes, prompt=..., ...)

    Orchestration: LaMa → face(FACE_MODEL) → Real-ESRGAN → [DDColor].
    Offline stubs OK when torch/weights missing (unless PATH_A_STRICT=1).
    """
    _ = (prompt, preserve_identity)  # reserved for future prompt-aware routing

    # Backward compat: first positional was bytes in stub era
    if isinstance(input_path, (bytes, bytearray)):
        img_bytes = bytes(input_path)
        input_path = None

    m = (mode or "authentic").strip().lower()
    if m not in ("authentic", "modern"):
        m = "authentic"

    # Resolve colorize from COLORIZE env if not passed
    if colorize is None:
        cenv = (os.environ.get("COLORIZE") or "auto").strip().lower()
        if cenv in ("1", "true", "yes", "on"):
            colorize = True
        elif cenv in ("0", "false", "no", "off"):
            colorize = False

    stage_names = plan_stages(m, colorize=colorize)

    # --- bytes API ---
    if img_bytes is not None and (input_path is None or out_path is None):
        with tempfile.TemporaryDirectory(prefix="patha_bytes_") as td:
            src = os.path.join(td, "in.jpg")
            dst = os.path.join(td, "out.jpg")
            with open(src, "wb") as f:
                f.write(img_bytes)
            result = run_stages(src, dst, stage_names, fidelity=fidelity)
            result.mode = m
            if not result.ok:
                raise RuntimeError(
                    f"path_a_failed: {result.error or 'unknown'}\n{describe()}"
                )
            with open(dst, "rb") as f:
                return f.read()

    # --- path API ---
    if not input_path or not out_path:
        raise TypeError(
            "restore_path_a(input_path, out_path, mode=...) or "
            "restore_path_a(img_bytes=...) required"
        )
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)

    result = run_stages(input_path, out_path, stage_names, fidelity=fidelity)
    result.mode = m
    if not result.ok and path_a_strict():
        raise RuntimeError(result.error or "path_a_failed")
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        # CLI: path_a_pipeline.py <in> <out> [authentic|modern]
        mode_arg = sys.argv[3] if len(sys.argv) > 3 else "authentic"
        os.environ.setdefault("PATH_A_ALLOW_STUB", "1")
        res = restore_path_a(sys.argv[1], sys.argv[2], mode=mode_arg)
        print(res.to_json() if isinstance(res, PipelineResult) else type(res))
    else:
        print(describe())
