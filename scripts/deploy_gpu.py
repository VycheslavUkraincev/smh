#!/usr/bin/env python3
"""SaveMyHistory — deploy_gpu.py
Готовит и развёртывает GPU-эндпоинт на RunPod при появлении баланса.
Запускать: python3 scripts/deploy_gpu.py
"""
import os, json, urllib.request, subprocess, sys

DO_APP_ID = "f622ac8f-d9b7-448e-8953-12f948064a68"
GHCR_IMAGE = "ghcr.io/vycheslavukraincev/smh/smh-gpu:latest"

secrets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "../.secrets")
RP_KEY = open(os.path.join(secrets_dir, "runpod_key.txt")).read().strip()
DO_TOKEN = open(os.path.join(secrets_dir, "do_token.txt")).read().strip()

def graphql(query):
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        "https://api.runpod.io/graphql", data=data,
        headers={"Authorization": f"Bearer {RP_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def create_endpoint():
    """Создаёт Serverless endpoint на RunPod с RTX 4090."""
    print("→ Создаю GPU-эндпоинт на RunPod...")
    query = json.dumps({
        "query": "mutation { saveEndpoint(input: {name: \"smh-gpu\"}) { id name } }"
    }).encode()
    req = urllib.request.Request(
        "https://api.runpod.io/graphql", data=query,
        headers={"Authorization": f"Bearer {RP_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
        print(f"  Результат: {json.dumps(result, indent=2)[:200]}")
        return result.get("data", {}).get("saveEndpoint", {}).get("id")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "balance" in body.lower():
            print("  ❌ Нужен баланс! Пополните RunPod или подайтесь на startup.")
            print("     runpod.io/startup-program → Starter Tier = $1000")
            return None
        print(f"  ❌ Ошибка: {body[:200]}")
        return None

def do_update_env(endpoint_id):
    """Меняет env vars в DO App Platform."""
    print(f"\n→ Меняю DO vars (endpoint_id: {endpoint_id})...")
    # DO API для обновления env vars
    url = f"https://api.digitalocean.com/v2/apps/{DO_APP_ID}"
    # Сначала читаем текущий spec
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {DO_TOKEN}"})
    with urllib.request.urlopen(req) as r:
        app = json.loads(r.read())
    spec = app.get("app", {}).get("spec", {})
    for w in spec.get("workers", []):
        if w["name"] == "smh-worker":
            cur_envs = {e["key"]: e for e in w.get("envs", [])}
            cur_envs["INTAKE_ONLY"] = {"key": "INTAKE_ONLY", "value": "0"}
            cur_envs["GEN_PROVIDER"] = {"key": "GEN_PROVIDER", "value": "gpu"}
            cur_envs["GEN_MIN_BATCH"] = {"key": "GEN_MIN_BATCH", "value": "1"}
            cur_envs["RUNPOD_ENDPOINT"] = {
                "key": "RUNPOD_ENDPOINT",
                "value": f"https://api.runpod.io/v2/{endpoint_id}/runsync"
            }
            cur_envs["RUNPOD_KEY"] = {
                "key": "RUNPOD_KEY",
                "value": RP_KEY,
                "type": "SECRET"
            }
            w["envs"] = list(cur_envs.values())
    # Обновляем spec
    update = json.dumps({"spec": spec}).encode()
    req2 = urllib.request.Request(url, data=update,
        headers={"Authorization": f"Bearer {DO_TOKEN}", "Content-Type": "application/json"},
        method="PUT")
    try:
        with urllib.request.urlopen(req2) as r:
            result = json.loads(r.read())
        print(f"  ✅ DO обновлён, деплой запущен")
        print(f"  Деплой: {result.get('app',{}).get('active_deployment',{}).get('id','?')[:16]}")
    except urllib.error.HTTPError as e:
        print(f"  ❌ Ошибка DO: {e.read().decode()[:200]}")

if __name__ == "__main__":
    if "--check" in sys.argv:
        eid = create_endpoint()
        if eid:
            print(f"\n✅ Endpoint ID: {eid}")
            print(f"→ Run 'python3 scripts/deploy_gpu.py --deploy {eid}' для активации")
    elif "--deploy" in sys.argv and len(sys.argv) > 2:
        eid = sys.argv[2]
        do_update_env(eid)
        print(f"\n✅ Готово! SMH теперь использует GPU ({eid})")
        print(f"→ Деплой на DO займёт 2-3 мин")
    else:
        print(f"""SaveMyHistory GPU Deploy Tool
=============================
Использование:
  python3 scripts/deploy_gpu.py --check   Проверить баланс и создать endpoint
  python3 scripts/deploy_gpu.py --deploy <endpoint_id>   Активировать GPU-режим

Шаги:
  1. Пополнить RunPod (мин $0.01) или податься на startup ($1000 бесплатно)
  2. python3 scripts/deploy_gpu.py --check
  3. python3 scripts/deploy_gpu.py --deploy <id>
""")
