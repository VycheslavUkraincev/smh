#!/usr/bin/env python3
"""SaveMyHistory — СТАДИЯ 3: ИИ-проверка результата.
Берёт restorations.status='generated', сравнивает оригинал ↔ результат
vision-моделью на подмену лица / лишние детали / изменение возраста.
PASS → status='done'. FAIL → status='analyzed' (на повтор, max 3) или 'needs_review'.
На дешёвом API, ПОСЛЕ GPU.
Запуск: python worker_verify.py [batch]
"""
import sys, json
from common import log, claim, update_row, presigned_get, vision

VERIFY_PROMPT = (
 "You are a strict restoration QC reviewer for a REAL family archive with DIVERSE source photos "
 "(scans, phone re-shoots, faded color 80s-90s, group shots, low resolution). "
 "Compare the ORIGINAL (first image) with the RESTORED (second image). "
 "The restoration MUST preserve true identity above all. Return STRICT JSON only:\n"
 "{\n"
 '  "same_people": true|false,        // are these clearly the SAME real persons?\n'
 '  "face_changed": true|false,       // facial STRUCTURE/features altered (not just sharper)?\n'
 '  "idealized": true|false,          // clearly beautified / visibly younger / plastic-smooth?\n'
 '  "extra_added": true|false,        // invented details/people/objects not in original?\n'
 '  "count_match": true|false,        // SAME number of people (check carefully on group photos)?\n'
 '  "artifacts": true|false,          // serious AI distortions (warped eyes, melted features)?\n'
 '  "severity": "none"|"minor"|"major",  // how bad is the WORST problem for identity?\n'
 '  "verdict": "pass" | "fail",       // fail ONLY if identity is compromised (major)\n'
 '  "reason": "<short>"\n'
 "}\n"
 "RULES:\n"
 "- FAIL if: face structure changed, person visibly younger/beautified, a face swapped, "
 "people added/removed, or warped/melted features. These break identity = major.\n"
 "- PASS is fine for: damage/scratch removal, denoise, restored color, mild sharpening, "
 "slight softness from low-res source. These do NOT break identity.\n"
 "- Do NOT fail a photo just because it looks cleaner. Only fail when the PERSON changed.\n"
 "- On group photos, count people in both images explicitly before deciding count_match."
)

def extract_json(text):
    text = text.strip()
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return json.loads(text[i:j+1])
    raise ValueError("no json")

def main(batch=10):
    rows = claim("generated", "processing_verify", batch)
    if not rows:
        log("verify", "нет фото на проверку (generated)"); return 0
    log("verify", f"взято {len(rows)} на проверку")
    ok = 0
    for r in rows:
        rid = r["id"]
        try:
            orig = presigned_get(r["original_key"], ttl=1800)
            res = presigned_get(r["result_key"], ttl=1800)
            # vision с двумя картинками
            import os, urllib.request
            payload = {"model": "gpt-4o", "max_tokens": 500, "temperature": 0.1,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": VERIFY_PROMPT},
                    {"type": "image_url", "image_url": {"url": orig}},
                    {"type": "image_url", "image_url": {"url": res}},
                ]}]}
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions",
                data=json.dumps(payload).encode(), method="POST")
            req.add_header("Authorization", f"Bearer {os.environ['OPENAI_API_KEY']}")
            req.add_header("Content-Type", "application/json")
            out = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
            qc = extract_json(out["choices"][0]["message"]["content"])
            attempts = (r.get("attempts") or 0)
            if qc.get("verdict") == "pass":
                update_row(rid, {"qc": qc, "status": "done"})
                log("verify", f"{rid[:8]} ✓ PASS")
            else:
                if attempts >= 2:
                    update_row(rid, {"qc": qc, "status": "needs_review"})
                    log("verify", f"{rid[:8]} ✗ FAIL → needs_review ({qc.get('reason','')[:50]})")
                else:
                    update_row(rid, {"qc": qc, "status": "analyzed", "attempts": attempts + 1})
                    log("verify", f"{rid[:8]} ✗ FAIL → повтор #{attempts+1} ({qc.get('reason','')[:50]})")
            ok += 1
        except Exception as e:
            update_row(rid, {"status": "generated", "error": f"verify_err: {str(e)[:120]}"})
            log("verify", f"{rid[:8]} ОШИБКА → вернул в generated: {str(e)[:80]}")
    log("verify", f"готово: проверено {ok}/{len(rows)}")
    return ok

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    main(n)
