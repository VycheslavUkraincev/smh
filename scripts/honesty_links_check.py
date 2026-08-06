#!/usr/bin/env python3
"""Live honesty + same-origin link integrity check for savemyhistory.tech.

Usage:
  python3 scripts/honesty_links_check.py [https://savemyhistory.tech]
Exit 0 = PASS; 1 = FAIL (instant-SLA or broken non-CF same-origin refs).
"""
from __future__ import annotations

import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://savemyhistory.tech").rstrip("/")
PAGES = [
    "/",
    "/cabinet.html",
    "/gallery.html",
    "/waitlist.html",
    "/privacy.html",
    "/terms.html",
    "/admin.html",
]
MODE_PAGES = {"/", "/cabinet.html", "/gallery.html"}
TIMING_PAGES = {"/", "/cabinet.html", "/gallery.html", "/waitlist.html", "/privacy.html", "/terms.html"}
BAD_2MIN = re.compile(
    r"2\s*мин|за\s*2\s*минут|in\s*2\s*minutes|ready\s*in\s*2\s*min",
    re.I,
)
CTX = ssl.create_default_context()


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "SMH-honesty-links/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except Exception as e:  # noqa: BLE001
        return None, "", str(e).encode()


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        for attr in ("href", "src", "action", "poster", "data-src"):
            if ad.get(attr):
                self.links.append((tag, attr, ad[attr].strip()))


def main() -> int:
    fail = 0
    print(f"BASE {BASE}")
    print("=== HONESTY ===")
    bodies: dict[str, str] = {}
    for path in PAGES:
        st, _ct, body = fetch(BASE + path)
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else ""
        bodies[path] = text
        bad = BAD_2MIN.findall(text)
        timing_hits = sum(
            1
            for pat in (r"к утру", r"48h", r"48\s*час", r"overnight")
            if re.search(pat, text, re.I)
        )
        modes = (text.count("Подлинный"), text.count("Готовый"))
        print(
            f"{path}: status={st} len={len(text)} bad2min={bad or 'NONE'} "
            f"timing_hits={timing_hits} modes={modes}"
        )
        if st != 200:
            print(f"FAIL status {path}")
            fail = 1
        if bad:
            print(f"FAIL instant-SLA {path}: {bad}")
            fail = 1
        if path in TIMING_PAGES and timing_hits < 1:
            print(f"FAIL missing overnight/48h timing on {path}")
            fail = 1
        if path in MODE_PAGES and (modes[0] < 1 or modes[1] < 1):
            print(f"FAIL missing Подлинный/Готовый on {path}")
            fail = 1

    print("=== LINKS ===")
    to_check: dict[str, list[str]] = {}
    for path, text in bodies.items():
        p = LinkExtractor()
        try:
            p.feed(text)
        except Exception as e:  # noqa: BLE001
            print(f"WARN parse {path}: {e}")
            continue
        for tag, attr, raw in p.links:
            if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            if raw.startswith("//"):
                continue
            full = urllib.parse.urljoin(BASE + path, raw)
            parsed = urllib.parse.urlparse(full)
            host = parsed.netloc.lower()
            if host and host not in ("savemyhistory.tech", "www.savemyhistory.tech"):
                continue
            if "/cdn-cgi/" in parsed.path:
                # Cloudflare email-protection rewrite — known edge noise (#93 BLOCKED)
                continue
            key = parsed._replace(fragment="").geturl()
            to_check.setdefault(key, []).append(f"{path}:{tag}[{attr}]={raw}")

    st, _ct, sm = fetch(BASE + "/sitemap.xml")
    if st == 200:
        for loc in re.findall(r"<loc>(.*?)</loc>", sm.decode("utf-8", "replace")):
            to_check.setdefault(loc, []).append("sitemap")

    broken = []
    ok_n = 0
    for url in sorted(to_check):
        st, ct, body = fetch(url)
        size = len(body) if isinstance(body, (bytes, bytearray)) else 0
        asset_ext = (".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".svg", ".woff", ".woff2", ".json", ".xml", ".txt", ".mp4", ".webm", ".webmanifest")
        note = ""
        if st == 200 and isinstance(body, (bytes, bytearray)) and url.lower().endswith(asset_ext):
            head = body[:200].lower()
            if b"<html" in head or b"<!doctype html" in head:
                note = "ASSET_GOT_HTML"
        if st is None or (isinstance(st, int) and st >= 400) or note:
            broken.append((url, st, ct, size, note or "HTTP_ERR", to_check[url][:3]))
        else:
            ok_n += 1

    print(f"checked={ok_n + len(broken)} ok={ok_n} broken={len(broken)}")
    for row in broken:
        print("BROKEN", row)
        fail = 1

    print("VERDICT", "FAIL" if fail else "PASS")
    return fail


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        print("FAIL network", e)
        raise SystemExit(1)
