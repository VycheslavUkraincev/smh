import secrets
#!/usr/bin/env python3
"""
SaveMyHistory backend API (FastAPI).
- /api/upload-url : выдаёт presigned URL для прямой загрузки фото в DO Spaces
- /api/restorations : создать/получить заказы реставрации (mode: restore|revive)
- /api/waitlist : soft-waitlist (email + optional name/note)
- /api/health
Auth: проверяем Supabase JWT (Bearer) пользователя, привязываем заказ к user_id.
Секреты — из переменных окружения (на DO App Platform задаются как env vars).
"""
import os, time, uuid, json, io, hmac, hashlib, secrets, string, re
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import boto3
from botocore.client import Config
from waitlist_fallback import (
    append_entry as _waitlist_spaces_append,
    count_entries as _waitlist_spaces_count,
    load_entries as _waitlist_spaces_load,
)
from restorer_feedback import (
    build_event as _fb_build_event,
    publish_feedback as _fb_publish,
    publish_marks_export as _fb_publish_marks_export,
    publish_region_notes_export as _fb_publish_region_notes_export,
    load_marks_spaces as _fb_load_marks_spaces,
    save_marks_spaces as _fb_save_marks_spaces,
)
import httpx
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="SaveMyHistory API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ARENA_HTML = r"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex>
<title>Арена · Чат конкурса</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d0b09;color:#f4ecdd;font-family:system-ui,-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:14px 18px;border-bottom:1px solid rgba(201,162,75,.2);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
header h1{font-size:16px;letter-spacing:.04em}
header .who{margin-left:auto;display:flex;gap:6px;align-items:center}
select,input,button,textarea{font-family:inherit;font-size:14px}
select,#secret{background:#1a1510;color:#f4ecdd;border:1px solid rgba(201,162,75,.3);border-radius:8px;padding:7px 9px}
#feed{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:80%;padding:10px 13px;border-radius:12px;background:#16110c;border:1px solid rgba(201,162,75,.14);line-height:1.4;white-space:pre-wrap;word-wrap:break-word}
.msg .a{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#c9a24b;margin-bottom:3px;display:flex;gap:8px}
.msg .t{font-size:10px;color:#7a6f5d}
.me{align-self:flex-end;background:#23371f;border-color:rgba(120,180,110,.3)}
.cmd{align-self:center;max-width:90%;background:#2a1e0c;border-color:#c9a24b;text-align:center}
.sum{align-self:center;max-width:92%;background:#101820;border-color:#3a6ea5}
.upd{border-left:3px solid #c9a24b}
.rev{border-left:3px solid #6ea53a}
footer{padding:12px;border-top:1px solid rgba(201,162,75,.2);display:flex;gap:8px;flex-wrap:wrap}
textarea{flex:1;min-width:160px;background:#1a1510;color:#f4ecdd;border:1px solid rgba(201,162,75,.3);border-radius:10px;padding:10px;resize:none;height:46px}
button{background:#c9a24b;color:#0d0b09;border:none;border-radius:10px;padding:0 18px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:#c9a24b;border:1px solid rgba(201,162,75,.4)}
.kindsel{display:flex;gap:5px;align-items:center}
</style></head><body>
<header>
  <h1>🏆 Арена · Чат конкурса</h1>
  <div class=who>
    <span style="font-size:12px;color:#b5a892">я:</span>
    <select id=author><option>Флорентиец</option><option>О</option><option>М</option><option>С</option></select>
    <input id=secret placeholder="код" style="width:90px">
  </div>
</header>
<div id=feed></div>
<footer>
  <div class=kindsel>
    <select id=kind><option value=msg>сообщение</option><option value=update>обновил</option><option value=review>ознакомился</option><option value=command>команда</option></select>
  </div>
  <textarea id=body placeholder="Напиши и нажми Enter…"></textarea>
  <button onclick=send()>➤</button>
  <button class=ghost onclick=summary()>Саммери</button>
</footer>
<script>
const API=location.origin;let last=0;
const feed=document.getElementById('feed');
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function render(m){
  const me=document.getElementById('author').value;
  const d=document.createElement('div');
  let cls='msg';
  if(m.kind==='command')cls+=' cmd';else if(m.kind==='summary')cls+=' sum';
  else{if(m.author===me)cls+=' me';if(m.kind==='update')cls+=' upd';if(m.kind==='review')cls+=' rev';}
  d.className=cls;
  const t=new Date(m.created_at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
  d.innerHTML='<div class=a><span>'+esc(m.author)+'</span><span class=t>'+t+'</span></div>'+esc(m.body);
  feed.appendChild(d);
}
async function poll(){
  try{const r=await fetch(API+'/api/arena/messages?after='+last);const j=await r.json();
    (j.messages||[]).forEach(m=>{render(m);last=Math.max(last,m.id);});
    feed.scrollTop=feed.scrollHeight;}catch(e){}
}
async function send(){
  const body=document.getElementById('body').value.trim();if(!body)return;
  const author=document.getElementById('author').value;
  const secret=document.getElementById('secret').value.trim();
  const kind=document.getElementById('kind').value;
  const r=await fetch(API+'/api/arena/send',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({author,body,kind,secret})});
  if(r.status===401){alert('Неверный код доступа');return;}
  document.getElementById('body').value='';poll();
}
async function summary(){
  const secret=document.getElementById('secret').value.trim();
  const r=await fetch(API+'/api/arena/summary',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({secret})});
  if(r.status===401){alert('Неверный код доступа');return;}
  poll();
}
document.getElementById('body').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
document.getElementById('secret').value=localStorage.getItem('arena_secret')||'';
document.getElementById('secret').addEventListener('change',e=>localStorage.setItem('arena_secret',e.target.value));
document.getElementById('author').value=localStorage.getItem('arena_author')||'Флорентиец';
document.getElementById('author').addEventListener('change',e=>localStorage.setItem('arena_author',e.target.value));
poll();setInterval(poll,3000);
</script></body></html>"""


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
# lane/priority: overnight (free default) | realtime (paid later). Persisted only after DB column exists.
VALID_LANES = {"overnight", "realtime"}
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://savemyhistory.tech")
SHARE_SECRET = os.environ.get("SHARE_SECRET", SUPABASE_SECRET or "smh-share")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

# Soft-waitlist (aligns with waitlist_add.py / WAITLIST.md fields)
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)
WAITLIST_RATE_WINDOW = 60  # seconds
WAITLIST_RATE_MAX = 5      # requests per IP per window
_waitlist_hits = defaultdict(list)

async def require_admin(authorization: str):
    """Пускает только email из белого списка ADMIN_EMAILS."""
    user = await get_user(authorization)
    email = (user.get("email") or "").strip().lower()
    if not email or email not in ADMIN_EMAILS:
        raise HTTPException(403, "not admin")
    return user


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


def _client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64] or "unknown"
    return (request.client.host if request.client else "unknown")[:64]


def _waitlist_rate_ok(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _waitlist_hits[ip] if now - t < WAITLIST_RATE_WINDOW]
    if len(recent) >= WAITLIST_RATE_MAX:
        _waitlist_hits[ip] = recent
        return False
    recent.append(now)
    _waitlist_hits[ip] = recent
    return True


def _waitlist_table_missing(detail) -> bool:
    text = str(detail).lower()
    return (
        "could not find the table" in text
        or "pgrst205" in text
        or 'relation "public.waitlist" does not exist' in text
        or "relation 'public.waitlist' does not exist" in text
    )


async def _waitlist_count() -> int:
    """Exact row count without returning PII."""
    url = f"{SUPABASE_URL}/rest/v1/waitlist"
    headers = {
        "apikey": SUPABASE_SECRET,
        "Authorization": f"Bearer {SUPABASE_SECRET}",
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-0",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=headers, params={"select": "id"})
    if r.status_code >= 300:
        raise HTTPException(r.status_code, r.text)
    cr = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
    if "/" in cr:
        total = cr.split("/")[-1].strip()
        if total.isdigit():
            return int(total)
    return 0


async def _restorations_count(status: str) -> int:
    """Exact restorations count by status via REST Prefer:count=exact (no count_status RPC)."""
    url = f"{SUPABASE_URL}/rest/v1/restorations"
    headers = {
        "apikey": SUPABASE_SECRET,
        "Authorization": f"Bearer {SUPABASE_SECRET}",
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-0",
    }
    params = {"status": f"eq.{status}", "select": "id"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url, headers=headers, params=params)
        if r.status_code >= 300:
            return 0
        cr = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
        if "/" in cr:
            total = cr.split("/")[-1].strip()
            if total.isdigit():
                return int(total)
    except Exception:
        return 0
    return 0


@app.get("/api/health")
async def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/waitlist/status")
async def waitlist_status():
    """Публичный счётчик waitlist без PII."""
    try:
        count = await _waitlist_count()
    except HTTPException as e:
        if _waitlist_table_missing(e.detail):
            n = _waitlist_spaces_count(s3(), SPACES_BUCKET)
            if n is not None:
                return {"ok": True, "ready": True, "count": n, "storage": "spaces_fallback"}
            return {
                "ok": True,
                "ready": False,
                "count": 0,
                "table": "missing",
                "hint": "Apply migration_waitlist.sql in Supabase SQL Editor",
            }
        raise
    return {"ok": True, "ready": True, "count": count}



@app.get("/api/waitlist")
async def waitlist_get():
    """GET alias → status (count / table-missing). POST is the subscribe path."""
    return await waitlist_status()

@app.post("/api/waitlist")
async def waitlist_join(request: Request):
    """Soft-waitlist: email + optional name/note/source. Upsert by email.
    Honesty: доступ откроем письмом; реставрации обычно к утру / до 48h — без мгновенного SLA.
    Требует таблицу public.waitlist (см. migration_waitlist.sql).
    """
    if not _waitlist_rate_ok(_client_ip(request)):
        raise HTTPException(429, "rate_limited")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "bad_json")
    if not isinstance(body, dict):
        raise HTTPException(422, "bad_json")
    email = (body.get("email") or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        raise HTTPException(422, "invalid_email")
    name = (body.get("name") or "").strip()[:120] or None
    note = (body.get("note") or "").strip()[:500] or None
    source = (body.get("source") or "api").strip()[:40] or "api"

    missing_payload = {
        "ok": False,
        "ready": False,
        "table": "missing",
        "detail": "waitlist_table_missing",
        "hint": "Apply migration_waitlist.sql in Supabase SQL Editor",
        "message_ru": "Список ожидания ещё настраивается. Напишите нам email — сохраним вручную. Обычно доступ и реставрации — к утру / до 48 часов.",
        "message_ro": "Lista de așteptare încă se configurează. Scrieți-ne emailul — îl salvăm manual. Accesul și restaurările — de obicei până dimineață / în 48h.",
        "message_en": "Waitlist is still being set up. Email us and we will save it manually. Access/restorations usually by morning / within 48h.",
    }

    try:
        existing = await db("GET", "waitlist", params={
            "email": f"eq.{email}",
            "select": "id,email",
            "limit": "1",
        })
    except HTTPException as e:
        if _waitlist_table_missing(e.detail):
            try:
                already_fb, _n = _waitlist_spaces_append(s3(), SPACES_BUCKET, email, name=name, note=note, source=source)
                return {
                    "ok": True,
                    "already_subscribed": already_fb,
                    "status": "already_subscribed" if already_fb else "joined",
                    "storage": "spaces_fallback",
                    "message_ru": "Спасибо. Напишем на email, когда откроем доступ. Реставрации — обычно к утру / до 48 часов, без мгновенного SLA.",
                    "message_ro": "Mulțumim. Vă scriem pe email când deschidem accesul. Restaurările — de obicei până dimineață / în 48h, fără SLA instant.",
                    "message_en": "Thanks. We'll email when access opens. Restorations usually by morning / within 48h — not instant.",
                }
            except Exception:
                return JSONResponse(status_code=503, content=missing_payload)
        raise

    already = bool(existing)
    if already:
        patch = {}
        if name is not None:
            patch["name"] = name
        if note is not None:
            patch["note"] = note
        if source:
            patch["source"] = source
        if patch:
            try:
                await db("PATCH", "waitlist", params={"email": f"eq.{email}"}, payload=patch)
            except HTTPException as e:
                if _waitlist_table_missing(e.detail):
                    try:
                        already_fb, _n = _waitlist_spaces_append(s3(), SPACES_BUCKET, email, name=name, note=note, source=source)
                        return {
                            "ok": True,
                            "already_subscribed": already_fb,
                            "status": "already_subscribed" if already_fb else "joined",
                            "storage": "spaces_fallback",
                            "message_ru": "Спасибо. Напишем на email, когда откроем доступ. Реставрации — обычно к утру / до 48 часов, без мгновенного SLA.",
                            "message_ro": "Mulțumim. Vă scriem pe email când deschidem accesul. Restaurările — de obicei până dimineață / în 48h, fără SLA instant.",
                            "message_en": "Thanks. We'll email when access opens. Restorations usually by morning / within 48h — not instant.",
                        }
                    except Exception:
                        return JSONResponse(status_code=503, content=missing_payload)
                raise
    else:
        payload = {"email": email, "name": name, "note": note, "source": source}
        try:
            await db("POST", "waitlist", payload=payload)
        except HTTPException as e:
            if _waitlist_table_missing(e.detail):
                try:
                    already_fb, _n = _waitlist_spaces_append(s3(), SPACES_BUCKET, email, name=name, note=note, source=source)
                    return {
                        "ok": True,
                        "already_subscribed": already_fb,
                        "status": "already_subscribed" if already_fb else "joined",
                        "storage": "spaces_fallback",
                        "message_ru": "Спасибо. Напишем на email, когда откроем доступ. Реставрации — обычно к утру / до 48 часов, без мгновенного SLA.",
                        "message_ro": "Mulțumim. Vă scriem pe email când deschidem accesul. Restaurările — de obicei până dimineață / în 48h, fără SLA instant.",
                        "message_en": "Thanks. We'll email when access opens. Restorations usually by morning / within 48h — not instant.",
                    }
                except Exception:
                    return JSONResponse(status_code=503, content=missing_payload)
            detail = str(e.detail).lower()
            if e.status_code in (409, 23505) or "duplicate" in detail or "unique" in detail:
                already = True
            else:
                raise

    return {
        "ok": True,
        "already_subscribed": already,
        "status": "already_subscribed" if already else "joined",
        "message_ru": "Спасибо. Напишем на email, когда откроем доступ. Реставрации — обычно к утру / до 48 часов, без мгновенного SLA.",
        "message_ro": "Mulțumim. Vă scriem pe email când deschidem accesul. Restaurările — de obicei până dimineață / în 48h, fără SLA instant.",
        "message_en": "Thanks. We'll email when access opens. Restorations usually by morning / within 48h — not instant.",
    }

@app.get("/api/me")
async def me(authorization: str = Header(None)):
    """Подтверждает пользователя по access_token (серверным ключом). Обходит проблему publishable-ключа на клиенте."""
    user = await get_user(authorization)
    profile = await get_profile(user["id"])
    return {"id": user.get("id"), "email": user.get("email"),
            "name": (user.get("user_metadata") or {}).get("full_name"),
            "free_quota": int(profile.get("free_quota") or 0),
            "used_quota": int(profile.get("used_quota") or 0),
            "program_consent": bool(profile.get("program_consent")),
            "redeemed_code": profile.get("redeemed_code")}

@app.post("/api/upload-url")
async def upload_url(request: Request, authorization: str = Header(None)):
    user = await get_user(authorization)
    profile = await get_profile(user["id"])
    free_quota = int(profile.get("free_quota") or 0)
    used_quota = int(profile.get("used_quota") or 0)
    redeemed = profile.get("redeemed_code")
    if not (redeemed or free_quota > 0):
        raise HTTPException(403, "invite_required")
    if free_quota > 0 and used_quota >= free_quota:
        rows = await db("GET", "restorations",
                        params={"user_id": f"eq.{user['id']}",
                                "status": "neq.failed", "select": "id"})
        if len(rows or []) >= free_quota:
            raise HTTPException(402, "free_limit_reached")
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
    canvas.save(buf, format="PNG", optimize=True)
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
    # Friend path: invite unlock + profile quota (cabinet fields free_quota/used_quota/redeemed_code).
    # Invite path (free_quota > 0): honor invite free_quota/used_quota only — do not also hard-block at FREE_LIMIT.
    # FREE_LIMIT is fallback when redeemed but free_quota columns are missing/zero.
    profile = await get_profile(user["id"])
    free_quota = int(profile.get("free_quota") or 0)
    used_quota = int(profile.get("used_quota") or 0)
    redeemed = profile.get("redeemed_code")
    if not (redeemed or free_quota > 0):
        raise HTTPException(403, "invite_required")
    # P4 ship: used_quota alone no longer hard-blocks here — live non-failed rows below do.
    existing = await db("GET", "restorations",
                        params={"user_id": f"eq.{user['id']}", "status": "neq.failed", "select": "id"})
    used = len(existing or [])
    # Invite allowance wins over FREE_LIMIT; FREE_LIMIT only for non-invite/stale-quota fallback.
    hard_cap = free_quota if free_quota > 0 else FREE_LIMIT
    if used >= hard_cap:
        raise HTTPException(402, f"free_limit_reached:{hard_cap}")
    # lane/priority: default overnight (free). realtime accepted but not billed yet.
    # DB column `lane` may be absent — try persist, soft-fallback without it (BLOCKED until migration).
    raw_lane = (body.get("lane") or body.get("priority") or "overnight")
    if isinstance(raw_lane, (int, float)):
        lane = "realtime" if int(raw_lane) > 0 else "overnight"
    else:
        lane = str(raw_lane).strip().lower()
        if lane in ("paid", "priority", "fast"):
            lane = "realtime"
        elif lane in ("free", "night", "batch", "0"):
            lane = "overnight"
    if lane not in VALID_LANES:
        raise HTTPException(400, "bad lane (overnight|realtime)")
    row = {"user_id": user["id"], "original_key": original_key, "mode": mode, "status": "queued"}
    try:
        res = await db("POST", "restorations", payload={**row, "lane": lane})
        out = res[0] if isinstance(res, list) else res
        if isinstance(out, dict):
            out.setdefault("lane", lane)
        try:
            await db("PATCH", "profiles", params={"id": f"eq.{user['id']}"},
                     payload={"used_quota": used_quota + 1})
        except Exception:
            pass
        return out
    except HTTPException as e:
        detail = (e.detail if isinstance(e.detail, str) else str(e.detail)).lower()
        # Unknown column / schema cache → create without lane (safe for live DB).
        if e.status_code in (400, 404, 42703) or "lane" in detail or "column" in detail or "schema" in detail:
            res = await db("POST", "restorations", payload=row)
            out = res[0] if isinstance(res, list) else res
            if isinstance(out, dict):
                out["lane"] = lane
                out["lane_persisted"] = False
            try:
                await db("PATCH", "profiles", params={"id": f"eq.{user['id']}"},
                         payload={"used_quota": used_quota + 1})
            except Exception:
                pass
            return out
        raise

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
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

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
    <meta property='og:title' content='SaveMyHistory'><meta property='og:description' content='We brought a family photo back to life.'><meta property='og:image' content='{image_url}'><meta property='og:image:alt' content='Before and after family photo restoration'><meta property='og:type' content='article'><meta property='og:url' content='{PUBLIC_URL}/api/restorations/{rid}/share-card?sig={_short_sig(rid)}'><meta property='og:image:width' content='1200'><meta property='og:image:height' content='628'><meta name='twitter:card' content='summary_large_image'><meta name='twitter:title' content='SaveMyHistory'><meta name='twitter:description' content='We brought a family photo back to life.'><meta name='twitter:image' content='{image_url}'>
    <title>SaveMyHistory</title><style>body{{margin:0;background:#f4eee4;font-family:system-ui,sans-serif;color:#402a1b;display:grid;place-items:center;min-height:100vh}}.card{{width:min(1080px,92vw);background:#f5efe6;border:1px solid rgba(160,120,60,.25);border-radius:28px;box-shadow:0 20px 60px rgba(0,0,0,.10);overflow:hidden}}.pad{{padding:28px}}.top{{font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#a67c32;font-size:12px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.shot{{position:relative;border-radius:22px;overflow:hidden;background:#efe6d7;min-height:240px}}.shot img{{display:block;width:100%;height:100%;object-fit:cover}}.tag{{position:absolute;top:16px;left:16px;background:#000;color:#fff;padding:8px 12px;border-radius:999px;font-size:12px}}.tag.r{{background:#a67c32}}.copy{{padding:18px 0 0;font-size:28px;line-height:1.15;font-family:Georgia,serif}}.sub{{margin-top:8px;font-size:18px;color:#6f5c46}}.ft{{margin-top:18px;padding:16px 20px;background:#a67c32;color:#fff;font-weight:700;text-align:center;border-radius:18px}}.url{{margin-top:8px;font-size:12px;color:#8a7a66;text-align:center}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}.copy{{font-size:24px}}}}</style></head><body><div class='card'><div class='pad'><div class='top'>SAVE MY HISTORY · restored family memory</div><div class='grid'><div class='shot'><img src='{before or after}' alt='before restoration'><div class='tag'>BEFORE</div></div><div class='shot'><img src='{after}' alt='after restoration'><div class='tag r'>AFTER</div></div></div><div class='copy'>Old photo. New life.</div><div class='sub'>An old family photograph, gently brought back. Yours can be next.</div><div class='ft'>Restore a memory →</div><div class='url'>savemyhistory.tech · { _short_sig(rid) }</div></div></div></body></html>"""
    return HTMLResponse(html)

@app.post("/api/restorations/{rid}/share-card")
async def share_card_json(rid: str, authorization: str = Header(None)):
    """JSON для мобильного share / clipboard fallback."""
    user = await get_user(authorization)
    _png, _row = await _share_jpeg(rid, user_id=user["id"])
    return {"ok": True, "share_url": f"{PUBLIC_URL}/api/restorations/{rid}/share-card?sig={_short_sig(rid)}", "image_url": f"{PUBLIC_URL}/api/restorations/{rid}/share-card.png?sig={_short_sig(rid)}", "caption": "SaveMyHistory"}

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

# ============ ADMIN ============

@app.get("/api/admin/check")
async def admin_check(authorization: str = Header(None)):
    user = await require_admin(authorization)
    return {"ok": True, "email": user.get("email")}

# ---- Restorer board corpus (private; not under /public) ----
_RESTORER_ROOT = Path(__file__).resolve().parent / "restorer_corpus"
_RESTORER_CORPUS_DIR = _RESTORER_ROOT / "corpus"
_RESTORER_AFTER_DIR = _RESTORER_ROOT / "after"
_RESTORER_META = _RESTORER_ROOT / "CORPUS.json"
_RESTORER_MARKS = _RESTORER_ROOT / "marks.json"
_RESTORER_REGION_NOTES = _RESTORER_ROOT / "region_notes.json"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
_RESTORER_VERDICTS = {"", "approve", "approve_authentic", "approve_modern", "weak", "bad"}
_RESTORER_REGION_IMAGES = {"before", "auth", "mod", "after"}


def _restorer_load_meta():
    if not _RESTORER_META.is_file():
        raise HTTPException(503, "restorer corpus unavailable")
    try:
        data = json.loads(_RESTORER_META.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "restorer corpus meta corrupt")
    return data


def _restorer_photo_ids(meta=None):
    meta = meta or _restorer_load_meta()
    return {(p.get("id") or "").strip() for p in (meta.get("photos") or []) if (p.get("id") or "").strip()}


def _restorer_marks_has_content(m) -> bool:
    if not isinstance(m, dict):
        return False
    return bool(
        str(m.get("verdict") or "").strip()
        or str(m.get("comment") or "").strip()
        or str(m.get("defect_note") or "").strip()
    )


def _restorer_merge_marks_dicts(primary: dict, secondary: dict) -> dict:
    """Union two mark maps; prefer newer updated_at when both have content."""
    out = {}
    a = primary if isinstance(primary, dict) else {}
    b = secondary if isinstance(secondary, dict) else {}
    for pid in set(list(a.keys()) + list(b.keys())):
        ma, mb = a.get(pid), b.get(pid)
        a_ok = _restorer_marks_has_content(ma)
        b_ok = _restorer_marks_has_content(mb)
        if a_ok and b_ok:
            ta = int((ma or {}).get("updated_at") or 0)
            tb = int((mb or {}).get("updated_at") or 0)
            out[pid] = mb if tb > ta else ma
        elif a_ok:
            out[pid] = ma
        elif b_ok:
            out[pid] = mb
        elif isinstance(ma, dict):
            out[pid] = ma
        elif isinstance(mb, dict):
            out[pid] = mb
    return out


def _restorer_load_marks_local():
    if not _RESTORER_MARKS.is_file():
        return {}
    try:
        raw = json.loads(_RESTORER_MARKS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("marks"), dict):
        return raw["marks"]
    if isinstance(raw, dict):
        return raw
    return {}


def _restorer_load_marks():
    """Load marks from DO FS; if empty/missing, restore from Spaces dual-write."""
    local = _restorer_load_marks_local()
    spaces = {}
    try:
        if SPACES_KEY and SPACES_SECRET:
            spaces = _fb_load_marks_spaces(s3(), SPACES_BUCKET) or {}
    except Exception:
        spaces = {}
    if not isinstance(spaces, dict):
        spaces = {}
    local_n = sum(1 for m in local.values() if _restorer_marks_has_content(m))
    spaces_n = sum(1 for m in spaces.values() if _restorer_marks_has_content(m))
    if spaces_n and (not local_n or spaces_n > local_n):
        merged = _restorer_merge_marks_dicts(local, spaces)
        try:
            _restorer_save_marks_local(merged)
        except Exception:
            pass
        return merged
    if local_n and spaces_n:
        return _restorer_merge_marks_dicts(local, spaces)
    return local if local else spaces


def _restorer_save_marks_local(marks: dict, email: str = ""):
    payload = {
        "version": "restorer_marks_v1",
        "updated_at": int(time.time()),
        "updated_by": (email or "").strip().lower(),
        "marks": marks,
    }
    _RESTORER_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _RESTORER_MARKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    tmp.replace(_RESTORER_MARKS)
    return payload


def _restorer_save_marks(marks: dict, email: str = ""):
    """Persist marks to DO FS + best-effort Spaces (survives redeploy)."""
    payload = _restorer_save_marks_local(marks, email=email)
    try:
        if SPACES_KEY and SPACES_SECRET:
            _fb_save_marks_spaces(s3(), SPACES_BUCKET, marks, email=email)
    except Exception:
        pass
    return payload




def _restorer_git_tip() -> str:
    """Best-effort short tip for journal/ping (never raises)."""
    try:
        tip = (Path(__file__).resolve().parent.parent / '.git' / 'HEAD')
        # prefer env / deployed commit if present
        for k in ('GIT_COMMIT', 'COMMIT_SHA', 'DIGITALOCEAN_GIT_COMMIT_SHA'):
            v = (os.environ.get(k) or '').strip()
            if v:
                return v[:12]
        # fallback: read from a stamped file if any
        for cand in (
            Path(__file__).resolve().parent / 'GIT_TIP',
            Path(__file__).resolve().parent.parent / 'GIT_TIP',
        ):
            if cand.is_file():
                return cand.read_text(encoding='utf-8').strip()[:12]
    except Exception:
        pass
    return ''

def _restorer_feedback_emit(*, photo_id: str, verdict: str = "", comment: str = "",
                            defect_note: str = "", regions=None, email: str = "",
                            source: str = "review", kind: str = "",
                            action: str = "", note_id: str = "", image: str = "",
                            severity: str = "", tip: str = ""):
    """Best-effort Spaces journal + pending_ping queue. Never raises."""
    try:
        if not SPACES_KEY or not SPACES_SECRET:
            return {"ok": False, "reason": "no_spaces_env"}
        regs = regions if isinstance(regions, list) else []
        bbox = None
        img = (image or "").strip()
        if regs:
            r0 = regs[0] if isinstance(regs[0], dict) else {}
            try:
                bbox = {
                    "x": float(r0.get("x", 0)),
                    "y": float(r0.get("y", 0)),
                    "w": float(r0.get("w", 0)),
                    "h": float(r0.get("h", 0)),
                }
            except Exception:
                bbox = None
            if not img:
                img = str(r0.get("side") or r0.get("image") or "").strip()
        k = (kind or "").strip()
        if not k:
            k = "region_note" if (bbox or note_id or action in ("create", "update", "delete", "replace")) else "mark"
        ev = _fb_build_event(
            kind=k,
            photo_id=photo_id,
            source=source,
            verdict=verdict,
            comment=comment,
            email=email,
            tip=tip,
            bbox=bbox,
            image=img,
            note_id=note_id,
            action=action,
            defect_note=defect_note,
            severity=severity,
            extra={"regions": regs, "region_count": len(regs)} if regs else None,
        )
        return _fb_publish(s3(), SPACES_BUCKET, ev)
    except Exception as e:
        return {"ok": False, "reason": "exception", "error": str(e)[:300]}

def _restorer_ctype(name: str):
    low = name.lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


@app.get("/api/admin/restorer/corpus")
async def admin_restorer_corpus(authorization: str = Header(None)):
    """List restorer-board corpus metadata (admin only). Photos via /photo/{file}."""
    await require_admin(authorization)
    data = _restorer_load_meta()
    marks = _restorer_load_marks()
    photos = []
    for p in (data.get("photos") or []):
        fname = (p.get("file") or "").strip()
        if not fname or not _SAFE_NAME.match(fname):
            continue
        item = {k: p.get(k) for k in ("id", "file", "damage_tags", "status", "notes", "bytes", "md5", "after_file", "after_authentic_file", "after_modern_file", "qa_status", "wave") if k in p}
        item["photo_url"] = f"/api/admin/restorer/photo/{fname}"
        after_name = (p.get("after_file") or "").strip()
        auth_name = (p.get("after_authentic_file") or "").strip()
        mod_name = (p.get("after_modern_file") or "").strip()
        has_after = bool(after_name and _SAFE_NAME.match(after_name) and (_RESTORER_AFTER_DIR / after_name).is_file())
        has_auth = bool(auth_name and _SAFE_NAME.match(auth_name) and (_RESTORER_AFTER_DIR / auth_name).is_file())
        has_mod = bool(mod_name and _SAFE_NAME.match(mod_name) and (_RESTORER_AFTER_DIR / mod_name).is_file())
        item["has_after"] = has_after or has_auth or has_mod
        item["has_after_authentic"] = has_auth
        item["has_after_modern"] = has_mod
        if has_after:
            item["after_url"] = f"/api/admin/restorer/photo/{after_name}?kind=after"
        if has_auth:
            item["after_authentic_url"] = f"/api/admin/restorer/photo/{auth_name}?kind=after"
        if has_mod:
            item["after_modern_url"] = f"/api/admin/restorer/photo/{mod_name}?kind=after"
        mid = item.get("id") or ""
        if mid in marks:
            item["mark"] = marks[mid]
        photos.append(item)
    return {
        "ok": True,
        "version": data.get("version") or "restorer_board_v2",
        "n_photos": len(photos),
        "excluded": data.get("excluded") or [],
        "has_etalon": data.get("has_etalon") or [],
        "need_etalon": data.get("need_etalon") or [],
        "marks": marks,
        "warehouse": {
            "path_a": "HIT",
            "brushnet": "HIT",
            "flux_sdxl_qwen": "MISS",
            "spaces": "smh-photos",
            "region": "fra1",
            "n_instances": 0,
            "note": "учёт склада; GPU не арендуем из панели",
        },
        "cogs": {
            "path_a_mid_usd": 0.002,
            "path_a_classic_mid_usd": 0.0018,
            "default_gpu_usd_per_hour": 0.40,
            "default_photos_per_hour": 200,
        },
        "photos": photos,
    }


@app.get("/api/admin/restorer/photo/{filename}")
async def admin_restorer_photo(filename: str, kind: str = "before", authorization: str = Header(None)):
    """Serve one corpus BEFORE/AFTER image only to ADMIN_EMAILS."""
    await require_admin(authorization)
    name = (filename or "").strip()
    if not _SAFE_NAME.match(name) or "/" in name or ".." in name:
        raise HTTPException(400, "bad filename")
    kind = (kind or "before").strip().lower()
    if kind not in ("before", "after"):
        raise HTTPException(400, "bad kind")
    meta = _restorer_load_meta()
    if kind == "after":
        allowed = set()
        for p in (meta.get("photos") or []):
            for k in ("after_file", "after_authentic_file", "after_modern_file", "upscale_file"):
                n = (p.get(k) or "").strip()
                if n:
                    allowed.add(n)
        base = _RESTORER_AFTER_DIR
    else:
        allowed = {(p.get("file") or "").strip() for p in (meta.get("photos") or [])}
        base = _RESTORER_CORPUS_DIR
    if name not in allowed:
        raise HTTPException(404, "not found")
    path = (base / name).resolve()
    root = base.resolve()
    if not str(path).startswith(str(root) + os.sep) or not path.is_file():
        raise HTTPException(404, "not found")
    return Response(content=path.read_bytes(), media_type=_restorer_ctype(name), headers={
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/admin/restorer/marks")
async def admin_restorer_marks_get(authorization: str = Header(None)):
    """Load etalon decisions (approve/weak/bad + comment). Admin only."""
    await require_admin(authorization)
    marks = _restorer_load_marks()
    return {"ok": True, "version": "restorer_marks_v1", "marks": marks}


@app.put("/api/admin/restorer/marks")
async def admin_restorer_marks_put(request: Request, authorization: str = Header(None)):
    """Persist etalon decisions under private restorer_corpus/marks.json."""
    user = await require_admin(authorization)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    incoming = body.get("marks") if isinstance(body, dict) and "marks" in body else body
    if not isinstance(incoming, dict):
        raise HTTPException(400, "marks object required")
    allowed_ids = _restorer_photo_ids()
    cleaned = {}
    for pid, raw in incoming.items():
        pid = str(pid or "").strip()
        if pid not in allowed_ids:
            continue
        if not isinstance(raw, dict):
            continue
        verdict = str(raw.get("verdict") or "").strip()
        if verdict not in _RESTORER_VERDICTS:
            raise HTTPException(400, f"bad verdict for {pid}")
        comment = str(raw.get("comment") or "")[:2000]
        defect_note = str(raw.get("defect_note") or "")[:2000]
        cleaned[pid] = {
            "verdict": verdict,
            "comment": comment,
            "defect_note": defect_note,
            "updated_at": int(raw.get("updated_at") or time.time()),
        }
    # merge: allow partial updates when body.patch=true
    if isinstance(body, dict) and body.get("patch"):
        merged = _restorer_load_marks()
        merged.update(cleaned)
        cleaned = merged
    email = (user.get("email") or "").strip().lower()
    payload = _restorer_save_marks(cleaned, email=email)
    # journal each mark in this write (UI usually uses mark_one; bulk still dual-writes)
    fb_list = []
    emit_ids = list(cleaned.keys())
    if isinstance(body, dict) and body.get("patch"):
        # cleaned already merged above; emit only incoming keys if present
        incoming = body.get("marks") if "marks" in body else body
        if isinstance(incoming, dict):
            emit_ids = [str(k).strip() for k in incoming.keys() if str(k).strip() in cleaned]
    for pid in emit_ids:
        m = cleaned.get(pid) or {}
        fb_list.append(_restorer_feedback_emit(
            photo_id=pid,
            verdict=m.get("verdict") or "",
            comment=m.get("comment") or "",
            defect_note=m.get("defect_note") or "",
            regions=[],
            email=email,
            source="restorer",
        ))
    return {"ok": True, "version": payload["version"], "updated_at": payload["updated_at"], "marks": payload["marks"], "feedback": fb_list}


@app.put("/api/admin/restorer/marks/{photo_id}")
async def admin_restorer_mark_one(photo_id: str, request: Request, authorization: str = Header(None)):
    """Upsert one photo mark (verdict/comment/defect_note)."""
    user = await require_admin(authorization)
    pid = (photo_id or "").strip()
    if pid not in _restorer_photo_ids():
        raise HTTPException(404, "unknown photo")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object required")
    verdict = str(body.get("verdict") or "").strip()
    if verdict not in _RESTORER_VERDICTS:
        raise HTTPException(400, "bad verdict")
    marks = _restorer_load_marks()
    marks[pid] = {
        "verdict": verdict,
        "comment": str(body.get("comment") or "")[:2000],
        "defect_note": str(body.get("defect_note") or "")[:2000],
        "updated_at": int(time.time()),
    }
    email = (user.get("email") or "").strip().lower()
    payload = _restorer_save_marks(marks, email=email)
    fb = _restorer_feedback_emit(
        photo_id=pid,
        verdict=verdict,
        comment=marks[pid].get("comment") or "",
        defect_note=marks[pid].get("defect_note") or "",
        regions=[],
        email=email,
        source="restorer",
        kind="mark",
        action="upsert",
    )
    return {"ok": True, "id": pid, "mark": marks[pid], "updated_at": payload["updated_at"], "feedback": fb}


def _restorer_clamp01(v):
    try:
        x = float(v)
    except Exception:
        return None
    if x != x:  # NaN
        return None
    return max(0.0, min(1.0, x))


def _restorer_norm_bbox(raw):
    if not isinstance(raw, dict):
        return None
    x = _restorer_clamp01(raw.get("x"))
    y = _restorer_clamp01(raw.get("y"))
    w = _restorer_clamp01(raw.get("w"))
    h = _restorer_clamp01(raw.get("h"))
    if None in (x, y, w, h):
        return None
    # keep inside image bounds
    if w <= 0 or h <= 0:
        return None
    if x + w > 1.0:
        w = 1.0 - x
    if y + h > 1.0:
        h = 1.0 - y
    if w <= 0 or h <= 0:
        return None
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}



# ---- GPU bake-off review (private admin; Path C BrushNet) ----
_GPU_BAKEOFF_REVIEW = _RESTORER_ROOT / "gpu_bakeoff_review.json"
_GPU_BAKEOFF_MARKS = _RESTORER_ROOT / "gpu_bakeoff_marks.json"
_GPU_BAKEOFF_BEFORE = _RESTORER_ROOT / "gpu_bakeoff" / "before"
_GPU_BAKEOFF_AFTER = _RESTORER_ROOT / "gpu_bakeoff" / "after"
_GPU_BAKEOFF_NANO_BAR = _RESTORER_ROOT / "gpu_bakeoff" / "nano_bar"
_GPU_BAKEOFF_LOG = _RESTORER_ROOT / "gpu_bakeoff" / "run_log.txt"
_GPU_BAKEOFF_VERDICTS = {"", "pass", "weak", "fail", "PASS", "WEAK", "FAIL", "approve", "bad"}


def _gpu_bakeoff_load_review():
    if not _GPU_BAKEOFF_REVIEW.is_file():
        raise HTTPException(503, "gpu bakeoff review unavailable")
    try:
        data = json.loads(_GPU_BAKEOFF_REVIEW.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "gpu bakeoff review corrupt")
    if not isinstance(data, dict):
        raise HTTPException(500, "gpu bakeoff review corrupt")
    return data


def _gpu_bakeoff_photo_ids():
    data = _gpu_bakeoff_load_review()
    return {str(c.get("id") or "").strip() for c in (data.get("cards") or []) if str(c.get("id") or "").strip()}


def _gpu_bakeoff_load_marks():
    if not _GPU_BAKEOFF_MARKS.is_file():
        return {}
    try:
        raw = json.loads(_GPU_BAKEOFF_MARKS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("marks"), dict):
        return raw["marks"]
    if isinstance(raw, dict):
        return raw
    return {}


def _gpu_bakeoff_save_marks(marks: dict, email: str = ""):
    payload = {
        "version": "gpu_bakeoff_marks_v1",
        "updated_at": int(time.time()),
        "updated_by": email or "",
        "marks": marks,
    }
    _GPU_BAKEOFF_MARKS.parent.mkdir(parents=True, exist_ok=True)
    _GPU_BAKEOFF_MARKS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


@app.get("/api/admin/gpu-bakeoff/review")
async def admin_gpu_bakeoff_review(authorization: str = Header(None)):
    """Path C / GPU bake-off review board metadata (admin only)."""
    await require_admin(authorization)
    data = _gpu_bakeoff_load_review()
    marks = _gpu_bakeoff_load_marks()
    cards_out = []
    for c in (data.get("cards") or []):
        if not isinstance(c, dict):
            continue
        pid = str(c.get("id") or "").strip()
        before = str(c.get("before_file") or "").strip()
        after = str(c.get("after_file") or "").strip()
        nano = str(c.get("nano_bar_file") or "").strip()
        after_status = str(c.get("after_status") or "WAITING").strip().upper()
        nano_status = str(c.get("nano_bar_status") or "").strip().upper()
        has_before = bool(before and _SAFE_NAME.match(before) and (_GPU_BAKEOFF_BEFORE / before).is_file())
        has_after = bool(after and _SAFE_NAME.match(after) and (_GPU_BAKEOFF_AFTER / after).is_file())
        has_nano = bool(
            nano
            and _SAFE_NAME.match(nano)
            and (_GPU_BAKEOFF_NANO_BAR / nano).is_file()
            and nano_status == "PASS"
        )
        if has_after:
            after_status = "FILLED"
        elif after_status not in ("WAITING", "NO_AFTER", "FILLED"):
            after_status = "WAITING"
        item = dict(c)
        item["has_before"] = has_before
        item["has_after"] = has_after
        item["has_nano_bar"] = has_nano
        item["after_status"] = after_status
        item["before_url"] = f"/api/admin/gpu-bakeoff/photo/{before}?kind=before" if has_before else ""
        item["after_url"] = f"/api/admin/gpu-bakeoff/photo/{after}?kind=after" if has_after else ""
        item["nano_bar_url"] = f"/api/admin/gpu-bakeoff/photo/{nano}?kind=nano_bar" if has_nano else ""
        if pid in marks:
            item["mark"] = marks[pid]
        cards_out.append(item)
    run = dict(data.get("run") or {})
    # Prefer embedded excerpt; fall back to on-disk sanitized run_log.txt
    if not str(run.get("logs_excerpt") or "").strip() and _GPU_BAKEOFF_LOG.is_file():
        try:
            run["logs_excerpt"] = _GPU_BAKEOFF_LOG.read_text(encoding="utf-8", errors="replace")[:12000]
            run.setdefault("logs_file", "run_log.txt")
        except Exception:
            pass
    return {
        "ok": True,
        "version": data.get("version") or "gpu_bakeoff_review_v1",
        "status": data.get("status") or "WAITING",
        "updated_at_utc": data.get("updated_at_utc") or "",
        "run": run,
        "cards": cards_out,
        "marks": marks,
        "n_cards": len(cards_out),
    }


@app.get("/api/admin/gpu-bakeoff/log")
async def admin_gpu_bakeoff_log(authorization: str = Header(None)):
    """Sanitized bake-off run log excerpt (admin only)."""
    await require_admin(authorization)
    data = _gpu_bakeoff_load_review()
    run = data.get("run") or {}
    excerpt = str(run.get("logs_excerpt") or "").strip()
    if not excerpt and _GPU_BAKEOFF_LOG.is_file():
        try:
            excerpt = _GPU_BAKEOFF_LOG.read_text(encoding="utf-8", errors="replace")
        except Exception:
            excerpt = ""
    if not excerpt:
        raise HTTPException(404, "log unavailable")
    # hard cap + light sanitize (no secrets patterns)
    out = excerpt[:20000]
    for bad in ("Authorization:", "Bearer ", "api_key", "API_KEY", "VAST_API", "OPENAI_API", "sk-"):
        if bad in out:
            out = out.replace(bad, "[redacted]")
    return Response(content=out, media_type="text/plain; charset=utf-8", headers={
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/admin/gpu-bakeoff/photo/{filename}")
async def admin_gpu_bakeoff_photo(filename: str, kind: str = "before", authorization: str = Header(None)):
    """Serve bake-off BEFORE/AFTER only to ADMIN_EMAILS."""
    await require_admin(authorization)
    name = (filename or "").strip()
    if not _SAFE_NAME.match(name) or "/" in name or ".." in name:
        raise HTTPException(400, "bad filename")
    kind = (kind or "before").strip().lower()
    if kind not in ("before", "after", "nano_bar"):
        raise HTTPException(400, "bad kind")
    data = _gpu_bakeoff_load_review()
    if kind == "after":
        allowed = {(c.get("after_file") or "").strip() for c in (data.get("cards") or [])}
        base = _GPU_BAKEOFF_AFTER
    elif kind == "nano_bar":
        allowed = {
            (c.get("nano_bar_file") or "").strip()
            for c in (data.get("cards") or [])
            if str(c.get("nano_bar_status") or "").strip().upper() == "PASS"
        }
        base = _GPU_BAKEOFF_NANO_BAR
    else:
        allowed = {(c.get("before_file") or "").strip() for c in (data.get("cards") or [])}
        base = _GPU_BAKEOFF_BEFORE
    if name not in allowed:
        raise HTTPException(404, "not found")
    path = (base / name).resolve()
    root = base.resolve()
    if not str(path).startswith(str(root) + os.sep) or not path.is_file():
        raise HTTPException(404, "not found")
    return Response(content=path.read_bytes(), media_type=_restorer_ctype(name), headers={
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/admin/gpu-bakeoff/marks")
async def admin_gpu_bakeoff_marks_get(authorization: str = Header(None)):
    await require_admin(authorization)
    marks = _gpu_bakeoff_load_marks()
    return {"ok": True, "version": "gpu_bakeoff_marks_v1", "marks": marks}


@app.put("/api/admin/gpu-bakeoff/marks/{photo_id}")
async def admin_gpu_bakeoff_mark_one(photo_id: str, request: Request, authorization: str = Header(None)):
    """Upsert PASS/WEAK/FAIL + comment for one bake-off card."""
    user = await require_admin(authorization)
    pid = (photo_id or "").strip()
    if pid not in _gpu_bakeoff_photo_ids():
        raise HTTPException(404, "unknown photo")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object required")
    verdict = str(body.get("verdict") or "").strip()
    # normalize UI labels
    vmap = {"approve": "pass", "bad": "fail", "PASS": "pass", "WEAK": "weak", "FAIL": "fail"}
    verdict = vmap.get(verdict, verdict).lower()
    if verdict not in ("", "pass", "weak", "fail"):
        raise HTTPException(400, "bad verdict")
    marks = _gpu_bakeoff_load_marks()
    marks[pid] = {
        "verdict": verdict,
        "comment": str(body.get("comment") or "")[:4000],
        "updated_at": int(time.time()),
    }
    email = (user.get("email") or "").strip().lower()

    payload = _gpu_bakeoff_save_marks(marks, email=email)
    return {"ok": True, "id": pid, "mark": marks[pid], "updated_at": payload["updated_at"]}


# ---- API service review panel (fal Nano Banana path; no GPU rent) ----
_SERVICE_MARKS = _RESTORER_ROOT / "service_marks.json"
_SERVICE_VERDICTS = {"", "pass", "weak", "fail"}
_HARD_BAN_INVENT_IDS = {"pd_05", "pd_05_invent"}


def _service_is_fail_hold_case(case: dict) -> bool:
    """Match service-review isFailHoldCase: FAIL_HOLD / FAIL_HOLD_SIDE / demoted FAIL mod."""
    if not isinstance(case, dict):
        return False
    pe = str(case.get("parent_eye") or "")
    qa = str(case.get("qa_status") or "")
    blob = (pe + " " + qa).upper()
    if "FAIL_HOLD" in blob:
        return True
    if "FAIL_MOD_DEMOTED" in blob:
        return True
    pe_qa = pe + " " + qa
    if re.search(r"MODERN\s+FAIL", pe, flags=re.I) and re.search(r"demoted", pe_qa, flags=re.I):
        return True
    return False


def _service_parent_eye(p: dict) -> str:
    """Latest non-empty parent-eye / Roman mark string from corpus fields."""
    if not isinstance(p, dict):
        return ""
    # prefer human parent_eye note, then roman / qa, then newest wave*_parent_eye
    for k in ("parent_eye", "roman_notes51_eye", "roman_note"):
        v = p.get(k)
        if isinstance(v, dict):
            bits = [str(v.get("verdict") or "").strip(), str(v.get("reason") or "").strip()]
            s = " ".join(x for x in bits if x)
            if s:
                return s[:500]
        elif v:
            s = str(v).strip()
            if s:
                return s[:500]
    parent_keys = sorted(
        (k for k in p.keys() if str(k).endswith("_parent_eye") and p.get(k)),
        reverse=True,
    )
    for k in parent_keys:
        s = str(p.get(k) or "").strip()
        if s:
            return f"{k}: {s}"[:500]
    for k in ("qa_status", "qa_note"):
        v = p.get(k)
        if v:
            s = str(v).strip()
            if s:
                return s[:500]
    return ""


def _service_load_marks():
    if not _SERVICE_MARKS.is_file():
        return {}
    try:
        raw = json.loads(_SERVICE_MARKS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("marks"), dict):
        return raw["marks"]
    return {}


def _service_save_marks(marks: dict, email: str = ""):
    payload = {
        "version": "service_marks_v1",
        "updated_at": int(time.time()),
        "updated_by": (email or "").strip().lower(),
        "marks": marks,
    }
    _RESTORER_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _SERVICE_MARKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    tmp.replace(_SERVICE_MARKS)
    return payload


def _service_case_ids():
    ids = set(_restorer_photo_ids())
    try:
        data = _gpu_bakeoff_load_review()
        for c in (data.get("cards") or []):
            if not isinstance(c, dict):
                continue
            pid = str(c.get("id") or "").strip()
            nano = str(c.get("nano_bar_file") or "").strip()
            nano_status = str(c.get("nano_bar_status") or "").strip().upper()
            if pid and nano and nano_status == "PASS" and (_GPU_BAKEOFF_NANO_BAR / nano).is_file():
                ids.add(f"api_nano__{pid}")
    except Exception:
        pass
    return ids



def _service_infer_upscale_method(p: dict) -> str:
    """CORPUS upscale_method, else infer from notes/status. Never invent spend."""
    m = str(p.get("upscale_method") or "").strip()
    if m:
        return m[:80]
    blob = " ".join(
        str(p.get(k) or "")
        for k in ("upscale_note", "notes", "parent_eye", "qa_note")
    ).lower()
    st = str(p.get("upscale_status") or "").strip().upper()
    if "fal-ai/esrgan" in blob or "fal_ai/esrgan" in blob or "esrgan" in blob and "fal" in blob:
        return "fal-ai/esrgan"
    if "pil_lanczos" in blob or "lanczos" in blob:
        return "PIL_LANCZOS_x2_FREE"
    if st == "SKIP":
        return "SKIP"
    if st == "DONE" or p.get("upscale_file"):
        return "PIL_LANCZOS_x2_FREE"
    return ""


def _service_build_cases():
    """Seed case DB from existing AFTER only — never invent. pd_05 invent hard-banned."""
    meta = _restorer_load_meta()
    marks = _service_load_marks()
    # also surface restorer marks as prior feedback (parent/roman board)
    restorer_marks = _restorer_load_marks()
    cases = []
    after_root = _RESTORER_ROOT / "after"
    for p in (meta.get("photos") or []):
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        fname = str(p.get("file") or "").strip()
        after_name = str(p.get("after_file") or "").strip()
        auth_name = str(p.get("after_authentic_file") or "").strip()
        mod_name = str(p.get("after_modern_file") or "").strip()
        has_after = bool(after_name and _SAFE_NAME.match(after_name) and (after_root / after_name).is_file())
        has_auth = bool(auth_name and _SAFE_NAME.match(auth_name) and (after_root / auth_name).is_file())
        has_mod = bool(mod_name and _SAFE_NAME.match(mod_name) and (after_root / mod_name).is_file())
        upscale_name = str(p.get("upscale_file") or "").strip()
        has_upscale = False
        if upscale_name and _SAFE_NAME.match(upscale_name):
            if (after_root / upscale_name).is_file():
                has_upscale = True
            elif (after_root / "upscale" / upscale_name).is_file():
                has_upscale = True
            else:
                upscale_name = ""
        else:
            upscale_name = ""
        # HARD BAN: never invent AFTER for pd_05*
        if pid in _HARD_BAN_INVENT_IDS or pid.startswith("pd_05"):
            if not (has_after or has_auth or has_mod):
                has_after = has_auth = has_mod = False
            has_upscale = False
            upscale_name = ""
        if has_after or has_auth or has_mod:
            after_status = "READY"
        else:
            after_status = "WAITING"
        parent_eye = _service_parent_eye(p)
        mark = marks.get(pid) or restorer_marks.get(pid) or {}
        # normalize restorer approve/bad → pass/fail for service UI
        if mark and str(mark.get("verdict") or "") in ("approve", "approve_authentic", "approve_modern"):
            mark = dict(mark)
            mark["verdict"] = "pass"
            mark["verdict_source"] = "restorer_board"
        elif mark and str(mark.get("verdict") or "") == "bad":
            mark = dict(mark)
            mark["verdict"] = "fail"
            mark["verdict_source"] = "restorer_board"
        corpus_up = str(p.get("upscale_status") or "").strip().upper()
        mark_up = str((mark or {}).get("upscale_status") or "").strip().upper()
        upscale_method = _service_infer_upscale_method(p)
        if has_upscale:
            upscale_status = "DONE"
            upscale_url = f"/api/admin/restorer/photo/{upscale_name}?kind=after"
        elif corpus_up == "SKIP" or mark_up == "SKIP":
            upscale_status = "SKIP"
            upscale_url = ""
            if not upscale_method:
                upscale_method = "SKIP"
        else:
            if (has_after or has_auth or has_mod) and corpus_up in ("", "WAITING", "PLANNED"):
                upscale_status = "WAITING" if corpus_up in ("", "WAITING") else corpus_up
            else:
                upscale_status = (
                    corpus_up
                    if corpus_up in ("PLANNED", "WAITING", "DONE", "SKIP")
                    else (mark_up if mark_up in ("PLANNED", "WAITING", "DONE", "SKIP") else "PLANNED")
                )
            upscale_url = ""
        case = {
            "id": pid,
            "title": pid.replace("_", " "),
            "lane": "corpus",
            "pipeline": "inspect → CodeFormer(if soft faces) → Nano Banana(fal) face-lock",
            "provider": "api_fal_nano",
            "before_file": fname,
            "before_url": f"/api/admin/restorer/photo/{fname}" if fname else "",
            "after_file": after_name if has_after else "",
            "after_url": f"/api/admin/restorer/photo/{after_name}?kind=after" if has_after else "",
            "after_authentic_file": auth_name if has_auth else "",
            "after_authentic_url": f"/api/admin/restorer/photo/{auth_name}?kind=after" if has_auth else "",
            "after_modern_file": mod_name if has_mod else "",
            "after_modern_url": f"/api/admin/restorer/photo/{mod_name}?kind=after" if has_mod else "",
            "has_after": bool(has_after or has_auth or has_mod),
            "after_status": after_status,
            "upscale_file": upscale_name if has_upscale else "",
            "upscale_status": upscale_status,
            "upscale_url": f"/api/admin/restorer/photo/{upscale_name}?kind=after" if has_upscale else "",
            "upscale_method": upscale_method,
            "damage_tags": p.get("damage_tags") or [],
            "status": p.get("status") or "",
            "qa_status": p.get("qa_status") or "",
            "parent_eye": parent_eye,
            "notes": (p.get("notes") or "")[:800],
            "color_policy": "bw_source→authentic_bw+color_variant; color_source→authentic_only",
            "mark": mark,
        }
        cases.append(case)

    # Seed known PASS Nano authentic core4 (API path) — existing AFTER only
    try:
        gb = _gpu_bakeoff_load_review()
        for c in (gb.get("cards") or []):
            if not isinstance(c, dict):
                continue
            pid = str(c.get("id") or "").strip()
            before = str(c.get("before_file") or "").strip()
            nano = str(c.get("nano_bar_file") or "").strip()
            nano_status = str(c.get("nano_bar_status") or "").strip().upper()
            has_before = bool(before and (_GPU_BAKEOFF_BEFORE / before).is_file())
            has_nano = bool(
                nano
                and nano_status == "PASS"
                and (_GPU_BAKEOFF_NANO_BAR / nano).is_file()
            )
            if not pid or not has_nano:
                continue
            cid = f"api_nano__{pid}"
            mark = marks.get(cid) or {}
            cases.append({
                "id": cid,
                "title": f"{pid} · Nano API authentic",
                "lane": "api_nano",
                "pipeline": "fal Nano Banana · face/identity lock · no invent",
                "provider": "api_fal_nano",
                "stem_id": pid,
                "before_file": before,
                "before_url": f"/api/admin/gpu-bakeoff/photo/{before}?kind=before" if has_before else "",
                "after_file": nano,
                "after_url": f"/api/admin/gpu-bakeoff/photo/{nano}?kind=nano_bar",
                "after_authentic_file": nano,
                "after_authentic_url": f"/api/admin/gpu-bakeoff/photo/{nano}?kind=nano_bar",
                "after_modern_file": "",
                "after_modern_url": "",
                "has_after": True,
                "after_status": "READY",
                "upscale_status": "PLANNED",
                "upscale_url": "",
                "upscale_method": "",
                "damage_tags": ["nano_bar", "api", "core4"],
                "status": "HAS_ETALON",
                "qa_status": nano_status,
                "parent_eye": str(c.get("nano_bar_note") or c.get("q_verdict") or "")[:500],
                "notes": str(c.get("nano_bar_note") or c.get("notes") or "")[:800],
                "color_policy": "authentic restore; no invented recolor",
                "mark": mark,
            })
    except Exception:
        pass

    n_with_after = sum(1 for x in cases if x.get("has_after"))
    n_waiting = sum(1 for x in cases if x.get("after_status") == "WAITING")
    n_upscale_done = sum(1 for x in cases if x.get("upscale_status") == "DONE")
    n_upscale_skip = sum(1 for x in cases if x.get("upscale_status") == "SKIP")
    n_upscale_waiting = sum(
        1
        for x in cases
        if x.get("upscale_status") in ("WAITING", "PLANNED") and x.get("has_after")
    )
    fail_hold_ids = [str(x.get("id") or "") for x in cases if _service_is_fail_hold_case(x)]
    fail_hold_ids = [x for x in fail_hold_ids if x]
    upscale_stats = {
        "n_upscale_done": n_upscale_done,
        "n_upscale_skip": n_upscale_skip,
        "n_upscale_waiting": n_upscale_waiting,
        "n_upscale_esrgan": sum(1 for x in cases if str(x.get("upscale_method") or "") == "fal-ai/esrgan"),
        "n_upscale_pil": sum(1 for x in cases if str(x.get("upscale_method") or "") == "PIL_LANCZOS_x2_FREE"),
        "n_fail_hold": len(fail_hold_ids),
        "fail_hold_ids": fail_hold_ids,
    }
    return cases, marks, n_with_after, n_waiting, upscale_stats


@app.get("/api/admin/service-cases")
async def admin_service_cases(authorization: str = Header(None)):
    """Aggregated before/after case DB for API service review panel (admin)."""
    await require_admin(authorization)
    cases, marks, n_with_after, n_waiting, upscale_stats = _service_build_cases()
    return {
        "ok": True,
        "version": "service_cases_v2",
        "panel": "/service-review.html",
        "pipeline_canon": [
            "inspect photo",
            "CodeFormer ONLY if faces soft/unreadable",
            "Nano Banana (fal) restore with face/identity lock",
            "reject invent/plastic",
            "B&W → authentic B&W AFTER + separate color variant",
            "color → authentic restore (no invented recolor)",
            "upscale = DONE when CORPUS upscale_file exists under after/",
            "upscale_method = fal-ai/esrgan | PIL_LANCZOS_x2_FREE | SKIP (from CORPUS)",
            "parent eye / Roman marks overrule child PASS",
        ],
        "no_rent": True,
        "provider": "api_fal_nano",
        "n_cases": len(cases),
        "n_with_after": n_with_after,
        "n_waiting": n_waiting,
        "n_upscale_done": upscale_stats["n_upscale_done"],
        "n_upscale_skip": upscale_stats["n_upscale_skip"],
        "n_upscale_waiting": upscale_stats["n_upscale_waiting"],
        "n_upscale_esrgan": upscale_stats["n_upscale_esrgan"],
        "n_upscale_pil": upscale_stats["n_upscale_pil"],
        "n_fail_hold": upscale_stats["n_fail_hold"],
        "fail_hold_ids": upscale_stats.get("fail_hold_ids") or [],
        "n_marks": sum(1 for m in marks.values() if isinstance(m, dict) and str(m.get("verdict") or "").strip()),
        "git_tip": _restorer_git_tip(),
        "cases": cases,
        "marks": marks,
    }


@app.get("/api/admin/service-cases/marks")
async def admin_service_marks_get(authorization: str = Header(None)):
    await require_admin(authorization)
    marks = _service_load_marks()
    return {"ok": True, "version": "service_marks_v1", "marks": marks}


@app.put("/api/admin/service-cases/marks/{case_id}")
async def admin_service_mark_one(case_id: str, request: Request, authorization: str = Header(None)):
    """Persist PASS/WEAK/FAIL + comment for one service case."""
    user = await require_admin(authorization)
    cid = (case_id or "").strip()
    if cid not in _service_case_ids():
        raise HTTPException(404, "unknown case")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object required")
    verdict = str(body.get("verdict") or "").strip()
    vmap = {
        "approve": "pass", "bad": "fail",
        "PASS": "pass", "WEAK": "weak", "FAIL": "fail",
        "pass": "pass", "weak": "weak", "fail": "fail",
    }
    verdict = vmap.get(verdict, verdict).lower()
    if verdict not in _SERVICE_VERDICTS:
        raise HTTPException(400, "bad verdict")
    marks = _service_load_marks()
    marks[cid] = {
        "verdict": verdict,
        "comment": str(body.get("comment") or "")[:4000],
        "upscale_status": str(body.get("upscale_status") or "PLANNED")[:40],
        "updated_at": int(time.time()),
    }
    email = (user.get("email") or "").strip().lower()
    payload = _service_save_marks(marks, email=email)
    # journal for corpus-linked ids (not api_nano__*)
    fb = None
    if not cid.startswith("api_nano__"):
        try:
            fb = _restorer_feedback_emit(
                photo_id=cid,
                verdict=verdict,
                comment=marks[cid].get("comment") or "",
                defect_note="",
                regions=[],
                email=email,
                source="service_review",
                kind="mark",
                action="upsert",
            )
        except Exception:
            fb = None
    return {"ok": True, "id": cid, "mark": marks[cid], "updated_at": payload["updated_at"], "feedback": fb}


def _restorer_load_region_notes():
    if not _RESTORER_REGION_NOTES.is_file():
        return []
    try:
        raw = json.loads(_RESTORER_REGION_NOTES.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, dict) and isinstance(raw.get("notes"), list):
        return raw["notes"]
    if isinstance(raw, list):
        return raw
    return []


def _restorer_save_region_notes(notes: list, email: str = ""):
    payload = {
        "version": "restorer_region_notes_v1",
        "updated_at": int(time.time()),
        "updated_by": (email or "").strip().lower(),
        "notes": notes,
    }
    _RESTORER_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _RESTORER_REGION_NOTES.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    tmp.replace(_RESTORER_REGION_NOTES)
    return payload


def _restorer_clean_region_note(raw, allowed_ids, email="", note_id=None):
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("photo_id") or "").strip()
    if pid not in allowed_ids:
        return None
    image = str(raw.get("image") or "").strip().lower()
    if image not in _RESTORER_REGION_IMAGES:
        return None
    bbox = _restorer_norm_bbox(raw.get("bbox") or {})
    if not bbox:
        return None
    comment = str(raw.get("comment") or "").strip()[:4000]
    if not comment:
        return None
    nid = str(note_id or raw.get("id") or "").strip()
    if not nid or len(nid) > 80 or not re.match(r"^[A-Za-z0-9._-]{8,80}$", nid):
        nid = f"rn_{int(time.time()*1000)}_{secrets.token_hex(4)}"
    em = (email or str(raw.get("email") or "")).strip().lower()[:200]
    ts = int(raw.get("timestamp") or raw.get("updated_at") or time.time())
    return {
        "id": nid,
        "photo_id": pid,
        "image": image,
        "bbox": bbox,
        "comment": comment,
        "timestamp": ts,
        "email": em,
        "updated_at": int(time.time()),
    }


@app.get("/api/admin/restorer/region_notes")
async def admin_restorer_region_notes_get(authorization: str = Header(None), photo_id: str = None):
    """Load region annotation notes (admin only). Optional photo_id filter."""
    await require_admin(authorization)
    notes = _restorer_load_region_notes()
    pid = (photo_id or "").strip()
    if pid:
        notes = [n for n in notes if isinstance(n, dict) and n.get("photo_id") == pid]
    return {"ok": True, "version": "restorer_region_notes_v1", "notes": notes, "n": len(notes)}


@app.put("/api/admin/restorer/region_notes")
async def admin_restorer_region_notes_put(request: Request, authorization: str = Header(None)):
    """Replace or patch region notes under private restorer_corpus/region_notes.json."""
    user = await require_admin(authorization)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    incoming = body.get("notes") if isinstance(body, dict) and "notes" in body else body
    if not isinstance(incoming, list):
        raise HTTPException(400, "notes array required")
    allowed_ids = _restorer_photo_ids()
    email = (user.get("email") or "").strip().lower()
    cleaned = []
    for raw in incoming:
        note = _restorer_clean_region_note(raw, allowed_ids, email=email)
        if note:
            cleaned.append(note)
    if isinstance(body, dict) and body.get("patch"):
        by_id = {n.get("id"): n for n in _restorer_load_region_notes() if isinstance(n, dict) and n.get("id")}
        for n in cleaned:
            by_id[n["id"]] = n
        cleaned = list(by_id.values())
    payload = _restorer_save_region_notes(cleaned, email=email)
    fb_list = []
    for note in cleaned:
        fb_list.append(_restorer_feedback_emit(
            photo_id=note.get("photo_id") or "",
            verdict="",
            comment=note.get("comment") or "",
            defect_note="",
            regions=[{
                "x": (note.get("bbox") or {}).get("x"),
                "y": (note.get("bbox") or {}).get("y"),
                "w": (note.get("bbox") or {}).get("w"),
                "h": (note.get("bbox") or {}).get("h"),
                "side": note.get("image") or "before",
                "comment": note.get("comment") or "",
            }],
            email=email,
            source="review",
        ))
    return {"ok": True, "version": payload["version"], "updated_at": payload["updated_at"], "notes": payload["notes"], "n": len(payload["notes"]), "feedback": fb_list}


@app.put("/api/admin/restorer/region_notes/{photo_id}")
async def admin_restorer_region_notes_photo_put(photo_id: str, request: Request, authorization: str = Header(None)):
    """Replace all notes for one photo_id (keeps other photos)."""
    user = await require_admin(authorization)
    pid = (photo_id or "").strip()
    if pid not in _restorer_photo_ids():
        raise HTTPException(404, "unknown photo")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    incoming = body.get("notes") if isinstance(body, dict) and "notes" in body else body
    if not isinstance(incoming, list):
        raise HTTPException(400, "notes array required")
    allowed_ids = {pid}
    email = (user.get("email") or "").strip().lower()
    cleaned = []
    for raw in incoming:
        if isinstance(raw, dict):
            raw = dict(raw)
            raw["photo_id"] = pid
        note = _restorer_clean_region_note(raw, allowed_ids, email=email)
        if note:
            cleaned.append(note)
    existing = [n for n in _restorer_load_region_notes() if isinstance(n, dict) and n.get("photo_id") != pid]
    payload = _restorer_save_region_notes(existing + cleaned, email=email)
    photo_notes = [n for n in payload["notes"] if n.get("photo_id") == pid]
    fb_list = []
    for note in cleaned:
        fb_list.append(_restorer_feedback_emit(
            photo_id=pid,
            verdict="",
            comment=note.get("comment") or "",
            defect_note="",
            regions=[{
                "x": (note.get("bbox") or {}).get("x"),
                "y": (note.get("bbox") or {}).get("y"),
                "w": (note.get("bbox") or {}).get("w"),
                "h": (note.get("bbox") or {}).get("h"),
                "side": note.get("image") or "before",
                "comment": note.get("comment") or "",
            }],
            email=email,
            source="review",
        ))
    return {"ok": True, "id": pid, "notes": photo_notes, "updated_at": payload["updated_at"], "n": len(photo_notes), "feedback": fb_list}


@app.post("/api/admin/restorer/region_notes")
async def admin_restorer_region_notes_post(request: Request, authorization: str = Header(None)):
    """Append one region note."""
    user = await require_admin(authorization)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object required")
    email = (user.get("email") or "").strip().lower()
    note = _restorer_clean_region_note(body, _restorer_photo_ids(), email=email)
    if not note:
        raise HTTPException(400, "invalid note (photo_id/image/bbox/comment)")
    notes = _restorer_load_region_notes()
    notes.append(note)
    payload = _restorer_save_region_notes(notes, email=email)
    fb = _restorer_feedback_emit(
        photo_id=note.get("photo_id") or "",
        verdict=str((body or {}).get("verdict") or "").strip(),
        comment=note.get("comment") or "",
        defect_note="",
        regions=[{
            "x": (note.get("bbox") or {}).get("x"),
            "y": (note.get("bbox") or {}).get("y"),
            "w": (note.get("bbox") or {}).get("w"),
            "h": (note.get("bbox") or {}).get("h"),
            "side": note.get("image") or "before",
            "comment": note.get("comment") or "",
        }],
        email=email,
        source="review",
    )
    return {"ok": True, "note": note, "updated_at": payload["updated_at"], "feedback": fb}


@app.delete("/api/admin/restorer/region_notes/{note_id}")
async def admin_restorer_region_notes_delete(note_id: str, authorization: str = Header(None)):
    """Delete one region note by id (own notes only, unless same admin list)."""
    user = await require_admin(authorization)
    nid = (note_id or "").strip()
    if not nid:
        raise HTTPException(400, "note id required")
    email = (user.get("email") or "").strip().lower()
    notes = _restorer_load_region_notes()
    keep = []
    deleted = None
    for n in notes:
        if isinstance(n, dict) and n.get("id") == nid:
            # allow any admin to delete (small team); prefer own
            deleted = n
            continue
        keep.append(n)
    if deleted is None:
        raise HTTPException(404, "note not found")
    payload = _restorer_save_region_notes(keep, email=email)
    fb = _restorer_feedback_emit(
        photo_id=(deleted.get("photo_id") or ""),
        verdict="",
        comment=f"[deleted region note] {(deleted.get('comment') or '')[:500]}",
        defect_note="",
        regions=[{
            "x": (deleted.get("bbox") or {}).get("x"),
            "y": (deleted.get("bbox") or {}).get("y"),
            "w": (deleted.get("bbox") or {}).get("w"),
            "h": (deleted.get("bbox") or {}).get("h"),
            "side": deleted.get("image") or "before",
            "comment": deleted.get("comment") or "",
        }] if isinstance(deleted.get("bbox"), dict) else [],
        email=email,
        source="review",
    )
    return {"ok": True, "deleted": deleted.get("id"), "updated_at": payload["updated_at"], "feedback": fb}


@app.patch("/api/admin/restorer/region_notes/{note_id}")
async def admin_restorer_region_notes_patch(note_id: str, request: Request, authorization: str = Header(None)):
    """Edit own region note comment/bbox."""
    user = await require_admin(authorization)
    nid = (note_id or "").strip()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object required")
    email = (user.get("email") or "").strip().lower()
    notes = _restorer_load_region_notes()
    found = None
    for i, n in enumerate(notes):
        if isinstance(n, dict) and n.get("id") == nid:
            found = i
            break
    if found is None:
        raise HTTPException(404, "note not found")
    cur = dict(notes[found])
    owner = (cur.get("email") or "").strip().lower()
    if owner and owner != email:
        raise HTTPException(403, "can only edit own note")
    if "comment" in body:
        c = str(body.get("comment") or "").strip()[:4000]
        if not c:
            raise HTTPException(400, "comment required")
        cur["comment"] = c
    if "bbox" in body:
        bb = _restorer_norm_bbox(body.get("bbox") or {})
        if not bb:
            raise HTTPException(400, "bad bbox")
        cur["bbox"] = bb
    if "image" in body:
        image = str(body.get("image") or "").strip().lower()
        if image not in _RESTORER_REGION_IMAGES:
            raise HTTPException(400, "bad image")
        cur["image"] = image
    cur["email"] = email or owner
    cur["updated_at"] = int(time.time())
    notes[found] = cur
    payload = _restorer_save_region_notes(notes, email=email)
    fb = _restorer_feedback_emit(
        photo_id=cur.get("photo_id") or "",
        verdict=str((body or {}).get("verdict") or "").strip(),
        comment=cur.get("comment") or "",
        defect_note="",
        regions=[{
            "x": (cur.get("bbox") or {}).get("x"),
            "y": (cur.get("bbox") or {}).get("y"),
            "w": (cur.get("bbox") or {}).get("w"),
            "h": (cur.get("bbox") or {}).get("h"),
            "side": cur.get("image") or "before",
            "comment": cur.get("comment") or "",
        }],
        email=email,
        source="review",
    )
    return {"ok": True, "note": cur, "updated_at": payload["updated_at"], "feedback": fb}




@app.post("/api/admin/restorer/send_q")
async def admin_restorer_send_q(request: Request, authorization: str = Header(None)):
    """Admin one-tap: queue FULL marks/notes snapshot for Q (+ optional LIVE TG)."""
    user = await require_admin(authorization)
    email = (user.get("email") or "").strip().lower()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    kind = str(body.get("kind") or "marks_export").strip().lower()
    tip = str(body.get("tip") or _restorer_git_tip() or "").strip()[:80]
    if not SPACES_KEY or not SPACES_SECRET:
        raise HTTPException(503, "spaces unavailable")
    client = s3()
    if kind in ("region_notes_export", "region_notes", "notes"):
        notes = _restorer_load_region_notes()
        # optional client overlay: body.notes array replaces for this ping only
        if isinstance(body.get("notes"), list):
            notes = body["notes"]
        fb = _fb_publish_region_notes_export(
            client, SPACES_BUCKET, notes=notes, email=email, tip=tip, source="review",
        )
        if not fb.get("ok"):
            raise HTTPException(502, "send_q failed")
        return {
            "ok": True,
            "kind": "region_notes_export",
            "ping_mode": fb.get("ping_mode"),
            "event_id": fb.get("event_id"),
            "summary": fb.get("summary"),
            "queue": fb.get("queue"),
            "ping": {"ok": bool((fb.get("ping") or {}).get("ok")), "reason": (fb.get("ping") or {}).get("reason") or (fb.get("ping") or {}).get("via")},
            "message": "Отправлено Q",
        }
    # default: marks_export — server marks + optional body.marks overlay
    marks = _restorer_load_marks()
    incoming = body.get("marks") if "marks" in body else None
    if isinstance(incoming, dict):
        # merge client snapshot over server (UI may have fresher local state)
        merged = dict(marks)
        for pid, raw in incoming.items():
            pid_s = str(pid or "").strip()
            if not pid_s or not isinstance(raw, dict):
                continue
            merged[pid_s] = {
                "verdict": str(raw.get("verdict") or "").strip(),
                "comment": str(raw.get("comment") or "")[:2000],
                "defect_note": str(raw.get("defect_note") or "")[:2000],
                "updated_at": int(raw.get("updated_at") or time.time()),
            }
        marks = merged
        # persist merged snapshot so redeploy / empty FS does not lose Send Q payload
        try:
            _restorer_save_marks(marks, email=email)
        except Exception:
            pass
    fb = _fb_publish_marks_export(
        client, SPACES_BUCKET, marks=marks, email=email, tip=tip, source="restorer",
    )
    if not fb.get("ok"):
        raise HTTPException(502, "send_q failed")
    return {
        "ok": True,
        "kind": "marks_export",
        "ping_mode": fb.get("ping_mode"),
        "event_id": fb.get("event_id"),
        "summary": fb.get("summary"),
        "queue": fb.get("queue"),
        "ping": {"ok": bool((fb.get("ping") or {}).get("ok")), "reason": (fb.get("ping") or {}).get("reason") or (fb.get("ping") or {}).get("via")},
        "message": "Отправлено Q",
    }


@app.post("/api/admin/restorer/feedback_smoke")
async def admin_restorer_feedback_smoke(authorization: str = Header(None)):
    """Admin-only smoke: write one Spaces journal event + wake ping (no corpus change)."""
    user = await require_admin(authorization)
    email = (user.get("email") or "").strip().lower()
    fb = _restorer_feedback_emit(
        photo_id="_smoke_feedback",
        verdict="weak",
        comment="smoke feedback journal+ping",
        defect_note="",
        regions=[{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "side": "before", "comment": "smoke"}],
        email=email,
        source="smoke",
        kind="region_note",
        action="smoke",
        tip="f98c2ac",
    )
    return {"ok": True, "feedback": fb, "keys": {
        "journal": "feedback/restorer_journal.jsonl",
        "pending_ping": "feedback/pending_ping.json",
    }}

@app.get("/api/admin/stats")
async def admin_stats(authorization: str = Header(None)):
    """Queue counts for ops. Quiet REST Prefer:count=exact (no count_status RPC / migration)."""
    await require_admin(authorization)
    statuses = (
        "queued", "uploaded", "processing", "processing_analyze", "analyzed",
        "generated", "processing_verify", "needs_review", "done", "failed",
    )
    out = {"queue": {}, "total_restorations": 0}
    for st in statuses:
        out["queue"][st] = await _restorations_count(st)
    q = out["queue"]
    out["total_restorations"] = sum(q.values())
    # overnight waiting == analyzed (worker INTAKE_ONLY / min-batch hold)
    out["overnight_waiting"] = q.get("analyzed", 0)
    out["summary"] = {
        "queued": q.get("queued", 0) + q.get("uploaded", 0),
        "analyzed": q.get("analyzed", 0),
        "verified": q.get("generated", 0) + q.get("processing_verify", 0) + q.get("needs_review", 0),
        "done": q.get("done", 0),
        "failed": q.get("failed", 0),
        "overnight_waiting": q.get("analyzed", 0),
    }
    return out

@app.get("/api/admin/restorations")
async def admin_restorations(authorization: str = Header(None), limit: int = 50, status: str = None):
    await require_admin(authorization)
    params = {"select": "*", "order": "created_at.desc", "limit": str(min(limit, 200))}
    if status:
        params["status"] = f"eq.{status}"
    rows = await db("GET", "restorations", params=params) or []
    client = s3()
    for r in rows:
        try:
            if r.get("original_key"):
                r["original_url"] = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": r["original_key"]}, ExpiresIn=3600)
            if r.get("result_key"):
                r["result_url"] = client.generate_presigned_url("get_object", Params={"Bucket": SPACES_BUCKET, "Key": r["result_key"]}, ExpiresIn=3600)
        except Exception:
            pass
    return rows

@app.post("/api/admin/restorations/{rid}/retry")
async def admin_retry(rid: str, authorization: str = Header(None)):
    await require_admin(authorization)
    res = await db("PATCH", "restorations", params={"id": f"eq.{rid}"}, payload={"status": "queued", "error": None})
    if not res:
        raise HTTPException(404, "not found")
    return {"ok": True}

@app.delete("/api/admin/restorations/{rid}")
async def admin_delete(rid: str, authorization: str = Header(None)):
    await require_admin(authorization)
    rows = await db("GET", "restorations", params={"id": f"eq.{rid}", "select": "original_key,result_key"})
    if not rows:
        raise HTTPException(404, "not found")
    client = s3()
    for k in (rows[0].get("original_key"), rows[0].get("result_key")):
        if k:
            try:
                client.delete_object(Bucket=SPACES_BUCKET, Key=k)
            except Exception:
                pass
    await db("DELETE", "restorations", params={"id": f"eq.{rid}"})
    return {"ok": True, "deleted": rid}


@app.get("/api/admin/waitlist")
async def admin_waitlist(authorization: str = Header(None), limit: int = 500):
    """Admin waitlist emails from Spaces JSONL and/or Supabase. No PII in logs."""
    await require_admin(authorization)
    lim = min(max(int(limit or 500), 1), 2000)
    emails = []
    seen = set()
    storage = []

    # Spaces JSONL fallback (primary while table missing)
    try:
        for e in _waitlist_spaces_load(s3(), SPACES_BUCKET):
            em = (e.get("email") or "").strip().lower()
            if not em or em in seen:
                continue
            seen.add(em)
            emails.append({
                "email": em,
                "name": e.get("name"),
                "note": e.get("note"),
                "source": e.get("source") or "spaces_fallback",
                "ts": e.get("ts"),
                "storage": "spaces_fallback",
            })
        storage.append("spaces_fallback")
    except Exception:
        pass

    # Supabase table if present
    try:
        rows = await db("GET", "waitlist", params={
            "select": "email,name,note,source,created_at",
            "order": "created_at.desc",
            "limit": str(lim),
        }) or []
        for r in rows:
            em = (r.get("email") or "").strip().lower()
            if not em or em in seen:
                continue
            seen.add(em)
            emails.append({
                "email": em,
                "name": r.get("name"),
                "note": r.get("note"),
                "source": r.get("source") or "supabase",
                "ts": r.get("created_at"),
                "storage": "supabase",
            })
        storage.append("supabase")
    except HTTPException as e:
        if not _waitlist_table_missing(e.detail):
            raise

    def _ts_key(e):
        t = e.get("ts")
        if t is None:
            return 0.0
        if isinstance(t, (int, float)):
            return float(t)
        try:
            from datetime import datetime
            s = str(t).replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    emails.sort(key=_ts_key, reverse=True)
    emails = emails[:lim]
    return {
        "ok": True,
        "count": len(emails),
        "storage": "+".join(storage) if storage else "none",
        "emails": emails,
    }


@app.get("/api/admin/invite-codes")
async def admin_list_invite_codes(authorization: str = Header(None), limit: int = 100):
    """Список инвайт-кодов (безопасные поля)."""
    await require_admin(authorization)
    rows = await db("GET", "invite_codes", params={
        "select": "code,free_restorations,max_uses,used_count,active,note,created_at",
        "order": "created_at.desc",
        "limit": str(min(max(limit, 1), 500)),
    }) or []
    return {"codes": rows}


@app.post("/api/admin/invite-codes")
async def admin_create_invite_codes(request: Request, authorization: str = Header(None)):
    """Создать N инвайт-кодов. Body: count, max_uses, free_restorations|credits, note, prefix."""
    await require_admin(authorization)
    body = await request.json()
    count = max(1, min(int(body.get("count") or 1), 50))
    max_uses = max(1, min(int(body.get("max_uses") or 1), 1000))
    free_restorations = max(1, min(int(body.get("free_restorations") or body.get("credits") or 3), 100))
    note = (body.get("note") or "")[:200] or None
    prefix = ((body.get("prefix") or "SMH").strip().upper() or "SMH")[:12]
    alphabet = string.ascii_uppercase + string.digits
    created = []
    for _ in range(count):
        row = None
        for _attempt in range(12):
            code = f"{prefix}-{''.join(secrets.choice(alphabet) for _ in range(6))}"
            payload = {
                "code": code,
                "free_restorations": free_restorations,
                "max_uses": max_uses,
                "used_count": 0,
                "active": True,
                "note": note,
            }
            try:
                res = await db("POST", "invite_codes", payload=payload)
                row = res[0] if isinstance(res, list) else res
                break
            except HTTPException as e:
                detail = str(e.detail).lower()
                if e.status_code in (409, 23505) or "duplicate" in detail or "unique" in detail:
                    continue
                raise
        if row is None:
            raise HTTPException(500, "could_not_generate_unique_code")
        created.append({
            "code": row.get("code"),
            "free_restorations": row.get("free_restorations"),
            "max_uses": row.get("max_uses"),
            "used_count": row.get("used_count", 0),
            "active": row.get("active", True),
            "note": row.get("note"),
            "created_at": row.get("created_at"),
        })
    return {"ok": True, "created": created}



# ============== ARENA CHAT (общий чат конкурса на четверых) ==============
ARENA_SECRET = os.environ.get("ARENA_SECRET", "arena2026")
ARENA_AUTHORS = {"Флорентиец", "О", "М", "С", "Florentine", "O", "M", "C"}

ARENA_KEY = "arena/chat.json"

def _arena_load():
    client = s3()
    try:
        obj = client.get_object(Bucket=SPACES_BUCKET, Key=ARENA_KEY)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return []

def _arena_save(rows):
    client = s3()
    client.put_object(Bucket=SPACES_BUCKET, Key=ARENA_KEY,
                      Body=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
                      ContentType="application/json")

@app.get("/api/arena/messages")
async def arena_messages(after: int = 0):
    rows = _arena_load()
    return {"messages": [r for r in rows if r.get("id", 0) > after]}

@app.post("/api/arena/send")
async def arena_send(request: Request):
    data = await request.json()
    secret = (data.get("secret") or "").strip()
    if secret != ARENA_SECRET:
        raise HTTPException(401, "bad secret")
    author = (data.get("author") or "").strip()
    body = (data.get("body") or "").strip()
    kind = (data.get("kind") or "msg").strip()
    if not author or not body:
        raise HTTPException(400, "author and body required")
    if len(body) > 8000:
        body = body[:8000]
    rows = _arena_load()
    nid = (rows[-1]["id"] + 1) if rows else 1
    msg = {"id": nid, "author": author, "body": body, "kind": kind,
           "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"}
    rows.append(msg)
    _arena_save(rows)
    return {"ok": True, "message": msg}

@app.post("/api/arena/summary")
async def arena_summary(request: Request):
    data = await request.json()
    if (data.get("secret") or "").strip() != ARENA_SECRET:
        raise HTTPException(401, "bad secret")
    rows = _arena_load()
    # лёгкое экстрактивное саммери без внешних вызовов
    total = len(rows)
    by_author = {}
    commands, updates, decisions = [], [], []
    for r in rows:
        a = r.get("author", "?")
        by_author[a] = by_author.get(a, 0) + 1
        b = (r.get("body") or "").strip()
        k = r.get("kind")
        if k == "command":
            commands.append(b)
        elif k == "update":
            updates.append(f"{a}: {b}")
        elif any(w in b.lower() for w in ("принято", "договор", "решено", "финал", "итог")):
            decisions.append(f"{a}: {b[:140]}")
    lines = [f"Всего сообщений: {total}.",
             "По авторам: " + ", ".join(f"{a}={n}" for a, n in by_author.items()) + "."]
    if commands:
        lines.append("Команды Флорентийца: " + " | ".join(commands[-6:]))
    if updates:
        lines.append("Последние обновления: " + " | ".join(updates[-8:]))
    if decisions:
        lines.append("Договорённости: " + " | ".join(decisions[-8:]))
    summary = "\n".join(lines)
    nid = (rows[-1]["id"] + 1) if rows else 1
    rows.append({"id": nid, "author": "Система", "body": summary, "kind": "summary",
                 "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"})
    _arena_save(rows)
    return {"ok": True, "summary": summary}

@app.post("/api/arena/reset")
async def arena_reset(request: Request):
    data = await request.json()
    if (data.get("secret") or "").strip() != ARENA_SECRET:
        raise HTTPException(401, "bad secret")
    seed = data.get("seed")
    rows = []
    if seed:
        rows = [{"id": 1, "author": "Система", "body": str(seed), "kind": "summary",
                 "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z"}]
    _arena_save(rows)
    return {"ok": True, "count": len(rows)}

@app.get("/arena")
async def arena_page():
    return HTMLResponse(ARENA_HTML)
