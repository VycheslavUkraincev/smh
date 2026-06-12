#!/usr/bin/env python3
"""SaveMyHistory — раннер пайплайна (один проход всех стадий).
Порядок: recover зависших → ИИ-глаза (1) → генерация (2) → ИИ-проверка (3).
Запускать по cron (напр. каждые 2-5 мин) как DO Worker / scheduled job.
  python worker_run.py
Опц. ENV: ANALYZE_BATCH, GEN_BATCH, VERIFY_BATCH, GPU_THRESHOLD
"""
import os
from common import log, rpc
import analyze, generate, verify

def main():
    # 0) восстановить зависшие
    try:
        n = rpc("recover_stuck", {})
        if n: log("run", f"recover: вернул {n} зависших")
    except Exception as e:
        log("run", f"recover err: {str(e)[:80]}")

    a = int(os.environ.get("ANALYZE_BATCH", "10"))
    g = int(os.environ.get("GEN_BATCH", "4"))
    v = int(os.environ.get("VERIFY_BATCH", "10"))

    # 1) ИИ-глаза (дёшево, всегда)
    na = analyze.main(a)
    # 3) проверка готовых (дёшево, всегда) — делаем до генерации, чтобы освобождать очередь
    nv = verify.main(v)
    # 2) генерация (дорого) — в API-режиме сразу; в GPU-режиме оркестратор решает порог
    ng = generate.main(g)

    log("run", f"итог прохода: analyzed={na} generated={ng} verified={nv}")

if __name__ == "__main__":
    main()
