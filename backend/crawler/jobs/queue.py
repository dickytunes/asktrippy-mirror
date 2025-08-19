# backend/crawler/jobs/queue.py
# Postgres-backed crawl job queue for Voy8.
#
# Key points:
# - Uses FOR UPDATE SKIP LOCKED to let many workers safely claim jobs.
# - Enforces per-host concurrency (default 2) by counting running jobs for the same host.
# - Honors priority (0–10) and mode (realtime/background).
# - Records started_at/finished_at and error text; never leaves jobs in limbo.
# - No external brokers; pure SQL with one connection/transaction per operation.
#
# Schema assumptions (from spec, Section 3D):
#   crawl_jobs(job_id PK, fsq_place_id FK → venues, mode TEXT, priority INT,
#              state TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, error TEXT)
#   venues(fsq_place_id PK, website TEXT)
#
# ENV:
#   DATABASE_URL=postgresql://user:pass@host:port/db
#   CRAWL_PER_HOST_CONCURRENCY=2   # per-host cap
#
# Typical usage (worker):
#   q = JobQueue()
#   jobs = q.claim_batch(limit=8)  # returns list of JobClaim with website host parsed
#   for j in jobs:
#       try:
#           ... run pipeline on j.base_url ...
#           q.finish_success(j.job_id)
#       except Exception as e:
#           q.finish_fail(j.job_id, error=str(e)[:2000])
#
# API handler (enqueue):
#   job_id = q.enqueue(fsq_place_id, mode="realtime", priority=10)

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


DEFAULT_PER_HOST_CAP = int(os.getenv("CRAWL_PER_HOST_CONCURRENCY", "2"))


@dataclass
class JobClaim:
    job_id: int
    fsq_place_id: str
    mode: str
    priority: int
    base_url: Optional[str]   # venues.website (may be NULL; pipeline will attempt recovery)
    host: Optional[str]       # parsed host from base_url
    state: str                # should be 'running' after claim
    started_at: str           # ISO str; DB timezone-normalized


class JobQueue:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set")

    # ---------- enqueue ----------

    def enqueue(self, fsq_place_id: str, *, mode: str = "background", priority: int = 5) -> int:
        """Create a pending job (dedupe: if an identical pending job exists, return its id)."""
        if not fsq_place_id or not str(fsq_place_id).strip():
            raise ValueError("enqueue() called without fsq_place_id")

        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            # Try to find an existing *pending* job for the same place+mode
            cur.execute(
                """
                SELECT job_id
                FROM crawl_jobs
                WHERE fsq_place_id = %s AND mode = %s AND state = 'pending'
                ORDER BY priority DESC, job_id ASC
                LIMIT 1
                """,
                (fsq_place_id, mode),
            )
            row = cur.fetchone()
            if row:
                return int(row[0])

            cur.execute(
                """
                INSERT INTO crawl_jobs (fsq_place_id, mode, priority, state)
                VALUES (%s, %s, %s, 'pending')
                RETURNING job_id
                """,
                (fsq_place_id, mode, int(priority)),
            )
            job_id = int(cur.fetchone()[0])
            conn.commit()
            return job_id

    def enqueue_many(self, items: List[Tuple[str, str, int]]) -> List[int]:
        """
        Bulk enqueue: items = [(fsq_place_id, mode, priority), ...]
        Returns job_ids for newly enqueued jobs (existing pendings are skipped).
        """
        job_ids: List[int] = []
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            for fsq_place_id, mode, priority in items:
                if not fsq_place_id or not str(fsq_place_id).strip():
                    raise ValueError("enqueue_many() called with empty fsq_place_id")

                cur.execute(
                    """
                    SELECT job_id FROM crawl_jobs
                    WHERE fsq_place_id = %s AND mode = %s AND state = 'pending'
                    ORDER BY priority DESC, job_id ASC LIMIT 1
                    """,
                    (fsq_place_id, mode),
                )
                row = cur.fetchone()
                if row:
                    job_ids.append(int(row[0]))
                    continue

                cur.execute(
                    """
                    INSERT INTO crawl_jobs (fsq_place_id, mode, priority, state)
                    VALUES (%s, %s, %s, 'pending')
                    RETURNING job_id
                    """,
                    (fsq_place_id, mode, int(priority)),
                )
                job_ids.append(int(cur.fetchone()[0]))
            conn.commit()
        return job_ids

    # ---------- claim / running ----------

    def claim_batch(self, *, limit: int = 8, per_host_cap: Optional[int] = None) -> List[JobClaim]:
        """
        Atomically claim up to `limit` jobs and mark them 'running', respecting per-host concurrency.
        Returns JobClaim rows (includes venue website + parsed host).
        """
        cap = int(per_host_cap) if per_host_cap is not None else DEFAULT_PER_HOST_CAP
        if cap < 1:
            cap = 1

        with psycopg2.connect(self.database_url) as conn:
            conn.autocommit = False
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute(
                f"""
                WITH pending AS (
                  SELECT cj.job_id, cj.fsq_place_id, cj.mode, cj.priority,
                         v.website,
                         lower(split_part(split_part(regexp_replace(v.website, '^https?://', ''), '/', 1), ':', 1)) AS host
                  FROM crawl_jobs cj
                  LEFT JOIN venues v USING (fsq_place_id)
                  WHERE cj.state = 'pending'
                ),
                running_counts AS (
                  SELECT lower(split_part(split_part(regexp_replace(v.website, '^https?://', ''), '/', 1), ':', 1)) AS host,
                         COUNT(*) AS running_now
                  FROM crawl_jobs cj
                  JOIN venues v USING (fsq_place_id)
                  WHERE cj.state = 'running'
                  GROUP BY 1
                ),
                eligible AS (
                  SELECT p.*
                  FROM pending p
                  LEFT JOIN running_counts r ON (p.host = r.host)
                  WHERE
                    p.host IS NULL
                    OR COALESCE(r.running_now, 0) < %s
                  ORDER BY p.priority DESC, p.job_id ASC
                  LIMIT %s
                ),
                marked AS (
                  SELECT e.job_id
                  FROM eligible e
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE crawl_jobs cj
                SET state = 'running', started_at = NOW(), error = NULL
                FROM eligible e
                WHERE cj.job_id = e.job_id
                RETURNING cj.job_id, e.fsq_place_id, e.mode, e.priority, e.website, e.host, cj.state, cj.started_at
                """,
                (cap, int(limit)),
            )
            rows = cur.fetchall()
            conn.commit()

        claims: List[JobClaim] = []
        for r in rows or []:
            claims.append(
                JobClaim(
                    job_id=int(r["job_id"]),
                    fsq_place_id=r["fsq_place_id"],
                    mode=r["mode"],
                    priority=int(r["priority"]),
                    base_url=r["website"],
                    host=r["host"],
                    state=r["state"],
                    started_at=str(r["started_at"]),
                )
            )
        return claims

    # ---------- finishing ----------

    def finish_success(self, job_id: int) -> None:
        """Mark job as success and stamp finished_at."""
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawl_jobs
                SET state='success', finished_at=NOW(), error=NULL
                WHERE job_id=%s AND state='running'
                """,
                (int(job_id),),
            )
            conn.commit()

    def finish_fail(self, job_id: int, *, error: Optional[str] = None) -> None:
        """Mark job as fail with an optional error string (truncated)."""
        err = (error or "").strip()
        if len(err) > 2000:
            err = err[:2000]
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawl_jobs
                SET state='fail', finished_at=NOW(), error=%s
                WHERE job_id=%s AND state='running'
                """,
                (err if err else None, int(job_id)),
            )
            conn.commit()

    # ---------- status & metrics ----------

    def get_status(self, job_id: int) -> Optional[dict]:
        """Return current job status (state, error, timestamps)."""
        with psycopg2.connect(self.database_url) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id, fsq_place_id, mode, priority, state, started_at, finished_at, error
                FROM crawl_jobs
                WHERE job_id=%s
                """,
                (int(job_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def depth(self) -> dict:
        """Return queue depth by state for simple monitoring."""
        with psycopg2.connect(self.database_url) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT state, COUNT(*) AS n
                FROM crawl_jobs
                GROUP BY state
                """
            )
            rows = cur.fetchall()
            return {r["state"]: int(r["n"]) for r in rows}

    def prune_stuck(self, *, max_running_minutes: int = 30) -> int:
        """
        Move jobs stuck in 'running' longer than threshold back to 'pending'.
        Returns number of jobs reset. Use sparingly (e.g., ops runbook).
        """
        with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawl_jobs
                SET state='pending', started_at=NULL, finished_at=NULL, error='reset_stuck'
                WHERE state='running' AND started_at < NOW() - (%s || ' minutes')::interval
                RETURNING job_id
                """,
                (int(max_running_minutes),),
            )
            rows = cur.fetchall()
            conn.commit()
        return len(rows or [])
