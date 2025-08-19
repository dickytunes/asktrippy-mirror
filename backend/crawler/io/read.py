# backend/crawler/io/read.py
# Read-side utilities for Voy8 crawler/enrichment.
#
# What this provides (MVP):
# - get_venue(fsq_place_id): baseline POI lookup
# - get_enrichment(fsq_place_id): enrichment row lookup
# - compute_freshness(enrichment_row, category_name): per-field stale/missing map
# - should_trigger_realtime(fsq_place_id, required_fields): True/False + which fields
# - select_stale_for_background(limit, top_percentile): pick venues for background refresh
# - select_stale_near(lat, lon, radius_m, limit): geo-aware stale picker (PostGIS)
# - get_venues_missing_website(limit): feed for Website Recovery (Section 3D)
# - get_scraped_pages(fsq_place_id, limit): quick audit of stored pages
#
# Spec alignment:
# - Freshness windows (env-configurable, defaults: hours 3d; menu/contact/price 14d; others 30d)
# - Non-negotiables per category: restaurants/bars, accommodation, attractions
# - Background selection policy: stale fields first, always include top ~10% popularity
#
# Env:
#   DATABASE_URL=postgresql://user:pass@host:port/dbname
#   FRESH_HOURS_DAYS=3
#   FRESH_MENU_CONTACT_PRICE_DAYS=14
#   FRESH_DESC_FEATURES_DAYS=30
#
# Tables assumed (per tech spec):
#   venues(fsq_place_id PK, name, category_name, latitude, longitude,
#          popularity_confidence, last_enriched_at, website, ...)
#   enrichment(fsq_place_id PK,
#              hours JSONB, hours_last_updated TIMESTAMPTZ,
#              contact_details JSONB, contact_last_updated TIMESTAMPTZ,
#              description TEXT, description_last_updated TIMESTAMPTZ,
#              menu_url TEXT, menu_items JSONB, menu_last_updated TIMESTAMPTZ,
#              price_range TEXT, price_last_updated TIMESTAMPTZ,
#              features JSONB, features_last_updated TIMESTAMPTZ,
#              -- category-specific:
#              accommodation_price_range TEXT, amenities JSONB,
#              fees TEXT, attraction_features JSONB,
#              sources JSONB)
#   scraped_pages(page_id, fsq_place_id, url, page_type, fetched_at, valid_until,
#                 http_status, content_type, content_hash, cleaned_text, source_method,
#                 redirect_chain, reason, size_bytes, duration_ms, first_byte_ms)
#
# NOTE: All functions are defensive: they tolerate missing enrichment rows.

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


# ----------------------------- config -----------------------------

FRESH_HOURS_DAYS = int(os.getenv("FRESH_HOURS_DAYS", "3"))
FRESH_MENU_CONTACT_PRICE_DAYS = int(os.getenv("FRESH_MENU_CONTACT_PRICE_DAYS", "14"))
FRESH_DESC_FEATURES_DAYS = int(os.getenv("FRESH_DESC_FEATURES_DAYS", "30"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------- DB helpers -----------------------------

def _get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


# ----------------------------- dataclasses -----------------------------

@dataclass
class FreshnessReport:
    fsq_place_id: str
    category_group: str  # one of: restaurant, accommodation, attraction, general
    required_fields: List[str]
    stale_fields: List[str]
    missing_fields: List[str]
    fresh_fields: List[str]
    last_updated: Optional[datetime]  # venue-level last_enriched_at if available


# ----------------------------- core lookups -----------------------------

def get_venue(fsq_place_id: str) -> Optional[Dict[str, Any]]:
    """Return a single venue row as dict (or None)."""
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT fsq_place_id, name, category_name, latitude, longitude,
                   popularity_confidence, last_enriched_at, website
            FROM venues
            WHERE fsq_place_id = %s
            """,
            (fsq_place_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_enrichment(fsq_place_id: str) -> dict | None:
    """Fetch enrichment snapshot for a venue."""
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                fsq_place_id,
                description,
                hours,
                contact_details,
                features,
                menu_url,
                menu_items,
                price_range,
                accommodation_price_range,
                amenities,
                fees,
                attraction_features,
                sources,
                description_last_updated,
                hours_last_updated,
                contact_last_updated,
                features_last_updated,
                menu_last_updated,
                price_last_updated,
                amenities_last_updated,
                fees_last_updated,
                attraction_features_last_updated
            FROM enrichment
            WHERE fsq_place_id = %s
            """,
            (fsq_place_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ----------------------------- category grouping -----------------------------

def _categorize(category_name: Optional[str]) -> str:
    """
    Map free-text category_name to a small set of groups for non-negotiables.
    Heuristic, fast, and good enough for MVP; refine later with FSQ category ids.
    """
    if not category_name:
        return "general"
    c = category_name.lower()
    # Restaurants / food & drink
    if any(k in c for k in ("restaurant", "café", "cafe", "bar", "pub", "diner", "bistro", "pizzeria", "coffee", "bakery")):
        return "restaurant"
    # Accommodation
    if any(k in c for k in ("hotel", "hostel", "motel", "guest house", "guesthouse", "bnb", "b&b", "lodge", "resort", "campground")):
        return "accommodation"
    # Attractions / museums / sights
    if any(k in c for k in ("attraction", "museum", "gallery", "sight", "landmark", "monument", "zoo", "aquarium", "park", "castle", "cathedral")):
        return "attraction"
    return "general"


# ----------------------------- freshness logic -----------------------------

def _is_stale(ts: Optional[datetime], window_days: int) -> bool:
    if not ts:
        return True
    return ts < (_now() - timedelta(days=window_days))


def compute_freshness(enrichment_row: Optional[Dict[str, Any]], category_name: Optional[str]) -> FreshnessReport:
    """
    Evaluate which fields are fresh/stale/missing per the tech spec and category group.
    Returns a FreshnessReport.
    """
    cat_group = _categorize(category_name)

    # Define required per group
    base_required = ["address", "contact_details", "opening_hours", "description"]
    group_required: List[str] = []
    if cat_group == "restaurant":
        group_required = ["menu", "price_range"]
    elif cat_group == "accommodation":
        group_required = ["price_range", "amenities"]
    elif cat_group == "attraction":
        group_required = ["features", "fees"]
    required = base_required + group_required

    stale: List[str] = []
    missing: List[str] = []
    fresh: List[str] = []

    # Helper to mark a field
    def mark(field_key: str, present: bool, last_updated: Optional[datetime], window: int):
        if not present:
            missing.append(field_key)
        else:
            if _is_stale(last_updated, window):
                stale.append(field_key)
            else:
                fresh.append(field_key)

    e = enrichment_row or {}

    # Address is stored on venues normally, not enrichment; treat as missing here and let caller check venues row.
    # We still surface it in required; /query handler can merge venue+enrichment status.
    # For compute_freshness() we'll consider address present if enrichment has a source that implies address was parsed,
    # but simplest for MVP is to mark "address" as missing and let higher layer check venues.address_full.
    # To avoid false alarms, we won't mark 'address' here; caller handles it.
    required_no_address = [f for f in required if f != "address"]

    # Opening hours
    mark("opening_hours",
         bool(e.get("hours")),
         e.get("hours_last_updated"),
         FRESH_HOURS_DAYS)

    # Contact details
    mark("contact_details",
         bool(e.get("contact_details")),
         e.get("contact_last_updated"),
         FRESH_MENU_CONTACT_PRICE_DAYS)

    # Description
    mark("description",
         bool(e.get("description")),
         e.get("description_last_updated"),
         FRESH_DESC_FEATURES_DAYS)

    # Features (generic)
    mark("features",
         bool(e.get("features")),
         e.get("features_last_updated"),
         FRESH_DESC_FEATURES_DAYS)

    # Restaurants/Bars
    if cat_group == "restaurant":
        # menu considered present if either menu_url or non-empty menu_items
        menu_present = bool(e.get("menu_url")) or (isinstance(e.get("menu_items"), (list, dict)) and len(e.get("menu_items")) > 0)
        mark("menu", menu_present, e.get("menu_last_updated"), FRESH_MENU_CONTACT_PRICE_DAYS)
        mark("price_range", bool(e.get("price_range")), e.get("price_last_updated"), FRESH_MENU_CONTACT_PRICE_DAYS)

    # Accommodation
    if cat_group == "accommodation":
        pr = e.get("accommodation_price_range") or e.get("price_range")
        mark("price_range", bool(pr), e.get("price_last_updated"), FRESH_MENU_CONTACT_PRICE_DAYS)
        amenities = e.get("amenities")
        mark("amenities", bool(amenities), e.get("features_last_updated") or e.get("description_last_updated"), FRESH_DESC_FEATURES_DAYS)

    # Attractions
    if cat_group == "attraction":
        mark("fees", bool(e.get("fees")), e.get("features_last_updated") or e.get("description_last_updated"), FRESH_MENU_CONTACT_PRICE_DAYS)
        mark("features", bool(e.get("attraction_features")) or bool(e.get("features")), e.get("features_last_updated"), FRESH_DESC_FEATURES_DAYS)

    # Collate required-vs-status (excluding 'address' which caller should check from venues)
    required_effective = [f for f in required if f != "address"]
    missing_req = [f for f in missing if f in required_effective]
    stale_req = [f for f in stale if f in required_effective]
    fresh_req = [f for f in fresh if f in required_effective]

    return FreshnessReport(
        fsq_place_id=(enrichment_row or {}).get("fsq_place_id", ""),
        category_group=cat_group,
        required_fields=required,
        stale_fields=sorted(set(stale_req)),
        missing_fields=sorted(set(missing_req)),
        fresh_fields=sorted(set(fresh_req)),
        last_updated=None,  # filled by should_trigger_realtime using venues.last_enriched_at
    )


# ----------------------------- decision helpers -----------------------------

def should_trigger_realtime(fsq_place_id: str, required_fields: Optional[List[str]] = None) -> Tuple[bool, FreshnessReport]:
    """
    Decide whether a realtime crawl should be enqueued for this venue:
      - If enrichment row missing → trigger
      - If any required field is missing or stale → trigger
      - 'address' is checked on the venues table (address_full)
    Returns (trigger_bool, FreshnessReport).
    """
    venue = get_venue(fsq_place_id)
    if not venue:
        # If the venue record itself is missing, something's off. Trigger to attempt recovery anyway.
        fr = FreshnessReport(fsq_place_id, "general", required_fields or [], ["description"], ["opening_hours", "contact_details"], [], None)
        return True, fr
    
    # Don't trigger crawls for venues without websites
    if not venue.get("website") or venue.get("website") == "":
        fr = FreshnessReport(fsq_place_id, "no_website", required_fields or [], [], [], [], None)
        return False, fr

    enr = get_enrichment(fsq_place_id)
    fr = compute_freshness(enr, venue.get("category_name"))
    fr.last_updated = venue.get("last_enriched_at")

    # Address check from venues
    if required_fields is None:
        req = fr.required_fields
    else:
        req = required_fields

    if "address" in req:
        if not (venue.get("address_full") or venue.get("address_components")):
            if "address" not in fr.missing_fields:
                fr.missing_fields.append("address")

    # Restrict to required fields if provided
    missing_req = [f for f in fr.missing_fields if f in req]
    stale_req = [f for f in fr.stale_fields if f in req]

    trigger = (enr is None) or bool(missing_req) or bool(stale_req)
    return trigger, fr


# ----------------------------- selection for background -----------------------------

def _popularity_threshold(percentile: float = 0.9) -> Optional[float]:
    """Compute popularity_confidence percentile (e.g., 0.9 for top 10%). Returns None if not computable."""
    with _get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT percentile_disc(%s) WITHIN GROUP (ORDER BY popularity_confidence) FROM venues WHERE popularity_confidence IS NOT NULL",
                (float(percentile),),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception:
            return None


def select_stale_for_background(limit: int = 200, top_percentile: float = 0.9) -> List[Dict[str, Any]]:
    """
    Return venues that should be refreshed by the background scheduler:
      - Any venue with missing or stale enrichment fields (per windows)
      - PLUS any venue in the top `top_percentile` popularity
    Ordered by (staleness first, then popularity desc, then oldest enrichment).
    """
    now = _now()
    th_hours = now - timedelta(days=FRESH_HOURS_DAYS)
    th_menu = now - timedelta(days=FRESH_MENU_CONTACT_PRICE_DAYS)
    th_other = now - timedelta(days=FRESH_DESC_FEATURES_DAYS)
    pop_thresh = _popularity_threshold(top_percentile)

    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT v.fsq_place_id, v.name, v.category_name, v.latitude, v.longitude,
                   v.popularity_confidence, v.last_enriched_at, v.website,
                   e.hours_last_updated, e.contact_last_updated, e.menu_last_updated,
                   e.price_last_updated, e.description_last_updated, e.features_last_updated
            FROM venues v
            LEFT JOIN enrichment e USING (fsq_place_id)
            WHERE
              -- Only process venues with websites
              v.website IS NOT NULL AND v.website != ''
              AND (
                -- stale/missing tests
                e.fsq_place_id IS NULL
                OR e.hours_last_updated IS NULL OR e.hours_last_updated < %s
                OR e.contact_last_updated IS NULL OR e.contact_last_updated < %s
                OR e.menu_last_updated IS NULL OR e.menu_last_updated < %s
                OR e.price_last_updated IS NULL OR e.price_last_updated < %s
                OR e.description_last_updated IS NULL OR e.description_last_updated < %s
                OR e.features_last_updated IS NULL OR e.features_last_updated < %s
              )
              OR (%s IS NOT NULL AND v.popularity_confidence IS NOT NULL AND v.popularity_confidence >= %s)
            ORDER BY
              -- put stale/missing first by detecting any NULL/old timestamps
              (CASE
                 WHEN e.fsq_place_id IS NULL THEN 0
                 WHEN (e.hours_last_updated IS NULL OR e.hours_last_updated < %s
                    OR e.contact_last_updated IS NULL OR e.contact_last_updated < %s
                    OR e.menu_last_updated IS NULL OR e.menu_last_updated < %s
                    OR e.price_last_updated IS NULL OR e.price_last_updated < %s
                    OR e.description_last_updated IS NULL OR e.description_last_updated < %s
                    OR e.features_last_updated IS NULL OR e.features_last_updated < %s)
                 THEN 0 ELSE 1 END) ASC,
              v.popularity_confidence DESC NULLS LAST,
              COALESCE(e.description_last_updated, e.features_last_updated, e.menu_last_updated,
                       e.price_last_updated, e.contact_last_updated, e.hours_last_updated,
                       v.last_enriched_at) ASC NULLS LAST
            LIMIT %s
            """,
            (
                th_hours, th_menu, th_menu, th_menu, th_other, th_other,                # WHERE stale windows
                pop_thresh, pop_thresh,                                                 # popularity clause
                th_hours, th_menu, th_menu, th_menu, th_other, th_other,                # ORDER BY stale detection
                int(limit),
            ),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def select_stale_near(lat: float, lon: float, radius_m: int = 1000, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Geo-aware variant: find stale venues within `radius_m` meters of (lat, lon).
    Requires PostGIS with a POINT/geometry column on venues (e.g., geometry(Point, 4326)) or latitude/longitude columns.
    This version uses lat/lon with ST_DWithin for accuracy.
    """
    now = _now()
    th_hours = now - timedelta(days=FRESH_HOURS_DAYS)
    th_menu = now - timedelta(days=FRESH_MENU_CONTACT_PRICE_DAYS)
    th_other = now - timedelta(days=FRESH_DESC_FEATURES_DAYS)

    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT v.fsq_place_id, v.name, v.category_name, v.latitude, v.longitude,
                   v.popularity_confidence, v.last_enriched_at, v.website
            FROM venues v
            LEFT JOIN enrichment e USING (fsq_place_id)
            WHERE
              -- Only process venues with websites
              v.website IS NOT NULL AND v.website != ''
              AND ST_DWithin(
                ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
              )
              AND (
                e.fsq_place_id IS NULL
                OR e.hours_last_updated IS NULL OR e.hours_last_updated < %s
                OR e.contact_last_updated IS NULL OR e.contact_last_updated < %s
                OR e.menu_last_updated IS NULL OR e.menu_last_updated < %s
                OR e.price_last_updated IS NULL OR e.price_last_updated < %s
                OR e.description_last_updated IS NULL OR e.description_last_updated < %s
                OR e.features_last_updated IS NULL OR e.features_last_updated < %s
              )
            ORDER BY v.popularity_confidence DESC NULLS LAST
            LIMIT %s
            """,
            (float(lon), float(lat), int(radius_m),
             th_hours, th_menu, th_menu, th_menu, th_other, th_other,
             int(limit)),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


# ----------------------------- website recovery feed -----------------------------

def get_venues_missing_website(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Return venues with NULL/blank website to feed the Website Recovery step.
    Prefer rows that have email (domain heuristic) or social in existing data (if present).
    """
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT fsq_place_id, name, category_name, address_full, address_components,
                   phone, email, website, popularity_confidence
            FROM venues
            WHERE (website IS NULL OR website = '')
            ORDER BY (email IS NOT NULL) DESC, popularity_confidence DESC NULLS LAST, fsq_place_id
            LIMIT %s
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


# ----------------------------- audit / debug helpers -----------------------------

def get_scraped_pages(fsq_place_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent scraped_pages rows for a venue (for debugging/QA)."""
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT page_id, url, page_type, fetched_at, valid_until, http_status,
                   content_type, reason, size_bytes, duration_ms, first_byte_ms
            FROM scraped_pages
            WHERE fsq_place_id = %s
            ORDER BY fetched_at DESC
            LIMIT %s
            """,
            (fsq_place_id, int(limit)),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
