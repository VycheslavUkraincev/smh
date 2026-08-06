"""Spaces JSONL fallback for waitlist when public.waitlist is missing."""
import json
import os
import time
from typing import Any, Optional

WAITLIST_SPACES_KEY = os.environ.get("WAITLIST_SPACES_KEY", "waitlist/entries.jsonl")


def _key() -> str:
    return WAITLIST_SPACES_KEY


def load_entries(s3_client, bucket) -> list:
    """Load waitlist entries from Spaces JSONL. Missing key → empty list."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=_key())
        body = obj["Body"].read().decode("utf-8")
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound") or "NoSuchKey" in str(e):
            return []
        raise
    entries = []
    for line in body.split(chr(10)):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries


def count_entries(s3_client, bucket) -> Optional[int]:
    """Return entry count, or None if Spaces is unreachable."""
    try:
        return len(load_entries(s3_client, bucket))
    except Exception:
        return None


def append_entry(
    s3_client,
    bucket,
    email: str,
    name: Optional[str] = None,
    note: Optional[str] = None,
    source: str = "api",
) -> tuple:
    """Append email to Spaces JSONL. Returns (already_subscribed, count)."""
    email_norm = (email or "").strip().lower()
    entries = load_entries(s3_client, bucket)
    for e in entries:
        if (e.get("email") or "").strip().lower() == email_norm:
            return True, len(entries)
    row: dict[str, Any] = {
        "email": email_norm,
        "name": name,
        "note": note,
        "source": source or "api",
        "ts": int(time.time()),
    }
    entries.append(row)
    body = chr(10).join(json.dumps(e, ensure_ascii=False) for e in entries) + chr(10)
    s3_client.put_object(
        Bucket=bucket,
        Key=_key(),
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
        ACL="private",
    )
    return False, len(entries)
