# backend/crawler/jobs/status.py
# Job state machine helpers for crawl_jobs (pending → running → success|fail).
# Provides: set_state(...), get(...), counts(), recent_failures().
#
from __future__ import annotations

import os
from typing import Optional, Dict, Any, List
import psycopg2
from psycopg2.extras import RealDictCursor

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_SUCCESS = "success"
STATE_FAIL    = "fail"

VALID_NEXT = {
    STATE_PENDING: {STATE_RUNNING},
    STATE_RUNNING: {STATE_SUCCESS, STATE_FAIL},
    STATE_SUCCESS: set(),
    STATE_FAIL: set(),
}

def _get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)

def get(job_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM crawl_jobs WHERE job_id=%s", (int(job_id),))
        row = cur.fetchone()
        return dict(row) if row else None

def set_state(job_id: int, new_state: str, *, error: Optional[str] = None) -> bool:
    cur_state = get(job_id)
    if not cur_state:
        return False
    prev = cur_state["state"]
    if new_state not in VALID_NEXT.get(prev, set()):
        return False

    with _get_conn() as conn, conn.cursor() as cur:
        if new_state == STATE_RUNNING:
            cur.execute(
                "UPDATE crawl_jobs SET state=%s, started_at=NOW(), error=NULL WHERE job_id=%s",
                (STATE_RUNNING, int(job_id)),
            )
        elif new_state == STATE_SUCCESS:
            cur.execute(
                "UPDATE crawl_jobs SET state=%s, finished_at=NOW(), error=NULL WHERE job_id=%s AND state=%s",
                (STATE_SUCCESS, int(job_id), STATE_RUNNING),
            )
        else:  # fail
            err = (error or "").strip()[:2000] or None
            cur.execute(
                "UPDATE crawl_jobs SET state=%s, finished_at=NOW(), error=%s WHERE job_id=%s AND state=%s",
                (STATE_FAIL, err, int(job_id), STATE_RUNNING),
            )
        conn.commit()
    return True

def counts() -> Dict[str, int]:
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT state, COUNT(*) AS n FROM crawl_jobs GROUP BY state")
        rows = cur.fetchall()
        return {r["state"]: int(r["n"]) for r in rows}

def recent_failures(limit: int = 20) -> List[Dict[str, Any]]:
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT job_id, fsq_place_id, mode, priority, started_at, finished_at, error
            FROM crawl_jobs
            WHERE state='fail'
            ORDER BY finished_at DESC NULLS LAST, job_id DESC
            LIMIT %s
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
