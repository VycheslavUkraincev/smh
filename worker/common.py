#!/usr/bin/env python3
"""SaveMyHistory worker — общие хелперы (БД, Spaces, vision, fal).
Используется analyze.py / generate.py / verify.py.
Секреты из ENV (на DO Worker задаются как env vars):
  SUPABASE_URL, SUPABASE_SECRET, SPACES_KEY, SPACES_SECRET, SPACES_REGION,
  SPACES_BUCKET, SPACES_ENDPOINT, OPENAI_API_KEY, FAL_KEY
"""
import os, json, time, urllib.request, urllib.error

SUPA_URL = os.environ["SUPABASE_URL"].rstrip("/")
SECRET   = os.environ["SUPABASE_SECRET"]
BUCKET   = os.environ.get("SPACES_BUCKET", "smh-photos")
REGION   = os.environ.get("SPACES_REGION", "fra1")
ENDPOINT = os.environ.get("SPACES_ENDPOINT", f"https://{REGION}.digitaloceanspaces.com")

def log(stage, msg):
    print(f"{time.strftime('%H:%M:%S')} [{stage}] {msg}", flush=True)

# ---------- Supabase REST ----------
def db(method, path, payload=None, params=""):
    url = f"{SUPA_URL}/rest/v1/{path}{params}"
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("apikey", SECRET); r.add_header("Authorization", f"Bearer {SECRET}")
    r.add_header("Content-Type", "application/json"); r.add_header("Prefer", "return=representation")
    try:
        resp = urllib.request.urlopen(r, timeout=30); body = resp.read().decode()
        return json.loads(body) if body else []
    except urllib.error.HTTPError as e:
        log("db", f"ERR {e.code} {e.read().decode()[:200]}"); return None

def rpc(name, payload):
    url = f"{SUPA_URL}/rest/v1/rpc/{name}"
    r = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    r.add_header("apikey", SECRET); r.add_header("Authorization", f"Bearer {SECRET}")
    r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=40); body = resp.read().decode()
        return json.loads(body) if body else []
    except urllib.error.HTTPError as e:
        log("rpc", f"ERR {e.code} {e.read().decode()[:200]}"); return None

def count_status(status):
    """Сколько реставраций в статусе. Пробуем RPC, фолбэк — REST count (не зависит от миграции)."""
    try:
        v = rpc("count_status", {"p_status": status})
        if isinstance(v, int):
            return v
        if isinstance(v, list) and v and isinstance(v[0], int):
            return v[0]
    except Exception:
        pass
    # фолбэк: REST HEAD c Prefer:count=exact → читаем Content-Range
    try:
        url = f"{SUPA_URL}/rest/v1/restorations?status=eq.{status}&select=id"
        r = urllib.request.Request(url, method="GET")
        r.add_header("apikey", SECRET); r.add_header("Authorization", f"Bearer {SECRET}")
        r.add_header("Prefer", "count=exact"); r.add_header("Range", "0-0")
        resp = urllib.request.urlopen(r, timeout=30)
        cr = resp.headers.get("Content-Range", "")  # формат "0-0/123"
        if "/" in cr:
            return int(cr.split("/")[-1])
    except Exception as e:
        log("count", f"err {str(e)[:80]}")
    return 0

def claim(from_status, to_status, limit):
    """Атомарно взять пачку фото нужного статуса (skip locked)."""
    return rpc("claim_restorations", {"p_from": from_status, "p_to": to_status, "p_limit": limit}) or []

def update_row(rid, fields):
    return db("PATCH", "restorations", payload=fields, params=f"?id=eq.{rid}")

# ---------- DO Spaces (boto3 опционально; presigned через API) ----------
def s3():
    import boto3
    from botocore.client import Config
    return boto3.client("s3", region_name=REGION, endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ["SPACES_KEY"], aws_secret_access_key=os.environ["SPACES_SECRET"],
        config=Config(s3={"addressing_style": "virtual"}))

def presigned_get(key, ttl=3600):
    return s3().generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=ttl)

# ---------- Vision (OpenAI gpt-4o) ----------
def vision(prompt, image_url, max_tokens=900):
    """Отправляет картинку + промпт в gpt-4o vision, возвращает текст ответа."""
    key = os.environ["OPENAI_API_KEY"]
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
        "max_tokens": max_tokens, "temperature": 0.2,
    }
    r = urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(), method="POST")
    r.add_header("Authorization", f"Bearer {key}"); r.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(r, timeout=90)
    out = json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"]
