#!/usr/bin/env python3
"""SaveMyHistory — end-to-end тест пайплайна (запускать ПОСЛЕ migration_eyes.sql).
Берёт одно существующее фото из Spaces, создаёт тестовый заказ status=queued,
прогоняет analyze → generate → verify, печатает результат каждой стадии.
Не трогает реальные пользовательские заказы (помечает test=true в error).
Запуск: .venv/bin/python worker_e2e_test.py <original_key>
  где original_key — ключ существующего фото в бакете (напр. uploads/.../x.jpg)
"""
import os, sys, json, uuid, time, pathlib

# локальные ключи → ENV
os.environ.setdefault("SUPABASE_URL", json.load(open(".supabase_config.json"))["url"])
os.environ.setdefault("SUPABASE_SECRET", pathlib.Path(".supabase_secret").read_text().strip())
sp = json.load(open(".do_spaces.json"))
for k, v in {"SPACES_KEY": sp["access_key"], "SPACES_SECRET": sp["secret_key"],
             "SPACES_REGION": sp["region"], "SPACES_BUCKET": sp["bucket"], "SPACES_ENDPOINT": sp["endpoint"]}.items():
    os.environ.setdefault(k, v)

from common import db, log
import analyze, generate, verify

TEST_USER = "00000000-0000-0000-0000-0000000000ee"  # фиктивный, для теста

def main(original_key):
    print("=== E2E TEST пайплайна ===")
    # 1) создать тестовый заказ
    row = {"user_id": TEST_USER, "original_key": original_key, "mode": "restore",
           "status": "queued", "error": "e2e_test"}
    res = db("POST", "restorations", payload=row)
    if not res:
        print("❌ не удалось создать тестовый заказ (миграция применена? FK user_id?)")
        return
    rid = res[0]["id"]
    print(f"✅ создан тестовый заказ {rid[:8]} status=queued")

    print("\n--- СТАДИЯ 1: ИИ-глаза (analyze) ---")
    analyze.main(1)
    r = (db("GET", "restorations", params={"id": f"eq.{rid}", "select": "status,analysis,prompt"}) or [{}])[0]
    print("статус:", r.get("status"))
    print("анализ:", json.dumps(r.get("analysis"), ensure_ascii=False)[:300])
    print("промпт:", (r.get("prompt") or "")[:120], "...")

    print("\n--- СТАДИЯ 2: генерация (generate) ---")
    generate.main(1)
    r = (db("GET", "restorations", params={"id": f"eq.{rid}", "select": "status,result_key"}) or [{}])[0]
    print("статус:", r.get("status"), "| result_key:", r.get("result_key"))

    print("\n--- СТАДИЯ 3: ИИ-проверка (verify) ---")
    verify.main(1)
    r = (db("GET", "restorations", params={"id": f"eq.{rid}", "select": "status,qc"}) or [{}])[0]
    print("статус:", r.get("status"))
    print("QC:", json.dumps(r.get("qc"), ensure_ascii=False)[:300])

    print("\n=== очистка тестового заказа ===")
    db("DELETE", "restorations", params={"id": f"eq.{rid}"})
    print("✅ тестовый заказ удалён. E2E завершён.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: worker_e2e_test.py <original_key>"); sys.exit(1)
    main(sys.argv[1])
