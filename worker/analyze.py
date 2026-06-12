#!/usr/bin/env python3
"""SaveMyHistory — СТАДИЯ 1: ИИ-глаза.
Берёт restorations.status='queued', осматривает фото vision-моделью,
определяет повреждения + режим, ГЕНЕРИРУЕТ промпт под конкретное фото,
пишет analysis+prompt, ставит status='analyzed'.
Это делается на API (дёшево) ДО аренды GPU.
Запуск: python worker_analyze.py [batch]   (по умолчанию 10)
"""
import sys, json, time
from worker_common import log, claim, update_row, presigned_get, vision

# базовое identity-правило (наш моат: не подменять лицо)
IDENTITY = ("CRITICAL: This is a real family-archive photo. NEVER redraw, beautify, idealize or rejuvenate any face. "
 "Preserve every person's EXACT facial structure, features, proportions, age and expression — including small/blurry/background faces. "
 "Preserve the exact number of people. Face accuracy is more important than beauty.")

ANALYZE_PROMPT = (
 "You are a photo-restoration analyst. Look at this old family photograph and return STRICT JSON only, no prose:\n"
 "{\n"
 '  "kind": "bw" | "faded_color" | "color",\n'
 '  "damage": ["scratches","dust","stains","creases","tears","fading","blur","noise","missing_parts"],  // only what you actually see\n'
 '  "faces": <int>,           // number of human faces, include blurry/background\n'
 '  "face_clarity": "clear" | "soft" | "very_blurry",\n'
 '  "severity": "light" | "medium" | "heavy",   // overall damage\n'
 '  "recommended_mode": "gentle" | "natural_color" | "modern",  // gentle=bw archival, natural_color=subtle colorize, modern=fresh look\n'
 '  "notes": "<one short sentence on what to fix>"\n'
 "}\n"
 "Rules: choose natural_color for bw/faded photos of people (brings them to life but stays true). "
 "Choose gentle if the user clearly wants documentary bw. Choose modern only if photo is already fairly clear. "
 "Be conservative — these are real ancestors."
)

# валидные значения и нормализация опечаток от vision-модели
VALID_DAMAGE = {"scratches","dust","stains","creases","tears","fading","blur","noise","missing_parts"}
DAMAGE_FIX = {"creasing":"creases","crease":"creases","scratch":"scratches","stain":"stains","tear":"tears","soft":None,"dirt":"dust","spots":"stains"}
VALID_KIND = {"bw","faded_color","color"}
VALID_CLARITY = {"clear","soft","very_blurry"}
VALID_SEV = {"light","medium","heavy"}
VALID_MODE = {"gentle","natural_color","modern"}

def sanitize(a):
    """Чистим ответ vision: только валидные значения, чтобы не ломать промпт."""
    if not isinstance(a, dict):
        return {"kind":"faded_color","damage":[],"faces":1,"face_clarity":"soft","severity":"medium","recommended_mode":"natural_color","notes":""}
    dmg = []
    for d in (a.get("damage") or []):
        d = str(d).strip().lower()
        d = DAMAGE_FIX.get(d, d)
        if d in VALID_DAMAGE and d not in dmg:
            dmg.append(d)
    a["damage"] = dmg
    if a.get("kind") not in VALID_KIND: a["kind"] = "faded_color"
    if a.get("face_clarity") not in VALID_CLARITY: a["face_clarity"] = "soft"
    if a.get("severity") not in VALID_SEV: a["severity"] = "medium"
    if a.get("recommended_mode") not in VALID_MODE: a["recommended_mode"] = "natural_color"
    try: a["faces"] = max(0, int(a.get("faces", 1)))
    except Exception: a["faces"] = 1
    return a

# шаблоны промптов генерации по режиму (стадия 2 возьмёт готовое)
def build_prompt(a):
    dmg = ", ".join(a.get("damage", [])) or "general aging"
    base_fix = (f"Carefully remove {dmg}. Improve contrast, tonal range and clarity. "
                "Fill frame edge to edge, no borders. Keep natural skin texture and real film grain. "
                "Do NOT smooth or beautify faces, do NOT add modern digital over-sharpening, do NOT make it look AI-generated.")
    mode = a.get("recommended_mode", "natural_color")
    if mode == "gentle":
        body = ("Restore this old black-and-white family photograph as an authentic vintage archival print. "
                + base_fix + " Keep it black-and-white, documentary and true to the original.")
    elif mode == "modern":
        body = ("Restore this old family photograph to look like a clean, sharp, naturally-lit modern photo, "
                "vivid yet realistic colors. " + base_fix)
    else:  # natural_color
        body = ("Restore and naturally colorize this old family photograph with realistic, slightly muted, "
                "period-accurate colors and natural skin tones (NOT oversaturated, NOT Instagram look). " + base_fix)
    return IDENTITY + " " + body

def extract_json(text):
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip() if text.count("```") >= 2 else text
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    raise ValueError("no json")

def main(batch=10):
    rows = claim("queued", "processing_analyze", batch)  # временный лок-статус
    if not rows:
        log("analyze", "очередь пуста (queued нет)"); return 0
    log("analyze", f"взято {len(rows)} фото на анализ")
    ok = 0
    for r in rows:
        rid = r["id"]
        try:
            url = presigned_get(r["original_key"], ttl=1800)
            raw = vision(ANALYZE_PROMPT, url)
            a = sanitize(extract_json(raw))
            prompt = build_prompt(a)
            update_row(rid, {
                "analysis": a, "prompt": prompt,
                "mode": {"gentle":"restore","natural_color":"restore","modern":"revive"}.get(a.get("recommended_mode"),"restore"),
                "status": "analyzed", "analyzed_at": "now()",
            })
            ok += 1
            log("analyze", f"{rid[:8]} → {a.get('recommended_mode')} | {a.get('faces')} лиц | {a.get('severity')}")
        except Exception as e:
            update_row(rid, {"status": "queued", "error": f"analyze_err: {str(e)[:120]}"})
            log("analyze", f"{rid[:8]} ОШИБКА → вернул в queued: {str(e)[:80]}")
    log("analyze", f"готово: {ok}/{len(rows)} проанализировано")
    return ok

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    main(n)
