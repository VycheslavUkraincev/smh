#!/usr/bin/env python3
"""SaveMyHistory — Path A commercial pipeline stub (D4).

Canon order (ONE photo):
  1) LaMa              — inpaint scratches / holes / stains
  2) GFPGAN | RF++     — face restore (gentle, no beautify)
  3) Real-ESRGAN       — upscale / background
  4) DDColor           — only if color / faded color needed

CodeFormer ≠ commercial default. Keep it on GEN_STACK=legacy only.

This module is SAFE PREP: documents stages, weight URLs, disk budget,
and ENV switches. Inference wiring lands after Docker Path A image
build + GPU smoke (Roman «GPU да» + $cap). Do NOT rent GPU here.

ENV
----
  WEIGHTS_DIR          default /weights
  FACE_MODEL           gfpgan | restoreformer   (default: gfpgan)
  ENABLE_DDCOLOR       0|1                      (default: 0)
  PATH_A_STRICT        0|1  if 1, missing weights → hard error

Disk budget (weights only, GFPGAN path): ~0.8–1.5 GB
Full CUDA image (torch+CUDA): typically 8–15 GB — not counted below.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

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
        # HuggingFace / AdvPaint mirror commonly used by lama-cleaner:
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
            "v1.3.0/GFPGANv1.4.pth"
        ),
        "required": True,  # when FACE_MODEL=gfpgan
    },
    "restoreformer": {
        "file": "RestoreFormer++.pth",
        "approx_mb": 380,
        # Placeholder URL — pin exact release asset before first Path A build.
        "url": (
            "https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/"
            "v1.0.0/RestoreFormer++.pth"
        ),
        "required": False,  # when FACE_MODEL=restoreformer
        "note": "Confirm release asset name/URL before docker build.",
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
        # Placeholder — pin official DDColor checkpoint before enabling.
        "url": (
            "https://huggingface.co/piddnad/DDColor-models/resolve/main/"
            "ddcolor_modelscope.pth"
        ),
        "required": False,
        "note": "Only if ENABLE_DDCOLOR=1; confirm URL before build.",
    },
}


def face_model() -> str:
    """FACE_MODEL=gfpgan|restoreformer (default gfpgan)."""
    v = (os.environ.get("FACE_MODEL") or "gfpgan").strip().lower()
    if v in ("rfpp", "restoreformer++", "restoreformer_plusplus"):
        return "restoreformer"
    if v not in ("gfpgan", "restoreformer"):
        return "gfpgan"
    return v


def enable_ddcolor() -> bool:
    return (os.environ.get("ENABLE_DDCOLOR") or "0").strip() in ("1", "true", "yes")


def weights_dir() -> str:
    return os.environ.get("WEIGHTS_DIR") or "/weights"


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
    # band: catalog sum ± ~20% → fits stated 0.8–1.5 GB for GFPGAN path
    low = max(800, int(total * 0.85))
    high = max(low, int(total * 1.15))
    return low, high


def describe() -> str:
    low, high = estimate_disk_mb()
    lines = [
        "Path A pipeline (stub)",
        f"  FACE_MODEL={face_model()}",
        f"  ENABLE_DDCOLOR={int(enable_ddcolor())}",
        f"  WEIGHTS_DIR={weights_dir()}",
        f"  stages: {' → '.join(STAGES)}",
        f"  expected weights disk: ~{low}–{high} MB (canon pack ~0.8–1.5 GB)",
        "  files:",
    ]
    for key, meta in expected_weight_files():
        lines.append(
            f"    - {meta['file']}  (~{meta['approx_mb']} MB)  [{key}]"
        )
        lines.append(f"      {meta['url']}")
    return "\n".join(lines)


def restore_path_a(
    img_bytes: bytes,
    prompt: str = "",
    fidelity: float = 0.5,
    preserve_identity: bool = True,
    colorize: Optional[bool] = None,
) -> bytes:
    """Path A restore entrypoint.

    STUB: raises until real LaMa/GFPGAN/ESRGAN/DDColor wiring + weights
    are present in the Path A image (see Dockerfile.gpu.patha).

    Contract mirrors legacy runpod_handler._restore (bytes in → JPEG bytes).
    """
    _ = (prompt, fidelity, preserve_identity, colorize)
    raise NotImplementedError(
        "path_a_pipeline.restore_path_a is a stub — build Dockerfile.gpu.patha, "
        "download weights, implement stages, then set GEN_STACK=path_a after smoke. "
        f"Current plan:\n{describe()}"
    )


if __name__ == "__main__":
    print(describe())
