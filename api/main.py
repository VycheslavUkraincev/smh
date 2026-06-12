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


def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]
    for p in paths:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fit_cover(im, tw, th):
    w, h = im.size
    if w == 0 or h == 0:
        return Image.new("RGB", (tw, th), "#efe6d7")
    s = max(tw / w, th / h)
    im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    x = max(0, (im.width - tw) // 2)
    y = max(0, (im.height - th) // 2)
    return im.crop((x, y, x + tw, y + th))


async def _fetch_image(url: str):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url)
    if r.status_code >= 300:
        raise HTTPException(400, "image_fetch_failed")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


async def _share_images(rid, user_id=None, row=None):
    if row is None:
        if user_id is not None:
            row = await _share_payload(rid, user_id)
        else:
            rows = await db("GET", "restorations", params={"id": f"eq.{rid}", "select": "*"})
            if not rows:
                raise HTTPException(404, "not found")
            row = rows[0]
            if row.get("status") != "done":
                raise HTTPException(400, "not_ready")
    client = s3()
    before_key = row.get("original_key")
    after_key = row.get("result_key") or row.get("original_key")
    before_url = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": before_key}, ExpiresIn=3600) if before_key else ""
    after_url = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": after_key}, ExpiresIn=3600) if after_key else before_url
    return row, before_url, after_url


async def _share_row_for_request(rid, authorization=None, sig=None):
    if authorization:
        user = await get_user(authorization)
        return await _share_payload(rid, user["id"])
    if not sig or not hmac.compare_digest(sig, _short_sig(rid)):
        raise HTTPException(403, "bad share token")
    rows = await db("GET", "restorations", params={"id": f"eq.{rid}", "select": "*"})
    if not rows:
        raise HTTPException(404, "not found")
    row = rows[0]
    if row.get("status") != "done":
        raise HTTPException(400, "not_ready")
    return row


async def _share_jpeg(rid, user_id=None, row=None):
    row, before_url, after_url = await _share_images(rid, user_id=user_id, row=row)
    before = await _fetch_image(before_url) if before_url else None
    after = await _fetch_image(after_url) if after_url else before
    W, H = 1200, 628
    canvas = Image.new("RGB", (W, H), (244, 238, 228))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([16, 16, W - 16, H - 16], radius=28, fill=(245, 239, 230), outline=(166, 124, 50), width=2)
    draw.text((42, 36), "SAVE MY HISTORY", font=_font(22, True), fill=(166, 124, 50))
    draw.text((42, 72), "Restored family memory", font=_font(18), fill=(111, 92, 70))
    panel_y = 120
    panel_h = 400
    panel_w = (W - 100) // 2
    bx = 42
    ax = bx + panel_w + 16
    if before is None:
        before = after
    before = _fit_cover(before, panel_w, panel_h)
    after = _fit_cover(after, panel_w, panel_h)
    canvas.paste(before, (bx, panel_y))
    canvas.paste(after, (ax, panel_y))
    for x, label, color in [(bx + 16, "BEFORE", (0, 0, 0)), (ax + 16, "AFTER", (166, 124, 50))]:
        draw.rounded_rectangle([x, panel_y + 16, x + 102, panel_y + 44], radius=14, fill=color)
        draw.text((x + 51, panel_y + 30), label, font=_font(14, True), fill=(255, 255, 255), anchor="mm")
    draw.text((42, 548), "Old photo. New life.", font=_font(34, True), fill=(64, 42, 27))
    draw.text((42, 596), f"Restore a memory · {_short_sig(rid)}", font=_font(18), fill=(111, 92, 70))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue(), row

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

@app.get("/api/restorations/{rid}/share-card.png")
async def share_card_png(rid: str, authorization: str = Header(None), sig: str = None):
    """PNG share-карта для соцсетей."""
    row = await _share_row_for_request(rid, authorization, sig)
    png, _ = await _share_jpeg(rid, row=row)
    return Response(content=png, media_type="image/jpeg")

@app.get("/api/restorations/{rid}/share-card")
async def share_card(rid: str, authorization: str = Header(None), sig: str = None):
    """Отдаёт share preview HTML для готовой реставрации."""
    row = await _share_row_for_request(rid, authorization, sig)
    _png, row = await _share_jpeg(rid, row=row)
    client = s3()
    before_key = row.get("original_key")
    after_key = row.get("result_key") or row.get("original_key")
    before = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": before_key}, ExpiresIn=3600) if before_key else ""
    after = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": after_key}, ExpiresIn=3600) if after_key else before
    image_url = f"{PUBLIC_URL}/api/restorations/{rid}/share-card.png?sig={_short_sig(rid)}"
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta property='og:title' content='SaveMyHistory'><meta property='og:description' content='We brought a family photo back to life.'><meta property='og:image' content='{image_url}'><meta property='og:image:alt' content='Before and after family photo restoration'><meta property='og:type' content='article'><meta property='og:image:width' content='1200'><meta property='og:image:height' content='628'>
    <title>SaveMyHistory</title><style>body{{margin:0;background:#f4eee4;font-family:system-ui,sans-serif;color:#402a1b;display:grid;place-items:center;min-height:100vh}}.card{{width:min(1080px,92vw);background:#f5efe6;border:1px solid rgba(160,120,60,.25);border-radius:28px;box-shadow:0 20px 60px rgba(0,0,0,.10);overflow:hidden}}.pad{{padding:28px}}.top{{font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#a67c32;font-size:12px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.shot{{position:relative;border-radius:22px;overflow:hidden;background:#efe6d7;min-height:240px}}.shot img{{display:block;width:100%;height:100%;object-fit:cover}}.tag{{position:absolute;top:16px;left:16px;background:#000;color:#fff;padding:8px 12px;border-radius:999px;font-size:12px}}.tag.r{{background:#a67c32}}.copy{{padding:18px 0 0;font-size:28px;line-height:1.15;font-family:Georgia,serif}}.sub{{margin-top:8px;font-size:18px;color:#6f5c46}}.ft{{margin-top:18px;padding:16px 20px;background:#a67c32;color:#fff;font-weight:700;text-align:center;border-radius:18px}}.url{{margin-top:8px;font-size:12px;color:#8a7a66;text-align:center}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}.copy{{font-size:24px}}}}</style></head><body><div class='card'><div class='pad'><div class='top'>SAVE MY HISTORY · restored family memory</div><div class='grid'><div class='shot'><img src='{before or after}' alt='before restoration'><div class='tag'>BEFORE</div></div><div class='shot'><img src='{after}' alt='after restoration'><div class='tag r'>AFTER</div></div></div><div class='copy'>We brought a family photo back to life.</div><div class='sub'>Each restored picture can travel through social media and bring another family back.</div><div class='ft'>See the transformation</div><div class='url'>savemyhistory.tech · { _short_sig(rid) }</div></div></div></body></html>"""
    return HTMLResponse(html)

@app.post("/api/restorations/{rid}/share-card")
async def share_card_json(rid: str, authorization: str = Header(None)):
    """JSON для мобильного share / clipboard fallback."""
    user = await get_user(authorization)
    _png, _row = await _share_jpeg(rid, user_id=user["id"])
    return {"ok": True, "share_url": f"{PUBLIC_URL}/api/restorations/{rid}/share-card?sig={_short_sig(rid)}", "caption": "SaveMyHistory"}

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
