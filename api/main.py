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
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://savemyhistory.tech")
SHARE_SECRET = os.environ.get("SHARE_SECRET", SUPABASE_SECRET or "smh-share")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

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

@app.get("/api/admin/stats")
async def admin_stats(authorization: str = Header(None)):
    await require_admin(authorization)
    out = {"queue": {}, "total_restorations": 0}
    for st in ("queued", "uploaded", "processing", "processing_analyze", "analyzed", "generated", "processing_verify", "needs_review", "done", "failed"):
        rows = await db("GET", "restorations", params={"status": f"eq.{st}", "select": "id", "limit": "100000"})
        out["queue"][st] = len(rows or [])
    out["total_restorations"] = sum(out["queue"].values())
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
    await db("POST", "arena_chat", payload={"author": "Система", "body": summary, "kind": "summary"})
    return {"ok": True, "summary": summary}

@app.get("/arena")
async def arena_page():
    return HTMLResponse(ARENA_HTML)
