#!/usr/bin/env python3
"""SaveMyHistory — раннер пайплайна (один проход всех стадий).
Порядок: recover зависших → ИИ-глаза (1) → генерация (2) → ИИ-проверка (3).
Запускать по cron (напр. каждые 2-5 мин) как DO Worker / scheduled job.
  python run.py [--once]
Опц. ENV:
  ANALYZE_BATCH, GEN_BATCH, VERIFY_BATCH
  INTAKE_ONLY=1 — день: глаза+verify, генерация выкл (free overnight накопление)
  INTAKE_ONLY=0 + GEN_MIN_BATCH=N — ночь: генерация когда накопилось >= N
  PRIORITY_BYPASS=1 — игнор INTAKE_ONLY и порога батча (ops/paid smoke без колонки lane)
"""
import os
from common import log, rpc, count_status, analyze_credit_backoff_active, analyze_credit_backoff_remaining
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

    # РЕЖИМ НАКОПЛЕНИЯ (INTAKE_ONLY=1):
    #   фото копятся и готовятся (ИИ-глаза), но НЕ генерируются — ждут ночного батча.
    intake_only = os.environ.get("INTAKE_ONLY", "0") == "1"
    # ПОРОГ БАТЧА (GEN_MIN_BATCH): генерация стартует только когда накопилось >= N фото.
    min_batch = int(os.environ.get("GEN_MIN_BATCH", "0"))
    # PRIORITY_BYPASS: ops-рычаг без колонки lane в БД — разрешить generate даже днём.
    priority_bypass = os.environ.get("PRIORITY_BYPASS", "0") == "1"

    if analyze_credit_backoff_active():
        log("run", f"ANALYZE credit_backoff ~{int(analyze_credit_backoff_remaining())}s — глаза пропустят claim")

    # 1) ИИ-глаза (дёшево; skip claim if credit backoff) — готовит фото к генерации
    na = analyze.main(a)
    # 3) проверка готовых (дёшево, всегда)
    nv = verify.main(v)

    if intake_only and not priority_bypass:
        # считаем, сколько ждёт генерации, и НЕ генерируем
        waiting = count_status("analyzed")
        log("run", f"INTAKE_ONLY: analyzed={na} verified={nv} | ждёт ночного батча: {waiting}")
        return

    if intake_only and priority_bypass:
        log("run", "PRIORITY_BYPASS=1: генерация поверх INTAKE_ONLY")

    # 2) генерация (дорого). Если задан порог — ждём накопления (bypass снимает порог).
    if min_batch > 0 and not priority_bypass:
        waiting = count_status("analyzed")
        if waiting < min_batch:
            log("run", f"порог батча: {waiting}/{min_batch} — генерацию откладываем")
            log("run", f"итог прохода: analyzed={na} generated=0 verified={nv}")
            return
    ng = generate.main(g)

    log("run", f"итог прохода: analyzed={na} generated={ng} verified={nv}")

def loop(interval=120):
    """Постоянный режим: проход каждые interval сек. Для worker-сервиса на DO."""
    import time
    log("run", f"loop старт, интервал {interval}с")
    while True:
        try:
            main()
        except Exception as e:
            log("run", f"проход упал: {str(e)[:120]}")
        time.sleep(interval)

if __name__ == "__main__":
    import os, sys
    if "--once" in sys.argv:
        main()
    else:
        loop(int(os.environ.get("LOOP_INTERVAL", "120")))
