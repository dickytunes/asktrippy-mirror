# backend/enrichment/unify.py
# Merge schema.org and heuristic extractions into a single venue-level enrichment record.
#
# Precedence:
#   dedicated page (hours/menu/contact/about/fees) > schema.org > homepage/about text
# Sources:
#   include contributing page URLs for each field; caller persists as enrichment.sources (JSON array)
#
# API:
#   build_enrichment(pages: List[PageRecord], schema_by_url: Dict[url, dict]) -> (enrichment_dict, updated_fields)
#
from __future__ import annotations

from typing import Dict, List, Tuple, Any
from datetime import datetime, timezone

from backend.enrichment.facts_extractor import extract_from_page

FIELD_ORDER = ["hours", "contact_details", "description", "features", "fees", "menu_url", "price_range", "amenities"]

def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def _merge_hours(a: dict | None, b: dict | None) -> dict | None:
    if not a and not b:
        return None
    out = {}
    for src in (a or {}), (b or {}):
        for d, ranges in src.items():
            out.setdefault(d, [])
            for r in ranges:
                if r not in out[d]:
                    out[d].append(r)
    return out

def build_enrichment(pages: List[Any], schema_by_url: Dict[str, dict]) -> Tuple[Dict[str, Any], List[str]]:
    """
    :param pages: list of PageRecord-like objects (need .page_type, .url, .cleaned_text)
    :param schema_by_url: mapping url->parsed schema_org dict for that page (html parsed separately)
    :return: (enrichment row dict ready to persist, list of fields we updated)
    """
    # Accumulators
    acc: Dict[str, Any] = {}
    sources: Dict[str, List[str]] = {k: [] for k in FIELD_ORDER}
    updated: List[str] = []

    def take(field: str, value, url: str):
        if not value:
            return
        prev = acc.get(field)
        if field == "hours":
            merged = _merge_hours(prev, value)
            if merged:
                acc["hours"] = merged
                updated.append("hours")
                if url not in sources["hours"]:
                    sources["hours"].append(url)
        elif field == "contact_details":
            merged = (prev or {}).copy()
            merged.update(value)
            acc["contact_details"] = merged
            updated.append("contact_details")
            if url not in sources["contact_details"]:
                sources["contact_details"].append(url)
        elif field == "features":
            merged = sorted(list(set((prev or []) + list(value))))
            if merged:
                acc["features"] = merged
                updated.append("features")
                if url not in sources["features"]:
                    sources["features"].append(url)
        else:
            if prev:
                return
            acc[field] = value
            updated.append(field)
            if url not in sources.get(field, []):
                sources.setdefault(field, []).append(url)

    # 1) Pass: page-level heuristics (dedicated pages have priority)
    priority = {"hours": 0, "menu": 1, "contact": 2, "fees": 3, "about": 4, "homepage": 5, "other": 9}
    pages_sorted = sorted(pages, key=lambda p: priority.get((getattr(p, "page_type", "") or "other").lower(), 9))

    for p in pages_sorted:
        url = getattr(p, "url", None) or getattr(p, "final_url", None) or ""
        facts = extract_from_page(p)
        for k, v in facts.items():
            # map restaurant bits
            if k == "menu_url":
                take("menu_url", v, url)
            else:
                take(k, v, url)

    # 2) Pass: schema.org complements (where we still have holes)
    for p in pages_sorted:
        url = getattr(p, "url", None) or getattr(p, "final_url", None) or ""
        s = schema_by_url.get(url) or {}
        for k in ("hours", "contact_details", "description", "price_range", "amenities", "fees", "menu_url"):
            if k == "hours":
                take("hours", s.get("hours"), url)
            else:
                if k not in acc and s.get(k):
                    take(k, s.get(k), url)
                elif k in ("contact_details", "amenities") and s.get(k):
                    take(k, s.get(k), url)

    # 3) Shape final enrichment row
    enriched: Dict[str, Any] = {}
    now = _now()

    if "hours" in acc and acc["hours"]:
        enriched["hours"] = acc["hours"]
        enriched["hours_last_updated"] = now

    if "contact_details" in acc and acc["contact_details"]:
        enriched["contact_details"] = acc["contact_details"]
        enriched["contact_last_updated"] = now

    if "description" in acc and acc["description"]:
        enriched["description"] = acc["description"]
        enriched["description_last_updated"] = now

    if "features" in acc and acc["features"]:
        enriched["features"] = acc["features"]
        enriched["features_last_updated"] = now

    if "menu_url" in acc and acc["menu_url"]:
        enriched["menu_url"] = acc["menu_url"]
        enriched["menu_last_updated"] = now

    if "price_range" in acc and acc["price_range"]:
        enriched["price_range"] = acc["price_range"]
        enriched["price_last_updated"] = now

    if "amenities" in acc and acc["amenities"]:
        enriched["amenities"] = acc["amenities"]
        # reuse features_last_updated as generic freshness if you don't have a separate column
        enriched["features_last_updated"] = enriched.get("features_last_updated") or now

    if "fees" in acc and acc["fees"]:
        enriched["fees"] = acc["fees"]
        # fees freshness fits the 14d bucket (menu/contact/price); no separate timestamp in MVP table, reuse features_last_updated if needed.

    # Sources (flatten & dedupe)
    srcs: List[str] = []
    for field, urls in sources.items():
        for u in urls:
            if u and u not in srcs:
                srcs.append(u)
    if srcs:
        enriched["sources"] = srcs

    return enriched, sorted(list(set(updated)))
