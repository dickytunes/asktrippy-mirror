# backend/enrichment/llm_summary.py
# Safe RAG summary formatter (no external LLM calls in MVP).
# Input: enrichment dict + venue basics; Output: ~100–140 word neutral summary with source count.
#
from __future__ import annotations

from typing import Dict, Optional, List

def _fmt_hours(hours: dict) -> str:
    if not hours:
        return ""
    days = ["mon","tue","wed","thu","fri","sat","sun"]
    spans = []
    for d in days:
        if d in hours and hours[d]:
            parts = "/".join(["–".join(x) for x in hours[d][:2]])  # up to two spans per day in summary
            spans.append(f"{d.capitalize()} {parts}")
    return "; ".join(spans[:4])  # cap for brevity

def _fmt_price(pr: Optional[str]) -> str:
    return f" Price range: {pr}." if pr else ""

def _fmt_features(features: List[str] | None) -> str:
    if not features:
        return ""
    return " Features: " + ", ".join(features[:5]) + "."

def summarize(venue: Dict, enrichment: Dict) -> str:
    """
    Build a concise, neutral summary from known fields only (100–140 words target).
    """
    name = venue.get("name") or "This place"
    cat = venue.get("category_name") or ""
    parts: List[str] = []

    # Lead
    lead_bits = []
    if cat:
        lead_bits.append(cat)
    if venue.get("locality") or venue.get("region"):
        loc = ", ".join([x for x in [venue.get("locality"), venue.get("region")] if x])
        if loc:
            lead_bits.append(loc)
    lead = f"{name} — " + " · ".join(lead_bits) if lead_bits else f"{name}"
    parts.append(lead + ".")

    # Description
    desc = enrichment.get("description")
    if desc:
        parts.append(desc.strip())

    # Hours / contact / website
    hours_s = _fmt_hours(enrichment.get("hours") or {})
    line = []
    if hours_s:
        line.append(f"Hours: {hours_s}.")
    contact = enrichment.get("contact_details") or {}
    if contact.get("phone"):
        line.append(f"Phone: {contact['phone']}.")
    if contact.get("website"):
        line.append(f"Website: {contact['website']}.")
    if line:
        parts.append(" ".join(line))

    # Menu/fees/price/features depending on category
    if enrichment.get("menu_url"):
        parts.append(f"Menu available: {enrichment['menu_url']}.{_fmt_price(enrichment.get('price_range'))}")
    elif enrichment.get("price_range"):
        parts.append(_fmt_price(enrichment.get("price_range")))
    if enrichment.get("fees"):
        parts.append(f"Tickets/fees: {enrichment['fees']}.")

    feat_s = _fmt_features(enrichment.get("features") or enrichment.get("amenities"))
    if feat_s:
        parts.append(feat_s)

    # Sources (just count; full URLs shown in UI)
    scount = len(enrichment.get("sources") or [])
    if scount:
        parts.append(f"Sourced from {scount} page(s) on the venue site.")

    text = " ".join([p.strip() for p in parts if p and isinstance(p, str)]).strip()
    # keep between ~100–140 words if possible (simple clamp: truncate to 140 words)
    words = text.split()
    if len(words) > 140:
        text = " ".join(words[:140])
    return text
