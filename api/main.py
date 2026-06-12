#!/usr/bin/env python3
"""
SaveMyHistory backend API (FastAPI).
- /api/upload-url : выдаёт presigned URL для прямой загрузки фото в DO Spaces
- /api/restorations : создать/получить заказы реставрации (mode: restore|revive)
- /api/health
Auth: проверяем Supabase JWT (Bearer) пользователя, привязываем заказ к user_id.
Секреты — из переменных окружения (на DO App Platform задаются как env vars).
"""
import os, time, uuid, json
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import boto3
from botocore.client import Config
import httpx

app = FastAPI(title="SaveMyHistory API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET", "")          # service key (server-side)
SPACES_KEY = os.environ.get("SPACES_KEY", "")
SPACES_SECRET = os.environ.get("SPACES_SECRET", "")
SPACES_REGION = os.environ.get("SPACES_REGION", "fra1")
SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "smh-photos")
SPACES_ENDPOINT = os.environ.get("SPACES_ENDPOINT", f"https://{SPACES_REGION}.digitaloceanspaces.com")

VALID_MODES = {"restore", "revive"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/tiff", "image/webp", "image/heic", "image/heif"}
FREE_LIMIT = int(os.environ.get("FREE_LIMIT", "5"))   # бесплатных реставраций на юзера
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://savemyhistory.tech")

def s3():
    return boto3.client("s3", region_name=SPACES_REGION, endpoint_url=SPACES_ENDPOINT,
                        aws_access_key_id=SPACES_KEY, aws_secret_access_key=SPACES_SECRET,
                        config=Config(s3={"addressing_style": "virtual"}))

async def get_user(authorization: str):
    """Проверяет Supabase JWT, возвращает user dict."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "no token")
    token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SUPABASE_URL}/auth/v1/user",
                        headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_SECRET})
    if r.status_code != 200:
        raise HTTPException(401, "invalid token")
    return r.json()

async def db(method, path, payload=None, params=None):
    """REST к Supabase (service key, обходит RLS на сервере)."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {"apikey": SUPABASE_SECRET, "Authorization": f"Bearer {SUPABASE_SECRET}",
               "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, url, headers=headers, json=payload, params=params)
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text)
    return r.json() if r.content else None

@app.get("/api/health")
async def health():
    return {"ok": True, "ts": int(time.time())}

@app.get("/api/me")
async def me(authorization: str = Header(None)):
    """Подтверждает пользователя по access_token (серверным ключом). Обходит проблему publishable-ключа на клиенте."""
    user = await get_user(authorization)
    return {"id": user.get("id"), "email": user.get("email"),
            "name": (user.get("user_metadata") or {}).get("full_name")}

@app.post("/api/upload-url")
async def upload_url(request: Request, authorization: str = Header(None)):
    user = await get_user(authorization)
    body = await request.json()
    ext = (body.get("ext") or "jpg").lower().lstrip(".")
    if ext not in {"jpg", "jpeg", "png", "tiff", "webp", "heic"}:
        raise HTTPException(400, "bad ext")
    ctype = body.get("content_type", "image/jpeg")
    if ctype not in IMAGE_TYPES:
        ctype = "image/jpeg"
    key = f"uploads/{user['id']}/{uuid.uuid4().hex}.{ext}"
    url = s3().generate_presigned_url("put_object",
        Params={"Bucket": SPACES_BUCKET, "Key": key, "ContentType": ctype},
        ExpiresIn=600)
    return {"upload_url": url, "key": key}

@app.post("/api/restorations")
async def create_restoration(request: Request, authorization: str = Header(None)):
    user = await get_user(authorization)
    body = await request.json()
    mode = body.get("mode", "restore")
    if mode not in VALID_MODES:
        raise HTTPException(400, "bad mode")
    original_key = body.get("original_key")
    if not original_key:
        raise HTTPException(400, "no original_key")
    # лимит бесплатных реставраций: считаем все заказы юзера (кроме failed)
    existing = await db("GET", "restorations",
                        params={"user_id": f"eq.{user['id']}", "status": "neq.failed", "select": "id"})
    used = len(existing or [])
    if used >= FREE_LIMIT:
        raise HTTPException(402, f"free_limit_reached:{FREE_LIMIT}")
    row = {"user_id": user["id"], "original_key": original_key, "mode": mode, "status": "queued"}
    res = await db("POST", "restorations", payload=row)
    return res[0] if isinstance(res, list) else res

@app.post("/api/restorations/{rid}/retry")
async def retry_restoration(rid: str, authorization: str = Header(None)):
    """Повторить упавшую реставрацию: failed -> queued (только своё)."""
    user = await get_user(authorization)
    res = await db("PATCH", "restorations",
                   params={"id": f"eq.{rid}", "user_id": f"eq.{user['id']}"},
                   payload={"status": "queued", "error": None})
    if not res:
        raise HTTPException(404, "not found")
    return res[0] if isinstance(res, list) else res

@app.post("/api/restorations/{rid}/share-card")
async def share_card(rid: str, authorization: str = Header(None)):
    """Готовит share-ссылку/карточку для готовой реставрации."""
    user = await get_user(authorization)
    rows = await db("GET", "restorations", params={"id": f"eq.{rid}", "user_id": f"eq.{user['id']}", "select": "*"})
    if not rows:
        raise HTTPException(404, "not found")
    row = rows[0]
    if row.get("status") != "done":
        raise HTTPException(400, "not_ready")
    return {
        "ok": True,
        "share_url": f"{PUBLIC_URL}/cabinet.html?rid={rid}",
        "caption": "SaveMyHistory"
    }

@app.post("/api/restorations/{rid}/report")
async def report_restoration(rid: str, authorization: str = Header(None)):
    """Пользователь сообщает о проблеме (напр. исказилось лицо). Помечаем flagged."""
    user = await get_user(authorization)
    # без миграции: пишем пометку в существующее поле error
    res = await db("PATCH", "restorations",
                   params={"id": f"eq.{rid}", "user_id": f"eq.{user['id']}"},
                   payload={"error": "user_reported"})
    if not res:
        raise HTTPException(404, "not found")
    return {"ok": True}

@app.get("/api/restorations")
async def list_restorations(authorization: str = Header(None)):
    user = await get_user(authorization)
    res = await db("GET", "restorations",
                   params={"user_id": f"eq.{user['id']}", "select": "*", "order": "created_at.desc"})
    rows = res or []
    # добавляем временные ссылки для превью (исходник + результат)
    client = s3()
    for r in rows:
        try:
            if r.get("original_key"):
                r["original_url"] = client.generate_presigned_url("get_object",
                    Params={"Bucket": SPACES_BUCKET, "Key": r["original_key"]}, ExpiresIn=3600)
            if r.get("result_key"):
                r["result_url"] = client.generate_presigned_url("get_object",
                    Params={"Bucket": SPACES_BUCKET, "Key": r["result_key"]}, ExpiresIn=3600)
        except Exception:
            pass
    return rows
