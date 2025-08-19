#!/usr/bin/env python3
"""
Background scheduler for Voy8 enrichment freshness.

This scheduler:
1. Continuously monitors enrichment data freshness
2. Enqueues background crawl jobs for stale data
3. Prioritizes high-popularity venues and recent search areas
4. Respects rate limits and concurrency constraints

Usage:
    python -m backend.scheduler [--sleep-seconds N] [--batch-size N]

Environment:
    DATABASE_URL - PostgreSQL connection string
    FRESH_HOURS_DAYS - Hours freshness window (default: 3)
    FRESH_MENU_CONTACT_PRICE_DAYS - Menu/contact/price freshness (default: 14)
    FRESH_DESC_FEATURES_DAYS - Description/features freshness (default: 30)
    CRAWL_PER_HOST_CONCURRENCY - Per-host crawl limit (default: 2)
"""

import os
import sys
import time
import signal
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.crawler.jobs.queue import JobQueue
from backend.crawler.io.read import select_stale_for_background, select_stale_near

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_SLEEP_SECONDS = int(os.getenv("SCHEDULER_SLEEP_SECONDS", "300"))  # 5 minutes
DEFAULT_BATCH_SIZE = int(os.getenv("SCHEDULER_BATCH_SIZE", "50"))
DEFAULT_TOP_PERCENTILE = float(os.getenv("SCHEDULER_TOP_PERCENTILE", "0.9"))

# Freshness windows (from tech spec)
FRESH_HOURS_DAYS = int(os.getenv("FRESH_HOURS_DAYS", "3"))
FRESH_MENU_CONTACT_PRICE_DAYS = int(os.getenv("FRESH_MENU_CONTACT_PRICE_DAYS", "14"))
FRESH_DESC_FEATURES_DAYS = int(os.getenv("FRESH_DESC_FEATURES_DAYS", "30"))

# Graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@dataclass
class SchedulerStats:
    """Track scheduler performance metrics."""
    cycles_run: int = 0
    jobs_enqueued: int = 0
    venues_processed: int = 0
    start_time: float = time.time()

    def add_cycle(self, jobs_enqueued: int, venues_processed: int):
        self.cycles_run += 1
        self.jobs_enqueued += jobs_enqueued
        self.venues_processed += venues_processed

    def get_stats(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": uptime,
            "cycles_run": self.cycles_run,
            "jobs_enqueued": self.jobs_enqueued,
            "venues_processed": self.venues_processed,
            "jobs_per_cycle": self.jobs_enqueued / self.cycles_run if self.cycles_run > 0 else 0,
            "cycles_per_hour": (self.cycles_run / uptime) * 3600 if uptime > 0 else 0
        }

def _get_conn():
    """Get database connection."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)

def get_recent_search_areas() -> List[Dict[str, Any]]:
    """
    Get recent search areas to boost venues in those locations.
    
    This is a simplified version - in production you might want to
    track actual user search patterns and boost venues in those areas.
    """
    # For MVP, we'll use a simple approach: boost venues in major cities
    # In production, this could analyze actual search logs
    major_cities = [
        {"lat": 51.5074, "lon": -0.1278, "radius_m": 50000, "name": "London"},
        {"lat": 52.5200, "lon": 13.4050, "radius_m": 50000, "name": "Berlin"},
        {"lat": 48.8566, "lon": 2.3522, "radius_m": 50000, "name": "Paris"},
        {"lat": 40.4168, "lon": -3.7038, "radius_m": 50000, "name": "Madrid"},
        {"lat": 41.9028, "lon": 12.4964, "radius_m": 50000, "name": "Rome"},
    ]
    
    return major_cities

def schedule_background_jobs(batch_size: int, stats: SchedulerStats) -> int:
    """
    Schedule background crawl jobs for stale enrichment data.
    
    Returns:
        Number of jobs enqueued
    """
    jq = JobQueue()
    jobs_enqueued = 0
    
    try:
        # 1. Get stale venues by freshness windows
        stale_venues = select_stale_for_background(
            limit=batch_size // 2,  # Reserve half for geo-boosted venues
            top_percentile=DEFAULT_TOP_PERCENTILE
        )
        
        # 2. Get recent search area venues
        recent_areas = get_recent_search_areas()
        geo_boosted_venues = []
        
        for area in recent_areas:
            area_venues = select_stale_near(
                lat=area["lat"],
                lon=area["lon"], 
                radius_m=area["radius_m"],
                limit=batch_size // len(recent_areas)
            )
            geo_boosted_venues.extend(area_venues)
        
        # Combine and deduplicate
        all_venues = stale_venues + geo_boosted_venues
        unique_venues = {v["fsq_place_id"]: v for v in all_venues}.values()
        
        # Sort by priority (stale first, then geo-boosted)
        priority_venues = list(unique_venues)[:batch_size]
        
        logger.info(f"Found {len(priority_venues)} venues needing background refresh")
        
        # 3. Enqueue background jobs
        for venue in priority_venues:
            fsq_id = venue["fsq_place_id"]
            
            # Check if venue already has a pending/running job
            with _get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM crawl_jobs 
                    WHERE fsq_place_id = %s AND state IN ('pending', 'running')
                    """,
                    (fsq_id,)
                )
                existing_jobs = cur.fetchone()[0]
            
            if existing_jobs == 0:
                # Enqueue background job with lower priority
                job_id = jq.enqueue(fsq_id, mode="background", priority=5)
                if job_id:
                    jobs_enqueued += 1
                    logger.debug(f"Enqueued background job {job_id} for {fsq_id}")
            else:
                logger.debug(f"Venue {fsq_id} already has {existing_jobs} active jobs")
        
        stats.add_cycle(jobs_enqueued, len(priority_venues))
        logger.info(f"Scheduled {jobs_enqueued} background jobs from {len(priority_venues)} venues")
        
    except Exception as e:
        logger.error(f"Error scheduling background jobs: {str(e)}", exc_info=True)
    
    return jobs_enqueued

def check_queue_health() -> Dict[str, Any]:
    """
    Check the health of the job queue and worker system.
    
    Returns:
        Dict with queue depth, worker status, and recommendations
    """
    try:
        with _get_conn() as conn, conn.cursor() as cur:
            # Queue depth by state
            cur.execute(
                """
                SELECT state, COUNT(*) as count
                FROM crawl_jobs 
                GROUP BY state
                """
            )
            state_counts = dict(cur.fetchall())
            
            # Recent job performance
            cur.execute(
                """
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN state = 'success' THEN 1 END) as success,
                    COUNT(CASE WHEN state = 'fail' THEN 1 END) as failed,
                    AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) as avg_duration
                FROM crawl_jobs 
                WHERE started_at > NOW() - INTERVAL '1 hour'
                  AND state IN ('success', 'fail')
                """
            )
            recent_stats = cur.fetchone()
            
            # Worker activity (jobs started in last 10 minutes)
            cur.execute(
                """
                SELECT COUNT(*) 
                FROM crawl_jobs 
                WHERE started_at > NOW() - INTERVAL '10 minutes'
                  AND state = 'running'
                """
            )
            active_workers = cur.fetchone()[0]
            
            return {
                "queue_depth": state_counts.get("pending", 0),
                "running_jobs": state_counts.get("running", 0),
                "recent_success_rate": recent_stats[1] / recent_stats[0] if recent_stats[0] > 0 else 0,
                "recent_avg_duration": recent_stats[3] or 0,
                "active_workers": active_workers,
                "recommendations": []
            }
            
    except Exception as e:
        logger.error(f"Error checking queue health: {str(e)}")
        return {"error": str(e)}

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Voy8 Background Scheduler")
    parser.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS,
                       help="Sleep seconds between scheduling cycles")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                       help="Maximum venues to process per cycle")
    
    args = parser.parse_args()
    
    logger.info(f"Starting Voy8 scheduler (sleep: {args.sleep_seconds}s, batch size: {args.batch_size})")
    logger.info(f"Freshness windows: hours={FRESH_HOURS_DAYS}d, menu/contact/price={FRESH_MENU_CONTACT_PRICE_DAYS}d, desc/features={FRESH_DESC_FEATURES_DAYS}d")
    
    stats = SchedulerStats()
    
    try:
        while not shutdown_requested:
            cycle_start = time.time()
            
            try:
                # Schedule background jobs
                jobs_enqueued = schedule_background_jobs(args.batch_size, stats)
                
                # Check queue health
                health = check_queue_health()
                if "error" not in health:
                    logger.info(f"Queue health: {health}")
                    
                    # Log recommendations
                    if health["queue_depth"] > 100:
                        logger.warning("High queue depth - consider adding more workers")
                    if health["recent_success_rate"] < 0.8:
                        logger.warning("Low success rate - check worker logs")
                    if health["active_workers"] == 0:
                        logger.warning("No active workers - start the worker process")
                
                # Log stats periodically
                if stats.cycles_run % 10 == 0:
                    current_stats = stats.get_stats()
                    logger.info(f"Scheduler stats: {current_stats}")
                
            except Exception as e:
                logger.error(f"Error in scheduling cycle: {str(e)}", exc_info=True)
            
            # Sleep until next cycle
            cycle_duration = time.time() - cycle_start
            sleep_time = max(0, args.sleep_seconds - cycle_duration)
            
            if sleep_time > 0:
                logger.debug(f"Sleeping for {sleep_time:.1f}s until next cycle")
                time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
    finally:
        final_stats = stats.get_stats()
        logger.info(f"Final stats: {final_stats}")

if __name__ == "__main__":
    main()
