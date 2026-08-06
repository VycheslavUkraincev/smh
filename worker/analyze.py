#!/usr/bin/env python3
"""SaveMyHistory — СТАДИЯ 1: ИИ-глаза.
Берёт restorations.status='queued', осматривает фото vision-моделью,
определяет повреждения + режим, ГЕНЕРИРУЕТ промпт под конкретное фото,
пишет analysis+prompt, ставит status='analyzed'.
Это делается на API (дёшево) ДО аренды GPU.
Запуск: python worker_analyze.py [batch]   (по умолчанию 10)

Канон Романа 2026-08-06:
  gentle = Подлинный (свежая печать, тот же цветовой режим)
  modern = Готовый (цвет того же кадра, лица/одежда/фон lock)
  natural_color = legacy мягкая колоризация
"""
import sys, json, time
from common import log, claim, update_row, presigned_get, vision

# Canon: face/scene/clothing lock; no invent; authentic = fresh-print same color mode.
IDENTITY = (
    "CRITICAL: This is a real family-archive photo. NEVER redraw, beautify, idealize, rejuvenate or swap any face. "
    "Preserve every person's EXACT facial structure, features, proportions, age and expression — including small/blurry/background faces. "
    "Preserve the exact number of people, the same clothing geometry/patterns, background and scene. "
    "Do NOT invent new objects, people, outfits, props or backgrounds. Face accuracy is more important than beauty."
)

ANALYZE_PROMPT = (
    "You are a photo-restoration analyst for a real family archive. Return STRICT JSON only, no prose:\n"
    "{\n"
    '  "kind": "bw" | "faded_color" | "color",\n'
    '  "damage": ["scratches","dust","stains","creases","tears","fading","blur","noise","missing_parts"],  // only what you actually see\n'
    '  "faces": <int>,           // number of human faces, include blurry/background\n'
    '  "face_clarity": "clear" | "soft" | "very_blurry",\n'
    '  "severity": "light" | "medium" | "heavy",   // overall damage\n'
    '  "recommended_mode": "gentle" | "natural_color" | "modern",  // gentle=Подлинный authentic, modern=Готовый color enliven, natural_color=muted legacy\n'
    '  "notes": "<one short sentence on what to fix>"\n'
    "}\n"
    "Rules (product canon):\n"
    "- gentle (Подлинный): default for bw/documentary sources — repair damage as a fresh print of the SAME photo; KEEP black-and-white if source is bw.\n"
    "- modern (Готовый): color-enliven the SAME frame when a lively color result fits; faces/clothes/background stay locked.\n"
    "- natural_color: only if source is already faded color and a muted period-true color repair fits better than full modern.\n"
    "- Prefer gentle over inventing color on pure bw ancestors. Be conservative — these are real people.\n"
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
        return {"kind":"faded_color","damage":[],"faces":1,"face_clarity":"soft","severity":"medium","recommended_mode":"gentle","notes":""}
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
    if a.get("recommended_mode") not in VALID_MODE: a["recommended_mode"] = "gentle"
    try: a["faces"] = max(0, int(a.get("faces", 1)))
    except Exception: a["faces"] = 1
    return a

# шаблоны промптов генерации по режиму (стадия 2 возьмёт готовое)
# gentle → Подлинный; modern → Готовый; natural_color → мягкая колоризация (legacy)
def build_prompt(a):
    dmg = ", ".join(a.get("damage", [])) or "general aging"
    kind = a.get("kind", "faded_color")
    base_fix = (
        f"Carefully repair {dmg} using only evidence from this same frame. "
        "Fill tears/scratches/stains when you can without inventing new faces, outfits or scenes. "
        "Fill frame edge to edge, no borders, no table edges around the print. "
        "Keep natural skin texture and real film grain. "
        "Do NOT smooth, beautify, rejuvenate or plasticize faces. "
        "Do NOT invent people, clothing patterns, props or backgrounds. "
        "Do NOT add AI watermarks or make it look AI-generated."
    )
    mode = a.get("recommended_mode", "gentle")
    if mode == "gentle":
        # Подлинный: свежая печать того же кадра, тот же цветовой режим
        if kind == "bw":
            color_rule = "Keep it black-and-white (same color mode as the original)."
        elif kind == "color":
            color_rule = "Keep the original color mode; correct casts only, do not restyle."
        else:
            color_rule = "Prefer archival black-and-white or very mild period tone; do not oversaturated colorize."
        body = (
            "Restore this old family photograph as an AUTHENTIC fresh print of the same photo "
            "(as if just printed from the original negative/print). "
            + base_fix + " " + color_rule +
            " Same faces, clothes, pose and scene — fully repair damage, not a modern reshoot."
        )
    elif mode == "modern":
        # Готовый: оживить цветом тот же кадр; лица/одежда/фон lock
        body = (
            "Restore and enliven this old family photograph with natural, realistic color on the SAME frame. "
            "Faces, clothing geometry/patterns and background must stay locked to the original — colorize, do not redesign. "
            + base_fix +
            " Clean and clear is fine; no beauty retouch, no new wardrobe, no new scene."
        )
    else:  # natural_color (legacy → muted color repair)
        body = (
            "Restore this old family photograph with realistic, slightly muted, period-accurate colors "
            "and natural skin tones on the SAME frame (NOT oversaturated, NOT Instagram look). "
            "Lock faces, clothes and background. " + base_fix
        )
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
        step = "spaces_presign"
        try:
            url = presigned_get(r["original_key"], ttl=1800)
            step = "openai_vision"
            raw = vision(ANALYZE_PROMPT, url)
            step = "parse"
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
            msg = str(e)
            # vision()/presign already prefix openai_vision|spaces_presign; else tag current step
            if not (msg.startswith("spaces_presign") or msg.startswith("openai_vision") or msg.startswith("parse")):
                msg = f"{step}: {msg}"
            err = f"analyze_err[{step}]: {msg[:120]}"
            update_row(rid, {"status": "queued", "error": err})
            log("analyze", f"{rid[:8]} FAIL step={step}: {msg[:80]} → queued")
    log("analyze", f"готово: {ok}/{len(rows)} проанализировано")
    return ok

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    main(n)
