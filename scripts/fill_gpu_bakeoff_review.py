#!/usr/bin/env python3
"""Fill gpu_bakeoff_review.json AFTER + Q comments when PATH_C_BAKEOFF_GO_RESULT lands.

Usage:
  python3 scripts/fill_gpu_bakeoff_review.py \
    [--result path/to/PATH_C_BAKEOFF_GO_RESULT.txt] \
    [--after-dir path/with/pd_XX_after.jpg] \
    [--dry-run]

Rules:
  - NEVER invent pd_05 AFTER. Copy only if a real file exists.
  - Does not redesign the page; only updates JSON + after/ assets.
  - spend_E=0 (no GPU / Nano).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REVIEW = REPO / "api" / "restorer_corpus" / "gpu_bakeoff_review.json"
AFTER_DIR = REPO / "api" / "restorer_corpus" / "gpu_bakeoff" / "after"
DEFAULT_RESULT = Path("projects/savemyhistory/.openclaw/tmp/PATH_C_BAKEOFF_GO_RESULT.txt")
IDS = ("pd_05", "pd_02", "pd_10", "pd_11")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_result(text: str) -> dict:
    out = {
        "destroy": None,
        "cost_usd": None,
        "candidate": None,
        "gpu": None,
        "verdicts": {},
        "comments": {},
    }
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"(?i)DESTROY\s*[:=]\s*(Y|N|YES|NO|PENDING|UNKNOWN)", s)
        if m:
            v = m.group(1).upper()
            out["destroy"] = "Y" if v in ("Y", "YES") else ("N" if v in ("N", "NO") else v)
        m = re.match(r"(?i)(?:COST|SPENT|USD)\s*[:=]\s*\$?([0-9.]+)", s)
        if m:
            try:
                out["cost_usd"] = float(m.group(1))
            except ValueError:
                pass
        m = re.match(r"(?i)CANDIDATE\s*[:=]\s*(.+)$", s)
        if m:
            out["candidate"] = m.group(1).strip()
        m = re.match(r"(?i)GPU\s*[:=]\s*(.+)$", s)
        if m:
            out["gpu"] = m.group(1).strip()
        m = re.match(r"(?i)(pd_\d{2})\s*[:=]\s*(PASS|WEAK|FAIL)\b(.*)$", s)
        if m:
            pid, verd, rest = m.group(1), m.group(2).upper(), (m.group(3) or "").strip(" :-	")
            out["verdicts"][pid] = verd
            if rest:
                out["comments"].setdefault(pid, {})["summary"] = rest
        m = re.match(
            r"(?i)(pd_\d{2})\.(faces|clothes|invent|damage_heal|sharpness_vs_likeness|summary)\s*[:=]\s*(.+)$",
            s,
        )
        if m:
            out["comments"].setdefault(m.group(1), {})[m.group(2)] = m.group(3).strip()
    return out


def find_after(pid: str, search_dirs: list[Path]) -> Path | None:
    names = [
        f"{pid}.jpg",
        f"{pid}.png",
        f"{pid}_after.jpg",
        f"{pid}_pathc_after.jpg",
        f"AFTER_{pid}.jpg",
        f"{pid}_brushnet.jpg",
        f"{pid}_out.jpg",
    ]
    for d in search_dirs:
        if not d or not d.is_dir():
            continue
        for n in names:
            p = d / n
            if p.is_file() and p.stat().st_size > 0:
                return p
        # recursive shallow
        for n in names:
            hits = list(d.rglob(n))
            for p in hits:
                if p.is_file() and p.stat().st_size > 0:
                    return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    ap.add_argument("--after-dir", type=Path, action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not REVIEW.is_file():
        raise SystemExit(f"missing review json: {REVIEW}")
    if not args.result.is_file():
        raise SystemExit(f"RESULT not ready: {args.result}")

    parsed = parse_result(args.result.read_text(encoding="utf-8", errors="replace"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    run = review.setdefault("run", {})
    if parsed.get("destroy"):
        run["destroy"] = parsed["destroy"]
    if parsed.get("cost_usd") is not None:
        run["cost_usd"] = parsed["cost_usd"]
    if parsed.get("candidate"):
        run["candidate"] = parsed["candidate"]
    if parsed.get("gpu"):
        run["gpu"] = parsed["gpu"]

    search = list(args.after_dir) + [
        Path("projects/savemyhistory/.openclaw/tmp/path_c_bakeoff"),
        Path("projects/savemyhistory/.openclaw/tmp/path_c_brushnet_out"),
        AFTER_DIR,
    ]

    filled = 0
    cards = review.get("cards") or []
    for card in cards:
        pid = card.get("id") or ""
        if pid not in IDS:
            continue
        src = find_after(pid, search)
        if not src:
            # honest: keep WAITING / NO_AFTER — never invent, especially pd_05
            if card.get("after_status") not in ("FILLED",):
                card["after_status"] = "NO_AFTER" if args.result.is_file() else "WAITING"
                card["after_file"] = ""
            continue
        dest_name = f"{pid}.jpg"
        dest = AFTER_DIR / dest_name
        print(f"AFTER {pid}: {src} -> {dest}")
        if not args.dry_run:
            AFTER_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        card["after_file"] = dest_name
        card["after_status"] = "FILLED"
        filled += 1
        v = parsed["verdicts"].get(pid)
        if v:
            card["q_verdict"] = v
        qc = card.setdefault("q_comments", {})
        for k, val in (parsed["comments"].get(pid) or {}).items():
            qc[k] = val

    review["status"] = "FILLED" if filled == len(cards) else ("PARTIAL" if filled else "WAITING")
    review["updated_at_utc"] = utc_now()
    run["fill_hook_utc"] = review["updated_at_utc"]
    run["n_after_filled"] = filled

    if args.dry_run:
        print(json.dumps({"status": review["status"], "filled": filled, "n_cards": len(cards)}, ensure_ascii=False, indent=2))
        return 0

    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK status={review['status']} filled={filled}/{len(cards)} -> {REVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
