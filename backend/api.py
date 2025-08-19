# backend/api.py
# FastAPI app implementing the SSOT routes:
#   POST /query   — vector+geo search, freshness check, enqueue realtime crawl if stale
#   POST /embed   — embedding API (local HF model per Section 8A), optional upsert to DB
#   POST /scrape  — enqueue explicit crawl job(s)
#   GET  /scrape/{job_id} — poll job status
#   POST /rank    — reranker (MVP: pass-through / optional)
#   GET  /health  — liveness/readiness
#
# Depends on modules shipped earlier in this chat:
#   backend/crawler/jobs/queue.py       (JobQueue)
#   backend/crawler/io/read.py          (freshness & lookups)
#   backend/crawler/io/write.py         (not used by API; worker persists)
#   backend/enrichment/llm_summary.py   (Safe RAG summary formatter)
#
# Notes:
# - Uses pgvector if 'embeddings' table exists; falls back to popularity sort if not.
# - Uses PostGIS ST_DWithin on (longitude, latitude) as per Section 3E examples.
# - Enqueues realtime crawl when required fields are missing/stale (Section 3D triggers).
# - No worker here; this API never runs crawls inline (per SSOT).
#

from __future__ import annotations

import os
import math
from typing import List, Optional, Dict, Any, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from crawler.jobs.queue import JobQueue
from crawler.io.read import (
    should_trigger_realtime,
    get_venue,
    get_enrichment,
)
from enrichment.llm_summary import summarize

# Load environment variables from .env file
load_dotenv()


# ------------------------ config ------------------------
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL is not set")

DEFAULT_RADIUS_M = int(os.getenv("QUERY_DEFAULT_RADIUS_M", "1500"))
MAX_RESULTS = int(os.getenv("QUERY_MAX_RESULTS", "30"))

# Embedding model (local, per Section 8A)
EMB_DIM = 384
_EMBEDDER = None  # lazy-loaded


def _ensure_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer  # install: sentence-transformers
        _EMBEDDER = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    return _EMBEDDER


def _embed(texts: List[str]) -> List[List[float]]:
    model = _ensure_embedder()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in (vecs if hasattr(vecs, "__iter__") else [vecs])]


def _pg():
    """Create database connection with timeout settings to prevent hanging queries"""
    conn = psycopg2.connect(DB_URL)
    conn.set_session(autocommit=False)
    # Set statement timeout to prevent 60-second hangs
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30s'")
        cur.execute("SET lock_timeout = '10s'")
    return conn


# ------------------------ request/response models ------------------------
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    lat: float
    lon: float
    radius_m: int = Field(DEFAULT_RADIUS_M, ge=50, le=100_000)
    limit: int = Field(15, ge=1, le=MAX_RESULTS)
    category: Optional[str] = None  # optional filter


class ResultCard(BaseModel):
    fsq_place_id: str
    name: str
    category_name: Optional[str]
    latitude: float
    longitude: float
    distance_m: int
    popularity_confidence: Optional[float]
    freshness: Dict[str, Any]
    sources_count: int
    summary: Optional[str]
    job_id: Optional[int]  # present if a realtime crawl was enqueued


class QueryResponse(BaseModel):
    results: List[ResultCard]


class EmbedRequest(BaseModel):
    text: List[str] = Field(..., min_items=1)
    upsert_for_fsq: Optional[List[str]] = None  # optional: same length as text
    valid_until_days: int = 30


class EmbedResponse(BaseModel):
    vectors: List[List[float]]
    dimension: int


class ScrapeRequest(BaseModel):
    fsq_place_ids: List[str] = Field(..., min_items=1)
    mode: str = Field("realtime", pattern="^(realtime|background)$")
    priority: int = Field(10, ge=0, le=10)


class ScrapeResponse(BaseModel):
    job_ids: List[int]


class RankRequest(BaseModel):
    ids: List[str] = Field(..., min_items=1)
    query: str


class RankResponse(BaseModel):
    ids: List[str]


# ------------------------ app ------------------------
app = FastAPI(title="Voy8 API", version="0.1.0")


# ------------------------ helpers ------------------------
def _embeddings_table_exists() -> bool:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_name='embeddings'
            )
            """
        )
        return bool(cur.fetchone()[0])


def _select_candidates_by_geo(
    conn, lat: float, lon: float, radius_m: int, limit: int, category: Optional[str]
) -> List[Dict[str, Any]]:
    """Optimized geo search with better query structure"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Use a more efficient query structure with proper indexing hints
        category_filter = "AND LOWER(v.category_name) LIKE LOWER(%s)" if category else ""
        category_param = (f"%{category}%",) if category else ()
        
        # Use a more efficient approach: first get candidates within bounding box, then filter by exact distance
        # This leverages the spatial index better than ST_DWithin alone
        cur.execute(
            f"""
            WITH geo_candidates AS (
                SELECT v.fsq_place_id, v.name, v.category_name, v.latitude, v.longitude,
                       v.popularity_confidence,
                       ST_Distance(
                           ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                       ) as distance_m
                FROM venues v
                WHERE
                  -- Use bounding box first for better spatial index usage
                  ST_Intersects(
                    ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
                    ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
                  )
                  AND v.website IS NOT NULL 
                  AND v.website != ''
                  {category_filter}
            )
            SELECT fsq_place_id, name, category_name, latitude, longitude, popularity_confidence
            FROM geo_candidates
            WHERE distance_m <= %s
            ORDER BY popularity_confidence DESC NULLS LAST, distance_m ASC
            LIMIT %s
            """,
            (float(lon), float(lat), float(lon), float(lat), int(radius_m))
            + category_param
            + (int(radius_m), int(limit)),
        )
        return [dict(r) for r in cur.fetchall()]


def _get_venues_and_enrichment_batch(
    conn, fsq_ids: List[str]
) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """Batch fetch venues and enrichment data to reduce database calls"""
    if not fsq_ids:
        return {}, {}
    
    # Batch fetch venues
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        placeholders = ','.join(['%s'] * len(fsq_ids))
        cur.execute(
            f"""
            SELECT fsq_place_id, name, category_name, latitude, longitude,
                   popularity_confidence, last_enriched_at, website
            FROM venues
            WHERE fsq_place_id IN ({placeholders})
            """,
            fsq_ids
        )
        venues = {row['fsq_place_id']: dict(row) for row in cur.fetchall()}
    
    # Batch fetch enrichment data
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        placeholders = ','.join(['%s'] * len(fsq_ids))
        cur.execute(
            f"""
            SELECT fsq_place_id, description, hours, contact_details, features,
                   menu_url, menu_items, price_range, accommodation_price_range,
                   amenities, fees, attraction_features, sources,
                   description_last_updated, hours_last_updated, contact_last_updated,
                   features_last_updated, menu_last_updated, price_last_updated,
                   amenities_last_updated, fees_last_updated, attraction_features_last_updated
            FROM enrichment
            WHERE fsq_place_id IN ({placeholders})
            """,
            fsq_ids
        )
        enrichment = {row['fsq_place_id']: dict(row) for row in cur.fetchall()}
    
    return venues, enrichment


def _semantic_rerank(
    conn, query_vec: List[float], candidates: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    ids = tuple([c["fsq_place_id"] for c in candidates])
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.fsq_place_id, (e.vector <=> %s::vector) AS distance
            FROM embeddings e
            WHERE e.fsq_place_id IN %s
            """,
            (vec_literal, ids),
        )
        dmap = {r["fsq_place_id"]: float(r["distance"]) for r in cur.fetchall()}
    for c in candidates:
        c["distance"] = dmap.get(c["fsq_place_id"], 0.5)
    return sorted(
        candidates,
        key=lambda x: (x["distance"], -(x.get("popularity_confidence") or 0.0)),
    )[:limit]


def _distance_haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ------------------------ routes ------------------------
@app.post("/query", response_model=QueryResponse)
def post_query(req: QueryRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Empty query")

    use_vectors = _embeddings_table_exists()
    qvec = _embed([req.query])[0] if use_vectors else None

    with _pg() as conn:
        # Get geo candidates
        candidates = _select_candidates_by_geo(
            conn, req.lat, req.lon, req.radius_m, req.limit, req.category
        )

        if use_vectors and qvec is not None:
            candidates = _semantic_rerank(conn, qvec, candidates, req.limit)
        else:
            candidates = candidates[: req.limit]

        # Batch fetch all venue and enrichment data
        fsq_ids = [c["fsq_place_id"] for c in candidates]
        venues, enrichment = _get_venues_and_enrichment_batch(conn, fsq_ids)

        jq = JobQueue()
        cards: List[ResultCard] = []
        
        for c in candidates:
            fsq_id = c["fsq_place_id"]
            ven = venues.get(fsq_id, {})
            enr = enrichment.get(fsq_id, {})
            
            dist = int(
                round(
                    _distance_haversine_m(
                        req.lat, req.lon, c["latitude"], c["longitude"]
                    )
                )
            )

            trigger, fres = should_trigger_realtime(fsq_id)
            job_id = (
                jq.enqueue(fsq_id, mode="realtime", priority=10) if trigger else None
            )

            try:
                summary = summarize(ven, enr) if enr else None
            except Exception:
                summary = None

            cards.append(
                ResultCard(
                    fsq_place_id=fsq_id,
                    name=ven.get("name") or c["name"],
                    category_name=ven.get("category_name") or c.get("category_name"),
                    latitude=float(c["latitude"]),
                    longitude=float(c["longitude"]),
                    distance_m=dist,
                    popularity_confidence=c.get("popularity_confidence"),
                    freshness={
                        "missing": fres.missing_fields,
                        "stale": fres.stale_fields,
                        "fresh": fres.fresh_fields,
                        "last_enriched_at": (
                            ven.get("last_enriched_at").isoformat()
                            if ven.get("last_enriched_at")
                            else None
                        ),
                    },
                    sources_count=len((enr.get("sources") or [])) if enr else 0,
                    summary=summary,
                    job_id=job_id,
                )
            )

        return QueryResponse(results=cards)


@app.post("/embed", response_model=EmbedResponse)
def post_embed(req: EmbedRequest):
    vecs = _embed(req.text)
    if req.upsert_for_fsq:
        if len(req.upsert_for_fsq) != len(req.text):
            raise HTTPException(
                status_code=400, detail="upsert_for_fsq must match length of text"
            )
        with _pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.embeddings')")
            if cur.fetchone()[0] is None:
                raise HTTPException(status_code=500, detail="embeddings table missing")
            for fsq_place_id, vec in zip(req.upsert_for_fsq, vecs):
                vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                cur.execute(
                    """
                    INSERT INTO embeddings (fsq_place_id, vector, valid_until)
                    VALUES (%s, %s::vector, NOW() + (%s || ' days')::interval)
                    ON CONFLICT (fsq_place_id) DO UPDATE
                    SET vector = EXCLUDED.vector,
                        valid_until = EXCLUDED.valid_until
                    """,
                    (fsq_place_id, vec_literal, int(req.valid_until_days)),
                )
            conn.commit()
    return EmbedResponse(vectors=vecs, dimension=len(vecs[0]) if vecs else EMB_DIM)


# ------------------------ FIXED SCRAPE ------------------------
@app.post("/scrape", response_model=ScrapeResponse)
def post_scrape(req: ScrapeRequest):
    jq = JobQueue()
    if not req.fsq_place_ids:
        raise HTTPException(status_code=400, detail="fsq_place_ids required")

    job_ids = jq.enqueue_many(
        [(fsq_id, req.mode, req.priority) for fsq_id in req.fsq_place_ids]
    )
    return ScrapeResponse(job_ids=job_ids)


@app.get("/scrape/{job_id}")
def get_scrape(job_id: int):
    jq = JobQueue()
    st = jq.get_status(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="job not found")

    resp = {
        "job_id": st["job_id"],
        "state": st["state"],
        "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"),
        "error": st.get("error"),
        "updated_fields": None,
        "enrichment": None,
    }

    if st["state"] == "success" and st.get("fsq_place_id"):
        fsq_id = st["fsq_place_id"]
        enr = get_enrichment(fsq_id)
        if enr:
            resp["enrichment"] = enr
            resp["updated_fields"] = list(enr.keys())

    return resp


@app.post("/rank", response_model=RankResponse)
def post_rank(req: RankRequest):
    return RankResponse(ids=req.ids)


@app.get("/health")
def get_health():
    try:
        with _pg() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            _ = cur.fetchone()
        ok_db = True
    except Exception:
        ok_db = False

    from crawler.jobs.queue import JobQueue

    jq = JobQueue()
    depth = jq.depth()
    return {
        "ok": ok_db,
        "db": "ok" if ok_db else "fail",
        "queue_depth": depth,
        "version": "0.1.0",
    }
