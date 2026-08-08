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
