# backend/enrichment/facts_extractor.py
# Heuristic extraction from Trafilatura cleaned_text, per page_type.
#
# Inputs: a Page-like object with fields: page_type, url, cleaned_text.
# Outputs (all optional keys):
#   {
#     "hours": { "mon":[["09:00","17:00"]], ... },
#     "contact_details": {"phone": "...", "email": "...", "website": "..."},
#     "menu_url": "https://...",           # if page_type == "menu"
#     "price_range": "$" | "$$" | "€€" | None,
#     "features": [...],
#     "fees": "Adults £12; Child £6",
#     "description": "Concise summary from the page text (first ~300 chars)"
#   }

from __future__ import annotations

import re
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

DAY_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day)?\b", re.I)
TIME_BLOCK_RE = re.compile(r"(\d{1,2})[:\.h]?(\d{2})")  # 9:00 / 9.00 / 9h00
RANGE_RE = re.compile(r"(\d{1,2}[:\.h]?\d{2})\s*(?:–|-|to|till|until|—)\s*(\d{1,2}[:\.h]?\d{2})", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{6,}\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
CURRENCY_RE = re.compile(r"([€£$])\s?(\d+(?:[.,]\d{1,2})?)")

PRICE_SYMBOL_RE = re.compile(r"price\s*range\s*[:\-]\s*([€£$]{1,4})", re.I)

def _hhmm(s: str) -> Optional[str]:
    s = s.strip().lower().replace(".", ":").replace("h", ":")
    if ":" not in s and len(s) in (3,4):
        s = s[:-2] + ":" + s[-2:]
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception:
        return None
    return None


def _extract_hours(text: str) -> Dict[str, List[List[str]]]:
    # Very light: look for lines with day names and time ranges.
    out: Dict[str, List[List[str]]] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        if not DAY_RE.search(ln):
            continue
        # collect ranges
        for m in RANGE_RE.finditer(ln):
            o = _hhmm(m.group(1))
            c = _hhmm(m.group(2))
            if not (o and c):
                continue
            # pick the first day token in the line for attribution (simple heuristic)
            dmatch = DAY_RE.search(ln)
            if not dmatch:
                continue
            d = dmatch.group(1).lower()[:3]
            out.setdefault(d, []).append([o, c])
    return out


def _price_from_symbols(text: str) -> Optional[str]:
    m = PRICE_SYMBOL_RE.search(text)
    if m:
        return m.group(1)
    # fallback: infer from multiple prices on menu pages
    prices = CURRENCY_RE.findall(text)
    if not prices:
        return None
    cur = prices[0][0]
    # naive bucketing: single currency sign repeated count
    uniq_vals = {float(p[1].replace(",", ".")) for p in prices if p[1]}
    if not uniq_vals:
        return None
    avg = sum(uniq_vals) / len(uniq_vals)
    # very rough bands (tune later per city)
    if cur in ("£", "€", "$"):
        if avg < 10: return cur
        if avg < 25: return cur*2
        if avg < 45: return cur*3
        return cur*4
    return None


def extract_from_page(page) -> Dict[str, Any]:
    text = (getattr(page, "cleaned_text", None) or "").strip()
    ptype = (getattr(page, "page_type", "") or "").lower()
    url = getattr(page, "url", None) or getattr(page, "final_url", None) or ""

    out: Dict[str, Any] = {}

    if not text:
        # Even with empty text, if this is the menu page we can set menu_url
        if ptype == "menu" and url:
            out["menu_url"] = url
        return out

    # Contact
    m = EMAIL_RE.search(text)
    if m:
        out.setdefault("contact_details", {})
        out["contact_details"]["email"] = m.group(0)

    m = PHONE_RE.search(text)
    if m:
        phone = re.sub(r"[^\d+]", "", m.group(1))
        if len(phone) >= 7:
            out.setdefault("contact_details", {})
            out["contact_details"]["phone"] = phone

    # Hours (only if on hours/contact/about/homepage)
    if ptype in ("hours", "contact", "about", "homepage"):
        hours = _extract_hours(text)
        if hours:
            out["hours"] = hours

    # Fees (attractions)
    if ptype in ("fees", "about", "visit", "homepage"):
        # collect short fee line if it contains a currency
        if CURRENCY_RE.search(text):
            # take the shortest line with currency to avoid walls of text
            fee_line = min([ln for ln in text.splitlines() if CURRENCY_RE.search(ln)], key=len)[:200]
            out["fees"] = fee_line.strip()

    # Menu & price range (restaurants)
    if ptype == "menu":
        out["menu_url"] = url
        pr = _price_from_symbols(text)
        if pr:
            out["price_range"] = pr

    # Description (fallback)
    if "description" not in out:
        # take the first line with decent length
        for ln in text.splitlines():
            t = ln.strip()
            if 60 <= len(t) <= 300:
                out["description"] = t
                break

    return out
