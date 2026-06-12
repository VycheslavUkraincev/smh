#!/usr/bin/env python3
"""
SaveMyHistory backend API (FastAPI).
- /api/upload-url : выдаёт presigned URL для прямой загрузки фото в DO Spaces
- /api/restorations : создать/получить заказы реставрации (mode: restore|revive)
- /api/health
Auth: проверяем Supabase JWT (Bearer) пользователя, привязываем заказ к user_id.
Секреты — из переменных окружения (на DO App Platform задаются как env vars).
"""
import os, time, uuid, json, io, hmac, hashlib
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import boto3
from botocore.client import Config
import httpx
from PIL import Image, ImageDraw, ImageFont

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
SHARE_SECRET = os.environ.get("SHARE_SECRET", SUPABASE_SECRET or "smh-share")


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

async def get_profile(uid):
    """Профиль юзера (квота/согласие). Если нет колонок миграции — вернёт что есть."""
    try:
        res = await db("GET", "profiles", params={"id": f"eq.{uid}", "select": "*"})
        return (res or [{}])[0]
    except Exception:
        return {}

async def _share_payload(rid, user_id):
    rows = await db("GET", "restorations", params={"id": f"eq.{rid}", "user_id": f"eq.{user_id}", "select": "*"})
    if not rows:
        raise HTTPException(404, "not found")
    row = rows[0]
    if row.get("status") != "done":
        raise HTTPException(400, "not_ready")
    return row

def _short_sig(rid):
    return hmac.new(SHARE_SECRET.encode(), rid.encode(), hashlib.sha256).hexdigest()[:8]

@app.post("/api/redeem-code")
async def redeem_code(request: Request, authorization: str = Header(None)):
    """Активация инвайт-кода + согласие на программу. Начисляет квоту."""
    user = await get_user(authorization)
    body = await request.json()
    code = (body.get("code") or "").strip()
    consent = bool(body.get("consent"))
    if not code:
        raise HTTPException(400, "no_code")
    if not consent:
        raise HTTPException(400, "consent_required")
    # атомарно через RPC redeem_invite
    url = f"{SUPABASE_URL}/rest/v1/rpc/redeem_invite"
    headers = {"apikey": SUPABASE_SECRET, "Authorization": f"Bearer {SUPABASE_SECRET}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, headers=headers, json={"p_user": user["id"], "p_code": code})
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text)
    out = r.json()
    if not out.get("ok"):
        raise HTTPException(400, out.get("reason", "redeem_failed"))
    # фиксируем согласие
    await db("PATCH", "profiles", params={"id": f"eq.{user['id']}"},
             payload={"program_consent": True, "consent_at": "now()"})
    return {"ok": True, "granted": out.get("granted")}

@app.post("/api/feedback")
async def submit_feedback(request: Request, authorization: str = Header(None)):
    """Отзыв/фидбек по результату."""
    user = await get_user(authorization)
    body = await request.json()
    row = {"user_id": user["id"], "rating": body.get("rating"),
           "text": (body.get("text") or "")[:2000],
           "restoration_id": body.get("restoration_id"),
           "allow_public": bool(body.get("allow_public", True))}
    try:
        await db("POST", "feedback", payload=row)
    except Exception as e:
        raise HTTPException(400, "feedback_failed")
    return {"ok": True}

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

@app.get("/api/restorations/{rid}/share-card")
async def share_card(rid: str, authorization: str = Header(None)):
    """Отдаёт share preview HTML для готовой реставрации."""
    user = await get_user(authorization)
    row = await _share_payload(rid, user["id"])
    client = s3()
    before_key = row.get("original_key")
    after_key = row.get("result_key") or row.get("original_key")
    before = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": before_key}, ExpiresIn=3600) if before_key else ""
    after = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": after_key}, ExpiresIn=3600) if after_key else before
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>SaveMyHistory</title><style>body{{margin:0;background:#f4eee4;font-family:system-ui,sans-serif;color:#402a1b;display:grid;place-items:center;min-height:100vh}}.card{{width:min(1080px,92vw);background:#f5efe6;border:1px solid rgba(160,120,60,.25);border-radius:28px;box-shadow:0 20px 60px rgba(0,0,0,.10);overflow:hidden}}.pad{{padding:28px}}.top{{font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#a67c32;font-size:12px}}.shot{{position:relative;margin-top:14px;border-radius:22px;overflow:hidden;background:#efe6d7}}.shot img{{display:block;width:100%;height:auto}}.tag{{position:absolute;top:16px;left:16px;background:#000;color:#fff;padding:8px 12px;border-radius:999px;font-size:12px}}.tag.r{{left:auto;right:16px;background:#a67c32}}.copy{{padding:18px 0 0;font-size:28px;line-height:1.15;font-family:Georgia,serif}}.sub{{margin-top:8px;font-size:18px;color:#6f5c46}}.ft{{margin-top:18px;padding:16px 20px;background:#a67c32;color:#fff;font-weight:700;text-align:center;border-radius:18px}}.url{{margin-top:8px;font-size:12px;color:#8a7a66;text-align:center}}</style></head><body><div class='card'><div class='pad'><div class='top'>SAVE MY HISTORY · restored family memory</div><div class='shot'><img src='{after}' alt='restored family photo'><div class='tag'>BEFORE</div><div class='tag r'>AFTER</div></div><div class='copy'>We brought a family photo back to life.</div><div class='sub'>Each restored picture can travel through social media and bring another family back.</div><div class='ft'>Share your story</div><div class='url'>savemyhistory.tech · { _short_sig(rid) }</div></div></div></body></html>"""
    return HTMLResponse(html)

@app.post("/api/restorations/{rid}/share-card")
async def share_card_json(rid: str, authorization: str = Header(None)):
    """JSON для мобильного share / clipboard fallback."""
    user = await get_user(authorization)
    await _share_payload(rid, user["id"])
    return {"ok": True, "share_url": f"{PUBLIC_URL}/api/restorations/{rid}/share-card", "caption": "SaveMyHistory"}

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
