#!/usr/bin/env python3
"""SaveMyHistory — download Path A model weights (no GPU rent).

Canon pack: LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → [DDColor]
CodeFormer is NOT downloaded here (legacy only).

Usage (on a machine with disk, e.g. Vast instance):
  python3 worker/scripts/download_path_a_weights.py
  python3 worker/scripts/download_path_a_weights.py --dest /weights
  python3 worker/scripts/download_path_a_weights.py --with-ddcolor --with-rfpp
  SKIP_LARGE=1 python3 worker/scripts/download_path_a_weights.py   # skip >100MB
  python3 worker/scripts/download_path_a_weights.py --dry-run

Vast one-liner (after SSH into rented box — rent only after Roman «GPU да»):
  mkdir -p /weights && python3 worker/scripts/download_path_a_weights.py --dest /weights

Sandbox note: Portal sandbox ~100MB file limit — large weights cannot fully land
there. Use SKIP_LARGE=1 for catalog/size checks only, or download on Vast/GPU host.

Checksums: GitHub/HF release assets rarely ship sha256 sidecars. This script
records SHA256 after download into weights.sha256.txt for later verify.
Official sizes below are approximate; hard fail if file < min_mb or empty.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from typing import Dict, List, Optional

# Official public release URLs (HTTP 200 verified 2026-08-06).
# Keep in sync with worker/path_a_pipeline.py WEIGHTS and Dockerfile.gpu.patha.
CATALOG: Dict[str, Dict[str, object]] = {
    "lama": {
        "file": "big-lama.pt",
        "url": (
            "https://github.com/Sanster/models/releases/download/"
            "add_big_lama/big-lama.pt"
        ),
        "approx_mb": 200,
        "min_mb": 50,
        "core": True,
        "note": "LaMa inpaint (Sanster big-lama)",
    },
    "gfpgan": {
        "file": "GFPGANv1.4.pth",
        "url": (
            "https://github.com/TencentARC/GFPGAN/releases/download/"
            "v1.3.4/GFPGANv1.4.pth"
        ),
        "approx_mb": 340,
        "min_mb": 100,
        "core": True,
        "note": "Face restore default (FACE_MODEL=gfpgan)",
    },
    "realesrgan": {
        "file": "RealESRGAN_x2plus.pth",
        "url": (
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.2.1/RealESRGAN_x2plus.pth"
        ),
        "approx_mb": 64,
        "min_mb": 20,
        "core": True,
        "note": "Upscale / background",
    },
    "restoreformer": {
        "file": "RestoreFormer++.ckpt",
        "url": (
            "https://github.com/wzhouxiff/RestoreFormerPlusPlus/releases/download/"
            "v1.0.0/RestoreFormer%2B%2B.ckpt"
        ),
        "approx_mb": 280,
        "min_mb": 80,
        "core": False,
        "optional_flag": "--with-rfpp",
        "note": (
            "Optional face (FACE_MODEL=restoreformer). "
            "Alt GFPGAN-arch: RestoreFormer.pth from GFPGAN v1.3.4."
        ),
        "alt_file": "RestoreFormer.pth",
        "alt_url": (
            "https://github.com/TencentARC/GFPGAN/releases/download/"
            "v1.3.4/RestoreFormer.pth"
        ),
        "alt_approx_mb": 280,
    },
    "ddcolor": {
        "file": "ddcolor_modelscope.pth",
        "url": (
            "https://huggingface.co/piddnad/DDColor-models/resolve/main/"
            "ddcolor_modelscope.pth"
        ),
        "approx_mb": 350,
        "min_mb": 80,
        "core": False,
        "optional_flag": "--with-ddcolor",
        "note": "Optional color (ENABLE_DDCOLOR=1)",
    },
}

SKIP_LARGE_MB = 100  # Portal sandbox / small disks


def skip_large() -> bool:
    return (os.environ.get("SKIP_LARGE") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def human_mb(n_bytes: int) -> float:
    return n_bytes / (1024 * 1024)


def download_one(
    key: str,
    meta: Dict[str, object],
    dest_dir: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    use_alt: bool = False,
) -> Dict[str, object]:
    """Download one weight. Returns status dict."""
    fname = str(meta["alt_file"] if use_alt and meta.get("alt_file") else meta["file"])
    url = str(meta["alt_url"] if use_alt and meta.get("alt_url") else meta["url"])
    approx = int(meta.get("alt_approx_mb") if use_alt and meta.get("alt_approx_mb") else meta["approx_mb"])
    min_mb = int(meta.get("min_mb") or 1)
    dest = os.path.join(dest_dir, fname)

    if skip_large() and approx > SKIP_LARGE_MB:
        return {
            "key": key,
            "file": fname,
            "status": "skipped_skip_large",
            "approx_mb": approx,
            "path": dest,
            "note": f"SKIP_LARGE=1 and ~{approx}MB > {SKIP_LARGE_MB}MB",
        }

    if os.path.isfile(dest) and not force and os.path.getsize(dest) > 0:
        size = os.path.getsize(dest)
        if human_mb(size) < min_mb:
            return {
                "key": key,
                "file": fname,
                "status": "too_small_existing",
                "bytes": size,
                "path": dest,
                "note": f"existing file < min_mb={min_mb}; use --force",
            }
        digest = sha256_file(dest) if not dry_run else "(dry-run)"
        return {
            "key": key,
            "file": fname,
            "status": "exists",
            "bytes": size,
            "sha256": digest,
            "path": dest,
        }

    if dry_run:
        return {
            "key": key,
            "file": fname,
            "status": "would_download",
            "approx_mb": approx,
            "url": url,
            "path": dest,
        }

    os.makedirs(dest_dir, exist_ok=True)
    tmp = dest + ".part"
    print(f"[{key}] downloading ~{approx} MB → {dest}")
    print(f"         {url}")
    try:
        urllib.request.urlretrieve(url, tmp)
        size = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
        if size == 0:
            raise RuntimeError("empty download")
        if human_mb(size) < min_mb:
            raise RuntimeError(
                f"download too small: {human_mb(size):.1f} MB < min {min_mb} MB"
            )
        os.replace(tmp, dest)
    except Exception as e:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return {
            "key": key,
            "file": fname,
            "status": "error",
            "error": str(e)[:300],
            "url": url,
            "path": dest,
        }

    digest = sha256_file(dest)
    print(f"[{key}] OK {human_mb(size):.1f} MB  sha256={digest[:16]}…")
    return {
        "key": key,
        "file": fname,
        "status": "downloaded",
        "bytes": size,
        "sha256": digest,
        "url": url,
        "path": dest,
    }


def write_checksum_log(dest_dir: str, rows: List[Dict[str, object]]) -> Optional[str]:
    lines = [
        "# SaveMyHistory Path A weights — SHA256 recorded after download",
        "# Official upstream rarely publishes sidecars; verify against this log.",
        "# Format: sha256  filename",
        "",
    ]
    any_hash = False
    for r in rows:
        digest = r.get("sha256")
        fname = r.get("file")
        if digest and fname and digest != "(dry-run)" and r.get("status") in (
            "downloaded", "exists",
        ):
            lines.append(f"{digest}  {fname}")
            any_hash = True
    if not any_hash:
        return None
    path = os.path.join(dest_dir, "weights.sha256.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def select_keys(args: argparse.Namespace) -> List[str]:
    keys: List[str] = []
    for k, meta in CATALOG.items():
        if meta.get("core"):
            keys.append(k)
            continue
        if k == "ddcolor" and args.with_ddcolor:
            keys.append(k)
        if k == "restoreformer" and args.with_rfpp:
            keys.append(k)
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        keys = [k for k in keys if k in want] or [k for k in CATALOG if k in want]
    return keys


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Download SaveMyHistory Path A weights (no GPU rent)."
    )
    p.add_argument(
        "--dest",
        default=os.environ.get("WEIGHTS_DIR") or "./weights",
        help="Destination directory (default WEIGHTS_DIR or ./weights)",
    )
    p.add_argument("--with-ddcolor", action="store_true", help="Also fetch DDColor")
    p.add_argument(
        "--with-rfpp", action="store_true",
        help="Also fetch RestoreFormer++.ckpt",
    )
    p.add_argument(
        "--rfpp-alt", action="store_true",
        help="With --with-rfpp: download RestoreFormer.pth (GFPGAN arch) instead",
    )
    p.add_argument("--force", action="store_true", help="Re-download even if present")
    p.add_argument("--dry-run", action="store_true", help="Print plan only")
    p.add_argument(
        "--only",
        default="",
        help="Comma keys: lama,gfpgan,realesrgan,restoreformer,ddcolor",
    )
    p.add_argument(
        "--list", action="store_true", help="List catalog and exit",
    )
    args = p.parse_args(argv)

    if args.list:
        print("Path A weight catalog (official URLs)\n")
        for k, m in CATALOG.items():
            tag = "core" if m.get("core") else f"optional {m.get('optional_flag')}"
            print(f"  [{k}] {m['file']}  ~{m['approx_mb']} MB  ({tag})")
            print(f"       {m['url']}")
            print(f"       {m['note']}")
            if m.get("alt_url"):
                print(f"       alt: {m['alt_file']} → {m['alt_url']}")
            print()
        print("Checksum note: after download, SHA256 written to weights.sha256.txt")
        print("SKIP_LARGE=1 skips files with approx_mb > 100 (sandbox-safe).")
        print()
        print("Vast one-liner (ONLY after Roman «GPU да» + $cap + second confirm):")
        print(
            "  mkdir -p /weights && "
            "python3 worker/scripts/download_path_a_weights.py --dest /weights"
        )
        return 0

    keys = select_keys(args)
    if not keys:
        print("Nothing selected. Use defaults (core) or --with-ddcolor / --with-rfpp.")
        return 2

    dest_dir = os.path.abspath(args.dest)
    print(f"dest={dest_dir}")
    print(f"SKIP_LARGE={int(skip_large())}  dry_run={int(args.dry_run)}")
    print(f"keys={keys}")
    print()

    rows: List[Dict[str, object]] = []
    errors = 0
    for key in keys:
        meta = CATALOG[key]
        use_alt = bool(key == "restoreformer" and args.rfpp_alt)
        row = download_one(
            key, meta, dest_dir,
            force=args.force, dry_run=args.dry_run, use_alt=use_alt,
        )
        rows.append(row)
        st = row.get("status")
        print(f"  {key}: {st}  {row.get('file')}")
        if st in ("error", "too_small_existing"):
            errors += 1
            if row.get("error"):
                print(f"         ERROR: {row['error']}")

    if not args.dry_run:
        log_path = write_checksum_log(dest_dir, rows)
        if log_path:
            print(f"\nchecksum log: {log_path}")

    # Summary disk estimate
    total_approx = sum(int(CATALOG[k]["approx_mb"]) for k in keys)
    print(f"\napprox selected pack: ~{total_approx} MB (GFPGAN core ~0.6 GB; full ~0.8–1.5 GB)")
    print("CodeFormer: NOT in Path A catalog (legacy image only).")
    print("Do NOT rent GPU from this script.")

    if errors:
        print(f"\nFAIL: {errors} error(s)")
        return 1
    skipped = sum(1 for r in rows if str(r.get("status", "")).startswith("skipped"))
    if skipped and skip_large():
        print(f"\nOK (partial): {skipped} skipped via SKIP_LARGE — re-run on Vast/GPU host")
        return 0
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
