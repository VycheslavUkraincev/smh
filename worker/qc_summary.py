#!/usr/bin/env python3
"""SaveMyHistory — сводка проверки реставраций партиями (для отчёта в чат).
Читает smh_qc_report.json (результат ИИ-проверки) и выдаёт человекочитаемую сводку:
сколько чистых, сколько с проблемами лиц/деталей, список фото на пересмотр — партиями.
Запуск: python smh_qc_summary.py [batch_start] [batch_size]
"""
import json, sys, os

WS = "/workspace"
REP = os.path.join(WS, "smh_qc_report.json")

# какие режимы считаем «бережными» (приоритет для документальной честности)
MODES = ["authentic", "vivid", "modern"]
FLAGS = ["face_changed", "people_count_changed", "extra_added", "missing_removed", "artifacts"]

def load():
    d = json.load(open(REP))
    # ключи — номера фото как строки; сортируем по числу
    return dict(sorted(d.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 1e9))

def photo_status(rec):
    """Вернуть худший флаг по всем режимам + список проблемных режимов."""
    problems = {}
    for m in MODES:
        r = rec.get(m)
        if not isinstance(r, dict):
            continue
        bad = [f for f in FLAGS if r.get(f)]
        if bad:
            problems[m] = bad
    return problems

def main(start=0, size=50):
    d = load()
    keys = list(d.keys())
    total = len(keys)
    batch = keys[start:start+size]
    if not batch:
        print(f"Партия пуста (start={start}). Всего фото в отчёте: {total}.")
        return

    clean = 0
    face_issues = []     # самое важное — подмена/изменение лиц
    detail_issues = []   # лишние/убранные детали, артефакты
    for k in batch:
        prob = photo_status(d[k])
        if not prob:
            clean += 1
            continue
        # классификация: есть ли проблема с лицами хоть в одном режиме
        has_face = any("face_changed" in v or "people_count_changed" in v for v in prob.values())
        if has_face:
            face_issues.append((k, prob))
        else:
            detail_issues.append((k, prob))

    end = min(start+size, total)
    print(f"📊 ПРОВЕРКА РЕСТАВРАЦИЙ — партия {start+1}–{end} из {total}")
    print(f"✅ Чистые (без замечаний): {clean} из {len(batch)}")
    print(f"⚠️ Замечания по лицам: {len(face_issues)}")
    print(f"🔸 Замечания по деталям/артефактам: {len(detail_issues)}")

    if face_issues:
        print("\n🔴 ФОТО НА ПЕРЕСМОТР (лица):")
        for k, prob in face_issues[:15]:
            modes = ", ".join(f"{m}({'+'.join(v)})" for m, v in prob.items())
            print(f"  • photo{k}: {modes}")
        if len(face_issues) > 15:
            print(f"  …и ещё {len(face_issues)-15}")

    if detail_issues:
        print("\n🟡 ФОТО С ДЕТАЛЯМИ (менее критично):")
        for k, prob in detail_issues[:10]:
            modes = ", ".join(f"{m}" for m in prob)
            print(f"  • photo{k}: {modes}")
        if len(detail_issues) > 10:
            print(f"  …и ещё {len(detail_issues)-10}")

    nxt = start + size
    if nxt < total:
        print(f"\n➡️ Следующая партия: start={nxt}")
    else:
        print("\n🏁 Это последняя партия — весь архив проверен.")

if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    main(start, size)
