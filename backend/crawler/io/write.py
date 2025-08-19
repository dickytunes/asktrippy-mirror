# backend/crawler/io/write.py
# Write-side utilities for Voy8 crawler/enrichment.
#
# What this provides:
# - write_scraped_pages(pages): persist PageRecord objects to scraped_pages
# - write_enrichment(fsq_place_id, data): upsert enrichment row with timestamps
# - update_venue_enrichment(fsq_place_id): set venues.last_enriched_at
#
# Spec alignment:
# - scraped_pages: all fields from PageRecord.to_scraped_pages_row()
# - enrichment: per-field *_last_updated timestamps, sources[] array
# - venues: last_enriched_at updated when enrichment is written
#
# Tables assumed (per tech spec):
#   scraped_pages(page_id, fsq_place_id, url, page_type, fetched_at, valid_until,
#                 http_status, content_type, content_hash, cleaned_text, source_method,
#                 redirect_chain, reason, size_bytes, duration_ms, first_byte_ms)
#   enrichment(fsq_place_id PK, hours JSONB, hours_last_updated TIMESTAMPTZ, ...)
#   venues(fsq_place_id PK, last_enriched_at TIMESTAMPTZ)

from __future__ import annotations

import os
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from ..pipeline import PageRecord


def _get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def write_scraped_pages(pages: List[PageRecord]) -> List[int]:
    """
    Write scraped pages to database.
    
    Returns list of page_id values for inserted rows.
    """
    if not pages:
        return []
    
    with _get_conn() as conn, conn.cursor() as cur:
        page_ids = []
        
        for page in pages:
            # Generate content hash if not provided
            if not page.content_hash and page.cleaned_text:
                page.content_hash = hashlib.sha256(
                    page.cleaned_text.encode('utf-8')
                ).hexdigest()
            
            # Insert page
            cur.execute(
                """
                INSERT INTO scraped_pages (
                    fsq_place_id, url, page_type, fetched_at, valid_until,
                    http_status, content_type, content_hash, cleaned_text,
                    source_method, redirect_chain, reason, size_bytes,
                    duration_ms, first_byte_ms
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) RETURNING page_id
                """,
                (
                    page.fsq_place_id,
                    page.url,
                    page.page_type,
                    page.fetched_at or _now(),
                    page.valid_until,
                    page.http_status,
                    page.content_type,
                    page.content_hash,
                    page.cleaned_text,
                    page.source_method,
                    json.dumps(page.redirect_chain or []),
                    page.reason,
                    page.size_bytes,
                    page.duration_ms,
                    page.first_byte_ms
                )
            )
            page_ids.append(cur.fetchone()[0])
        
        conn.commit()
        return page_ids


def write_enrichment(fsq_place_id: str, data: Dict[str, Any]) -> bool:
    """
    Upsert enrichment data for a venue.
    
    Args:
        fsq_place_id: Venue identifier
        data: Enrichment data dict with optional *_last_updated fields
    
    Returns:
        True if successful, False otherwise
    """
    if not fsq_place_id or not data:
        return False
    
    now = _now()
    
    # Prepare the data with timestamps
    enrichment_data = {}
    timestamp_fields = []
    
    # Map field names to their timestamp counterparts
    field_timestamps = {
        'hours': 'hours_last_updated',
        'contact_details': 'contact_last_updated', 
        'description': 'description_last_updated',
        'menu_url': 'menu_last_updated',
        'menu_items': 'menu_last_updated',
        'price_range': 'price_last_updated',
        'features': 'features_last_updated',
        'amenities': 'features_last_updated',
        'fees': 'fees_last_updated'
    }
    
    for field, value in data.items():
        if field in field_timestamps:
            # This is a data field, add it and its timestamp
            enrichment_data[field] = value
            timestamp_field = field_timestamps[field]
            enrichment_data[timestamp_field] = now
            timestamp_fields.append(timestamp_field)
        elif not field.endswith('_last_updated'):
            # This is a data field without explicit timestamp, add it
            enrichment_data[field] = value
            # Check if it should have a timestamp
            if field in field_timestamps:
                timestamp_field = field_timestamps[field]
                enrichment_data[timestamp_field] = now
                timestamp_fields.append(timestamp_field)
    
    # Ensure sources field exists
    if 'sources' not in enrichment_data:
        enrichment_data['sources'] = []
    
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            # Build dynamic upsert query
            fields = list(enrichment_data.keys())
            placeholders = ['%s'] * len(fields)
            values = [enrichment_data[field] for field in fields]
            
            # Add fsq_place_id to values
            values.insert(0, fsq_place_id)
            
            # Build the ON CONFLICT clause
            conflict_fields = ['fsq_place_id']
            update_clause = ', '.join([f"{field} = EXCLUDED.{field}" for field in fields])
            
            query = f"""
                INSERT INTO enrichment (fsq_place_id, {', '.join(fields)})
                VALUES (%s, {', '.join(placeholders)})
                ON CONFLICT (fsq_place_id) DO UPDATE SET
                {update_clause}
                RETURNING fsq_place_id
            """
            
            cur.execute(query, values)
            result = cur.fetchone()
            
            if result:
                # Update venues.last_enriched_at
                cur.execute(
                    """
                    UPDATE venues 
                    SET last_enriched_at = %s 
                    WHERE fsq_place_id = %s
                    """,
                    (now, fsq_place_id)
                )
                
                conn.commit()
                return True
            
    except Exception as e:
        print(f"Error writing enrichment for {fsq_place_id}: {str(e)}")
        return False
    
    return False


def update_venue_enrichment(fsq_place_id: str) -> bool:
    """
    Update venues.last_enriched_at timestamp.
    
    This is called after successful enrichment to track when the venue
    was last updated.
    """
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE venues 
                SET last_enriched_at = %s 
                WHERE fsq_place_id = %s
                """,
                (_now(), fsq_place_id)
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"Error updating venue enrichment timestamp for {fsq_place_id}: {str(e)}")
        return False


def mark_crawl_job_success(job_id: int, fsq_place_id: str) -> bool:
    """
    Mark a crawl job as successfully completed.
    
    This is a utility function that can be used by the worker
    to update job status after successful processing.
    """
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawl_jobs 
                SET state = 'success', finished_at = %s
                WHERE job_id = %s
                """,
                (_now(), job_id)
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"Error marking job {job_id} as success: {str(e)}")
        return False


def mark_crawl_job_failed(job_id: int, error: str) -> bool:
    """
    Mark a crawl job as failed with error message.
    
    This is a utility function that can be used by the worker
    to update job status after failed processing.
    """
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawl_jobs 
                SET state = 'fail', finished_at = %s, error = %s
                WHERE job_id = %s
                """,
                (_now(), error[:2000], job_id)  # Limit error message length
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        print(f"Error marking job {job_id} as failed: {str(e)}")
        return False
