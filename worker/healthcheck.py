#!/usr/bin/env python3
"""SaveMyHistory worker health-check: проверяет готовность пайплайна.
Запускать локально перед деплоем. Не меняет данные (кроме безопасного RPC-теста).
"""
import os, json, urllib.request, urllib.error, pathlib

# подгрузить локальные ключи в ENV (sandbox)
os.environ.setdefault("SUPABASE_URL", json.load(open(".supabase_config.json"))["url"])
os.environ.setdefault("SUPABASE_SECRET", pathlib.Path(".supabase_secret").read_text().strip())
sp = json.load(open(".do_spaces.json"))
os.environ.setdefault("SPACES_KEY", sp["access_key"]); os.environ.setdefault("SPACES_SECRET", sp["secret_key"])
os.environ.setdefault("SPACES_REGION", sp["region"]); os.environ.setdefault("SPACES_BUCKET", sp["bucket"])
os.environ.setdefault("SPACES_ENDPOINT", sp["endpoint"])

from worker_common import db, rpc, s3, BUCKET, vision

def check(name, fn):
    try:
        r = fn(); print(f"  ✅ {name}: {r}")
        return True
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:120]}")
        return False

print("=== SaveMyHistory pipeline health-check ===")
# 1) база — таблица restorations
check("DB restorations", lambda: f"{len(db('GET','restorations',params='?select=id&limit=1') or [])} rows reachable")
# 2) новые поля миграции (analysis)
check("Migration fields (analysis)", lambda: "поле есть" if db('GET','restorations',params='?select=analysis&limit=1') is not None else "?")
# 3) RPC claim_restorations (миграция 2)
check("RPC claim_restorations", lambda: f"вернул {len(rpc('claim_restorations',{'p_from':'__none__','p_to':'__none__','p_limit':0}) or [])} (ок если 0)")
# 4) RPC recover_stuck
check("RPC recover_stuck", lambda: f"вернул {rpc('recover_stuck',{})}")
# 5) Spaces доступ
check("DO Spaces", lambda: f"bucket {BUCKET}, {len(s3().list_objects_v2(Bucket=BUCKET,MaxKeys=1).get('Contents',[]))} obj sample")
# 6) OpenAI vision (только наличие ключа, без вызова чтобы не тратить)
check("OPENAI_API_KEY", lambda: "set" if os.environ.get("OPENAI_API_KEY") else "MISSING")
check("FAL_KEY", lambda: "set" if os.environ.get("FAL_KEY") else "MISSING")
print("=== конец ===")
