# backend/enrichment/schema_org.py
# Extract schema.org JSON-LD from a page and normalize to Voy8 fields.
#
# Outputs (all optional):
#   {
#     "hours": { "mon": [["09:00","17:00"]], ... },
#     "contact_details": { "phone": "+44 ...", "email": "info@...", "website": "https://..." , "social": ["https://..."] },
#     "description": "Concise description ...",
#     "price_range": "$$",
#     "amenities": ["wheelchairAccessible", ...],     # from amenityFeature
#     "fees": "Adults £12, Concessions £8",           # from offers/aggregateOffer if present
#     "menu_url": "https://example.com/menu"
#   }

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


DAY_ALIASES = {
    "monday": "mon", "mon": "mon", "mo": "mon",
    "tuesday": "tue", "tue": "tue", "tu": "tue",
    "wednesday": "wed", "wed": "wed", "we": "wed",
    "thursday": "thu", "thu": "thu", "th": "thu",
    "friday": "fri", "fri": "fri", "fr": "fri",
    "saturday": "sat", "sat": "sat", "sa": "sat",
    "sunday": "sun", "sun": "sun", "su": "sun",
}

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):?[0-5]\d$")  # 9:00 / 0900 / 23:30


def _ensure_hhmm(s: str) -> Optional[str]:
    s = s.strip()
    s = s.replace(".", ":")
    if ":" not in s and len(s) in (3, 4):
        s = s[:-2] + ":" + s[-2:]
    if TIME_RE.match(s):
        # zero-pad
        hh, mm = s.split(":")
        return f"{int(hh):02d}:{int(mm):02d}"
    return None


def _norm_day(d: Any) -> Optional[str]:
    if isinstance(d, dict) and "@type" in d and d.get("@type").lower() == "dayofweek" and "name" in d:
        d = d["name"]
    if isinstance(d, str):
        key = d.strip().lower()
        key = key.replace("http://schema.org/", "").replace("https://schema.org/", "")
        key = key.split("/")[-1]
        return DAY_ALIASES.get(key)
    return None


def _coerce_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _collect_jsonld(html: str) -> List[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or s.text or "")
        except Exception:
            continue
        for item in _coerce_list(data):
            if isinstance(item, dict):
                blocks.append(item)
    return blocks


def _types_of(obj: dict) -> List[str]:
    t = obj.get("@type")
    if not t:
        return []
    return [x.lower() for x in _coerce_list(t) if isinstance(x, str)]


def _parse_hours(specs: List[dict]) -> dict:
    # Normalize openingHoursSpecification into {"mon": [["09:00","17:00"], ...], ...}
    out = {k: [] for k in ["mon","tue","wed","thu","fri","sat","sun"]}
    for s in specs or []:
        if not isinstance(s, dict):
            continue
        days = _coerce_list(s.get("dayOfWeek"))
        opens = _ensure_hhmm(str(s.get("opens") or "").strip())
        closes = _ensure_hhmm(str(s.get("closes") or "").strip())
        if not (opens and closes):
            continue
        norm_days = [_norm_day(d) for d in days]
        for d in norm_days:
            if d in out:
                out[d].append([opens, closes])
    # Trim empties
    return {d: v for d, v in out.items() if v}


def _parse_amenities(feats: List[dict]) -> List[str]:
    names: List[str] = []
    for f in feats or []:
        if isinstance(f, dict):
            n = f.get("name") or f.get("propertyID") or f.get("description")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
    return list(dict.fromkeys(names))  # dedupe, keep order


def _parse_offers(offers: Any) -> Optional[str]:
    # Very light: join price & currency if present (for attractions/museums)
    parts: List[str] = []
    for o in _coerce_list(offers):
        if not isinstance(o, dict):
            continue
        price = o.get("price") or o.get("lowPrice")
        cur = o.get("priceCurrency")
        category = o.get("category") or o.get("name")
        if price and cur:
            frag = f"{category + ': ' if category else ''}{cur} {price}"
            parts.append(frag.strip())
    return "; ".join(parts) if parts else None


def parse_schema_org(html: str) -> dict:
    blocks = _collect_jsonld(html)
    res: dict = {}

    # Merge across blocks (publisher may have multiple)
    social: List[str] = []
    for b in blocks:
        types = _types_of(b)
        if not types:
            continue

        # Contact
        tel = b.get("telephone") or b.get("tel")
        email = b.get("email")
        url = b.get("url")
        same_as = _coerce_list(b.get("sameAs"))
        if tel or email or url or same_as:
            res.setdefault("contact_details", {})
            if tel and isinstance(tel, str):
                res["contact_details"]["phone"] = tel.strip()
            if email and isinstance(email, str):
                res["contact_details"]["email"] = email.strip()
            if url and isinstance(url, str):
                res["contact_details"]["website"] = url.strip()
            for s in same_as:
                if isinstance(s, str) and s.strip():
                    social.append(s.strip())

        # Description
        desc = b.get("description")
        if isinstance(desc, str) and len(desc.strip()) >= 30:
            res["description"] = desc.strip()

        # Price range (e.g. "$$")
        pr = b.get("priceRange")
        if isinstance(pr, str) and pr.strip():
            res["price_range"] = pr.strip()

        # Menu
        menu = b.get("menu") or b.get("hasMenu")
        if isinstance(menu, str) and menu.strip():
            res["menu_url"] = menu.strip()
        elif isinstance(menu, dict) and isinstance(menu.get("url"), str):
            res["menu_url"] = menu["url"].strip()

        # Opening hours
        ohs = _coerce_list(b.get("openingHoursSpecification"))
        hours = _parse_hours([x for x in ohs if isinstance(x, dict)])
        if hours:
            # Merge with any previous (union)
            prev = res.get("hours") or {}
            prev.update({k: v for k, v in hours.items() if v})
            res["hours"] = prev

        # Amenities
        am = _coerce_list(b.get("amenityFeature"))
        amenities = _parse_amenities([x for x in am if isinstance(x, dict)])
        if amenities:
            res["amenities"] = sorted(list(set((res.get("amenities") or []) + amenities)))

        # Offers / fees
        offers = b.get("offers") or b.get("aggregateOffer")
        fees = _parse_offers(offers)
        if fees:
            res["fees"] = fees

    if social:
        res.setdefault("contact_details", {})
        res["contact_details"]["social"] = list(dict.fromkeys(social))

    return res
