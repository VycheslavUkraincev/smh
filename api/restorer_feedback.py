"""Restorer feedback journal: Spaces JSONL + parent-readable ping queue.

Roman path 3+4:
  Spaces journal (mode 4): feedback/restorer_journal.jsonl (private ACL)
  Ping queue (mode 3):     feedback/pending_ping.json (parent announces Telegram)

Optional TELEGRAM_BOT_TOKEN on DO can LIVE-push; without it, QUEUE only.
No new secrets required — uses existing SPACES_* like waitlist_fallback.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional

import httpx

JOURNAL_KEY = os.environ.get(
    "RESTORER_FEEDBACK_JOURNAL_KEY", "feedback/restorer_journal.jsonl"
)
PENDING_PING_KEY = os.environ.get(
    "RESTORER_FEEDBACK_PENDING_PING_KEY", "feedback/pending_ping.json"
)
# legacy aliases still written for older readers (best-effort dual key optional)
LEGACY_JSONL_KEY = os.environ.get("RESTORER_FEEDBACK_JSONL_KEY", "")
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "8677751074").strip()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://savemyhistory.tech")
_PING_VERDICTS = {"bad", "weak"}


def journal_key() -> str:
    return JOURNAL_KEY


def pending_ping_key() -> str:
    return PENDING_PING_KEY


def _load_jsonl(s3_client, bucket: str, key: str) -> list:
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read().decode("utf-8")
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound") or "NoSuchKey" in str(e):
            return []
        raise
    out = []
    for line in body.split(chr(10)):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _put_jsonl(s3_client, bucket: str, key: str, rows: list) -> None:
    body = chr(10).join(json.dumps(r, ensure_ascii=False) for r in rows) + chr(10)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
        ACL="private",
    )


def _load_json(s3_client, bucket: str, key: str, default):
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound") or "NoSuchKey" in str(e):
            return default
        raise


def _put_json(s3_client, bucket: str, key: str, payload) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10)).encode("utf-8"),
        ContentType="application/json",
        ACL="private",
    )


def review_link(photo_id: str = "") -> str:
    base = (PUBLIC_URL or "https://savemyhistory.tech").rstrip("/")
    url = f"{base}/review.html"
    pid = (photo_id or "").strip()
    if pid:
        url += f"?photo={pid}"
    return url


def build_event(
    *,
    kind: str,
    photo_id: str,
    source: str,
    verdict: str = "",
    comment: str = "",
    email: str = "",
    tip: str = "",
    bbox: Optional[dict] = None,
    image: str = "",
    note_id: str = "",
    action: str = "",
    defect_note: str = "",
    severity: str = "",
    extra: Optional[dict] = None,
) -> dict[str, Any]:
    """One JSONL line — schema restorer_feedback_journal_v1."""
    src = source if source in ("restorer", "review", "smoke") else "restorer"
    ev: dict[str, Any] = {
        "v": 1,
        "id": "fb_" + uuid.uuid4().hex[:16],
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts": int(time.time()),
        "kind": kind,  # mark | region_note
        "action": action or ("upsert" if kind == "mark" else "save"),
        "photo_id": (photo_id or "").strip(),
        "source": src,
        "verdict": (verdict or severity or "").strip(),
        "comment": (comment or "")[:4000],
        "email": (email or "").strip().lower()[:200],
    }
    if tip:
        ev["tip"] = str(tip)[:80]
    if defect_note:
        ev["defect_note"] = str(defect_note)[:2000]
    if severity:
        ev["severity"] = str(severity).strip().lower()[:40]
    if bbox and isinstance(bbox, dict):
        ev["bbox"] = bbox
    if image:
        ev["image"] = str(image)[:40]
    if note_id:
        ev["note_id"] = str(note_id)[:80]
    if extra and isinstance(extra, dict):
        for k, val in extra.items():
            if k not in ev and val is not None:
                ev[k] = val
    return ev


def needs_ping(event: dict) -> bool:
    v = (event.get("verdict") or event.get("severity") or "").strip().lower()
    return v in _PING_VERDICTS


def format_ping_text(ev: dict) -> str:
    pid = ev.get("photo_id") or "?"
    verdict = ev.get("verdict") or ev.get("severity") or "—"
    comment = (ev.get("comment") or "").strip()
    if len(comment) > 280:
        comment = comment[:277] + "..."
    src = ev.get("source") or "restorer"
    kind = ev.get("kind") or "mark"
    link = review_link(pid)
    lines = [
        f"SMH feedback · {src}/{kind}",
        f"photo: {pid}",
        f"verdict: {verdict}",
        f"link: {link}",
    ]
    if comment:
        lines.append(f"comment: {comment}")
    return chr(10).join(lines)


def append_journal(s3_client, bucket: str, event: dict) -> dict:
    rows = _load_jsonl(s3_client, bucket, JOURNAL_KEY)
    rows.append(event)
    if len(rows) > 5000:
        rows = rows[-5000:]
    _put_jsonl(s3_client, bucket, JOURNAL_KEY, rows)
    if LEGACY_JSONL_KEY and LEGACY_JSONL_KEY != JOURNAL_KEY:
        try:
            _put_jsonl(s3_client, bucket, LEGACY_JSONL_KEY, rows)
        except Exception:
            pass
    return {"ok": True, "n": len(rows), "key": JOURNAL_KEY}


def load_journal(s3_client, bucket: str, limit: int = 200) -> list:
    rows = _load_jsonl(s3_client, bucket, JOURNAL_KEY)
    if limit and limit > 0:
        return rows[-limit:]
    return rows


def enqueue_pending_ping(s3_client, bucket: str, event: dict, text: str, tg_result: dict) -> dict:
    payload = _load_json(
        s3_client,
        bucket,
        PENDING_PING_KEY,
        {"version": "restorer_pending_ping_v1", "pending": []},
    )
    if not isinstance(payload, dict):
        payload = {"version": "restorer_pending_ping_v1", "pending": []}
    pending = payload.get("pending")
    if not isinstance(pending, list):
        pending = []
    ping = {
        "id": "ping_" + uuid.uuid4().hex[:12],
        "utc": event.get("utc") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts": int(event.get("ts") or time.time()),
        "photo_id": event.get("photo_id") or "",
        "verdict": event.get("verdict") or "",
        "comment": (event.get("comment") or "")[:500],
        "source": event.get("source") or "",
        "kind": event.get("kind") or "",
        "email": event.get("email") or "",
        "link": review_link(event.get("photo_id") or ""),
        "text": text,
        "chat_id": TELEGRAM_CHAT_ID,
        "announced": bool(tg_result.get("ok")),
        "tg": {"ok": bool(tg_result.get("ok")), "reason": tg_result.get("reason") or tg_result.get("via")},
    }
    if event.get("bbox"):
        ping["bbox"] = event["bbox"]
    if event.get("note_id"):
        ping["note_id"] = event["note_id"]
    if event.get("tip"):
        ping["tip"] = event["tip"]
    pending.append(ping)
    if len(pending) > 200:
        pending = pending[-200:]
    payload["pending"] = pending
    payload["updated_at"] = int(time.time())
    _put_json(s3_client, bucket, PENDING_PING_KEY, payload)
    return {"ok": True, "key": PENDING_PING_KEY, "ping_id": ping["id"], "announced": ping["announced"], "n": len(pending)}


def _telegram_send(text: str) -> dict:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"ok": False, "reason": "no_telegram_env"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        with httpx.Client(timeout=12) as c:
            r = c.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            })
        if r.status_code >= 300:
            return {"ok": False, "reason": f"http_{r.status_code}"}
        return {"ok": True, "via": "telegram_bot"}
    except Exception as e:
        return {"ok": False, "reason": "exception", "error": type(e).__name__}


def publish_feedback(s3_client, bucket: str, event: dict) -> dict:
    """Journal always; on bad/weak try LIVE telegram else QUEUE. Never raises."""
    out: dict[str, Any] = {
        "event_id": event.get("id"),
        "journal": None,
        "ping_mode": "none",
        "ping": None,
        "queue": None,
    }
    try:
        out["journal"] = append_journal(s3_client, bucket, event)
    except Exception as e:
        out["journal"] = {"ok": False, "error": type(e).__name__}
        return out
    if not needs_ping(event):
        out["ping_mode"] = "skip"
        return out
    text = format_ping_text(event)
    tg = _telegram_send(text)
    out["ping"] = tg
    if tg.get("ok"):
        out["ping_mode"] = "LIVE"
    else:
        out["ping_mode"] = "QUEUE"
    try:
        out["queue"] = enqueue_pending_ping(s3_client, bucket, event, text, tg)
    except Exception as e:
        out["queue"] = {"ok": False, "error": type(e).__name__}
    return out


def _verdict_bucket(verdict: str) -> str:
    v = (verdict or "").strip().lower()
    if v == "bad":
        return "bad"
    if v == "weak":
        return "weak"
    if v.startswith("approve"):
        return "approve"
    if v:
        return "other"
    return "empty"


def summarize_marks(marks: dict) -> dict[str, Any]:
    """Counts + id→verdict/comment list for marks_export snapshot."""
    items: list[dict[str, Any]] = []
    counts = {"bad": 0, "weak": 0, "approve": 0, "other": 0, "empty": 0}
    if not isinstance(marks, dict):
        marks = {}
    for pid, raw in marks.items():
        pid_s = str(pid or "").strip()
        if not pid_s:
            continue
        if not isinstance(raw, dict):
            raw = {}
        verdict = str(raw.get("verdict") or "").strip()
        comment = str(raw.get("comment") or "")[:500]
        defect_note = str(raw.get("defect_note") or "")[:500]
        bucket = _verdict_bucket(verdict)
        counts[bucket] = counts.get(bucket, 0) + 1
        item: dict[str, Any] = {"id": pid_s, "verdict": verdict, "comment": comment}
        if defect_note:
            item["defect_note"] = defect_note
        items.append(item)
    items.sort(key=lambda x: (
        0 if _verdict_bucket(x.get("verdict") or "") == "bad" else
        1 if _verdict_bucket(x.get("verdict") or "") == "weak" else 2,
        x.get("id") or "",
    ))
    return {
        "n_marks": len(items),
        "counts": counts,
        "marks": items,
        "bad_weak": [i for i in items if _verdict_bucket(i.get("verdict") or "") in ("bad", "weak")],
    }


def summarize_region_notes(notes: list) -> dict[str, Any]:
    """Compact region-notes snapshot for send_q from review panel."""
    items: list[dict[str, Any]] = []
    if not isinstance(notes, list):
        notes = []
    for raw in notes:
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("photo_id") or "").strip()
        nid = str(raw.get("id") or "").strip()
        comment = str(raw.get("comment") or "")[:500]
        image = str(raw.get("image") or "").strip()
        bbox = raw.get("bbox") if isinstance(raw.get("bbox"), dict) else None
        item: dict[str, Any] = {
            "id": nid,
            "photo_id": pid,
            "image": image,
            "comment": comment,
        }
        if bbox:
            item["bbox"] = bbox
        items.append(item)
    return {"n_notes": len(items), "notes": items}


def format_marks_export_text(summary: dict, *, email: str = "", tip: str = "") -> str:
    """Concise Telegram text: counts + bad/weak list."""
    counts = summary.get("counts") or {}
    n = int(summary.get("n_marks") or 0)
    lines = [
        "SMH · Отправить Q · marks_export",
        f"marks: {n} · bad={counts.get('bad', 0)} weak={counts.get('weak', 0)} approve={counts.get('approve', 0)}",
        f"panel: {PUBLIC_URL.rstrip('/')}/restorer.html",
    ]
    if email:
        lines.append(f"by: {email}")
    if tip:
        lines.append(f"tip: {tip}")
    bad_weak = summary.get("bad_weak") or []
    if bad_weak:
        lines.append("bad/weak:")
        for i in bad_weak[:40]:
            c = (i.get("comment") or "").strip().replace(chr(10), " ")
            if len(c) > 120:
                c = c[:117] + "..."
            line = f"  · {i.get('id')} [{i.get('verdict') or '?'}]"
            if c:
                line += f" - {c}"
            lines.append(line)
        if len(bad_weak) > 40:
            lines.append(f"  … +{len(bad_weak) - 40} more")
    else:
        lines.append("bad/weak: (none)")
    text = chr(10).join(lines)
    if len(text) > 3500:
        text = text[:3497] + "..."
    return text


def format_region_notes_export_text(summary: dict, *, email: str = "", tip: str = "") -> str:
    n = int(summary.get("n_notes") or 0)
    lines = [
        "SMH · Отправить Q · region_notes_export",
        f"notes: {n}",
        f"panel: {PUBLIC_URL.rstrip('/')}/review.html",
    ]
    if email:
        lines.append(f"by: {email}")
    if tip:
        lines.append(f"tip: {tip}")
    notes = summary.get("notes") or []
    for i in notes[:30]:
        c = (i.get("comment") or "").strip().replace(chr(10), " ")
        if len(c) > 120:
            c = c[:117] + "..."
        line = f"  · {i.get('photo_id') or '?'} / {i.get('image') or '?'}"
        if c:
            line += f" - {c}"
        lines.append(line)
    if len(notes) > 30:
        lines.append(f"  … +{len(notes) - 30} more")
    text = chr(10).join(lines)
    if len(text) > 3500:
        text = text[:3497] + "..."
    return text


def enqueue_export_ping(s3_client, bucket: str, event: dict, text: str, tg_result: dict, snapshot: dict) -> dict:
    """Queue pending_ping with FULL marks/notes snapshot for parent announce."""
    payload = _load_json(
        s3_client,
        bucket,
        PENDING_PING_KEY,
        {"version": "restorer_pending_ping_v1", "pending": []},
    )
    if not isinstance(payload, dict):
        payload = {"version": "restorer_pending_ping_v1", "pending": []}
    pending = payload.get("pending")
    if not isinstance(pending, list):
        pending = []
    ping = {
        "id": "ping_" + uuid.uuid4().hex[:12],
        "utc": event.get("utc") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts": int(event.get("ts") or time.time()),
        "kind": event.get("kind") or "marks_export",
        "source": event.get("source") or "restorer",
        "email": event.get("email") or "",
        "tip": event.get("tip") or "",
        "photo_id": event.get("photo_id") or "",
        "verdict": "export",
        "comment": (event.get("comment") or "")[:500],
        "link": review_link(""),
        "text": text,
        "chat_id": TELEGRAM_CHAT_ID,
        "announced": bool(tg_result.get("ok")),
        "tg": {"ok": bool(tg_result.get("ok")), "reason": tg_result.get("reason") or tg_result.get("via")},
        "snapshot": snapshot,
    }
    if "n_marks" in snapshot:
        ping["n_marks"] = snapshot.get("n_marks")
        ping["counts"] = snapshot.get("counts")
        ping["marks"] = snapshot.get("marks")
    if "n_notes" in snapshot:
        ping["n_notes"] = snapshot.get("n_notes")
        ping["notes"] = snapshot.get("notes")
    pending.append(ping)
    if len(pending) > 200:
        pending = pending[-200:]
    payload["pending"] = pending
    payload["updated_at"] = int(time.time())
    _put_json(s3_client, bucket, PENDING_PING_KEY, payload)
    return {
        "ok": True,
        "key": PENDING_PING_KEY,
        "ping_id": ping["id"],
        "announced": ping["announced"],
        "n": len(pending),
    }


def publish_marks_export(
    s3_client,
    bucket: str,
    *,
    marks: dict,
    email: str = "",
    tip: str = "",
    source: str = "restorer",
) -> dict:
    """Journal marks_export + ALWAYS queue pending_ping with full snapshot; optional LIVE TG."""
    summary = summarize_marks(marks if isinstance(marks, dict) else {})
    counts = summary.get("counts") or {}
    comment = (
        f"marks_export n={summary.get('n_marks', 0)} "
        f"bad={counts.get('bad', 0)} weak={counts.get('weak', 0)} approve={counts.get('approve', 0)}"
    )
    event = build_event(
        kind="marks_export",
        photo_id="_marks_export",
        source=source if source in ("restorer", "review", "smoke") else "restorer",
        verdict="export",
        comment=comment,
        email=email,
        tip=tip,
        action="send_q",
        extra={
            "n_marks": summary.get("n_marks"),
            "counts": counts,
            "marks": summary.get("marks"),
        },
    )
    out: dict[str, Any] = {
        "ok": True,
        "kind": "marks_export",
        "event_id": event.get("id"),
        "summary": summary,
        "journal": None,
        "ping_mode": "QUEUE",
        "ping": None,
        "queue": None,
    }
    try:
        out["journal"] = append_journal(s3_client, bucket, event)
    except Exception as e:
        out["ok"] = False
        out["journal"] = {"ok": False, "error": type(e).__name__}
        return out
    text = format_marks_export_text(summary, email=email, tip=tip)
    tg = _telegram_send(text)
    out["ping"] = tg
    if tg.get("ok"):
        out["ping_mode"] = "LIVE"
    else:
        out["ping_mode"] = "QUEUE"
    try:
        out["queue"] = enqueue_export_ping(s3_client, bucket, event, text, tg, summary)
    except Exception as e:
        out["ok"] = False
        out["queue"] = {"ok": False, "error": type(e).__name__}
    return out


def publish_region_notes_export(
    s3_client,
    bucket: str,
    *,
    notes: list,
    email: str = "",
    tip: str = "",
    source: str = "review",
) -> dict:
    """Journal region_notes_export + ALWAYS queue pending_ping with notes snapshot."""
    summary = summarize_region_notes(notes if isinstance(notes, list) else [])
    comment = f"region_notes_export n={summary.get('n_notes', 0)}"
    event = build_event(
        kind="region_notes_export",
        photo_id="_region_notes_export",
        source=source if source in ("restorer", "review", "smoke") else "review",
        verdict="export",
        comment=comment,
        email=email,
        tip=tip,
        action="send_q",
        extra={
            "n_notes": summary.get("n_notes"),
            "notes": summary.get("notes"),
        },
    )
    out: dict[str, Any] = {
        "ok": True,
        "kind": "region_notes_export",
        "event_id": event.get("id"),
        "summary": summary,
        "journal": None,
        "ping_mode": "QUEUE",
        "ping": None,
        "queue": None,
    }
    try:
        out["journal"] = append_journal(s3_client, bucket, event)
    except Exception as e:
        out["ok"] = False
        out["journal"] = {"ok": False, "error": type(e).__name__}
        return out
    text = format_region_notes_export_text(summary, email=email, tip=tip)
    tg = _telegram_send(text)
    out["ping"] = tg
    if tg.get("ok"):
        out["ping_mode"] = "LIVE"
    else:
        out["ping_mode"] = "QUEUE"
    try:
        out["queue"] = enqueue_export_ping(s3_client, bucket, event, text, tg, summary)
    except Exception as e:
        out["ok"] = False
        out["queue"] = {"ok": False, "error": type(e).__name__}
    return out
