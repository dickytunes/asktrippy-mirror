# backend/quality/html_gate.py
# HTML quality gates per Section 3C/3F:
# - Must be text/html or application/xhtml+xml
# - HTTP 200
# - ≥ MIN_VISIBLE_CHARS visible text
# - Reject placeholder pages (coming soon/under construction)
#
from __future__ import annotations

import os
import re
from typing import Optional

MIN_VISIBLE_CHARS = int(os.getenv("CRAWL_MIN_VISIBLE_CHARS", "200"))

PLACEHOLDER_PATTERNS = [
    r"coming\s+soon",
    r"under\s+construction",
    r"maintenance\s+mode",
    r"site\s+is\s+being\s+built",
]

def is_valid_mime(content_type: Optional[str]) -> bool:
    if not content_type:
        return False
    ct = content_type.split(";")[0].strip().lower()
    return ct in ("text/html", "application/xhtml+xml")

def is_placeholder(text: str) -> bool:
    tl = (text or "").lower()
    return any(re.search(p, tl) for p in PLACEHOLDER_PATTERNS)

def visible_text_ok(text: Optional[str]) -> bool:
    return bool(text) and len(text.strip()) >= MIN_VISIBLE_CHARS

def quality_reason(http_status: int, content_type: Optional[str], text: Optional[str]) -> str:
    """
    Return 'ok' or one of: non_200_status, invalid_mime, thin_content, placeholder
    """
    if http_status != 200:
        return "non_200_status"
    if not is_valid_mime(content_type):
        return "invalid_mime"
    if not visible_text_ok(text):
        return "thin_content"
    if is_placeholder(text or ""):
        return "thin_content"
    return "ok"
