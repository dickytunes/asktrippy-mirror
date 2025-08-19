#!/usr/bin/env python3
"""
Background worker for Voy8 crawler/enrichment pipeline.

This worker:
1. Claims jobs from the queue (realtime and background modes)
2. Runs the crawler pipeline for each venue
3. Updates the enrichment table with extracted data
4. Marks jobs as success/fail with proper error handling

Usage:
    python -m backend.worker [--workers N] [--batch-size N]

Environment:
    DATABASE_URL - PostgreSQL connection string
    CRAWL_PER_HOST_CONCURRENCY - Per-host crawl limit (default: 2)
    CRAWL_GLOBAL_CONCURRENCY - Global crawl limit (default: 32)
"""

import os
import sys
import time
import signal
import logging
from typing import List, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.crawler.jobs.queue import JobQueue, JobClaim
from backend.crawler.pipeline import CrawlPipeline
from backend.enrichment.schema_org import parse_schema_org
from backend.enrichment.facts_extractor import extract_from_page
from backend.enrichment.unify import build_enrichment
from backend.crawler.io.read import get_venue, get_enrichment
from backend.crawler.io.write import write_scraped_pages, write_enrichment

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_WORKERS = int(os.getenv("WORKER_COUNT", "1"))
DEFAULT_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "8"))
DEFAULT_SLEEP_SECONDS = int(os.getenv("WORKER_SLEEP_SECONDS", "1"))

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
class WorkerStats:
    """Track worker performance metrics."""
    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    total_crawl_time: float = 0.0
    start_time: float = time.time()

    def add_job(self, success: bool, crawl_time: float):
        self.jobs_processed += 1
        if success:
            self.jobs_succeeded += 1
        else:
            self.jobs_failed += 1
        self.total_crawl_time += crawl_time

    def get_stats(self) -> dict:
        uptime = time.time() - self.start_time
        avg_crawl_time = self.total_crawl_time / self.jobs_processed if self.jobs_processed > 0 else 0
        return {
            "uptime_seconds": uptime,
            "jobs_processed": self.jobs_processed,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "success_rate": self.jobs_succeeded / self.jobs_processed if self.jobs_processed > 0 else 0,
            "avg_crawl_time_ms": avg_crawl_time * 1000,
            "jobs_per_minute": (self.jobs_processed / uptime) * 60 if uptime > 0 else 0
        }

def process_job(job: JobClaim, stats: WorkerStats) -> bool:
    """Process a single crawl job."""
    start_time = time.time()
    success = False
    
    try:
        logger.info(f"Processing job {job.job_id} for {job.fsq_place_id} (mode: {job.mode})")
        
        # Get venue info
        venue = get_venue(job.fsq_place_id)
        if not venue:
            raise ValueError(f"Venue {job.fsq_place_id} not found")
        
        # Get or create base URL
        base_url = job.base_url
        if not base_url:
            logger.warning(f"No website for venue {job.fsq_place_id}, skipping")
            return False
        
        # Run crawler pipeline
        pipeline = CrawlPipeline()
        result = pipeline.crawl_site(base_url, deadline_ms=5000)
        
        if not result.pages:
            logger.warning(f"No pages crawled for {job.fsq_place_id}")
            return False
        
        # Write scraped pages to database
        fsq_place_id = job.fsq_place_id
        for page in result.pages:
            page.fsq_place_id = fsq_place_id
        
        write_scraped_pages(result.pages)
        
        # Extract enrichment data
        enrichment_data = {}
        for page in result.pages:
            if page.cleaned_text and page.http_status == 200:
                # Schema.org extraction
                schema_data = parse_schema_org(page.cleaned_text)
                if schema_data:
                    enrichment_data.update(schema_data)
                
                # Facts extraction
                facts = extract_from_page(page)
                if facts:
                    enrichment_data.update(facts)
        
        # Unify and write enrichment
        if enrichment_data:
            existing_enrichment = get_enrichment(fsq_place_id) or {}
            # Build schema_by_url mapping from enrichment_data
            schema_by_url = {}
            for page in result.pages:
                if page.http_status == 200 and page.url:
                    schema_by_url[page.url] = enrichment_data
            
            unified_enrichment, updated_fields = build_enrichment(
                result.pages, 
                schema_by_url
            )
            write_enrichment(fsq_place_id, unified_enrichment)
            
            logger.info(f"Enriched {job.fsq_place_id} with {len(unified_enrichment)} fields")
            success = True
        else:
            logger.warning(f"No enrichment data extracted for {job.fsq_place_id}")
            
    except Exception as e:
        logger.error(f"Error processing job {job.job_id}: {str(e)}", exc_info=True)
        success = False
    
    crawl_time = time.time() - start_time
    stats.add_job(success, crawl_time)
    
    return success

def worker_loop(worker_id: int, stats: WorkerStats):
    """Main worker loop."""
    logger.info(f"Worker {worker_id} starting")
    jq = JobQueue()
    
    while not shutdown_requested:
        try:
            # Claim a batch of jobs
            jobs = jq.claim_batch(limit=DEFAULT_BATCH_SIZE)
            
            if not jobs:
                # No jobs available, sleep briefly
                time.sleep(DEFAULT_SLEEP_SECONDS)
                continue
            
            logger.info(f"Worker {worker_id} claimed {len(jobs)} jobs")
            
            # Process each job
            for job in jobs:
                if shutdown_requested:
                    break
                
                try:
                    success = process_job(job, stats)
                    if success:
                        jq.finish_success(job.job_id)
                    else:
                        jq.finish_fail(job.job_id, error="Processing failed")
                except Exception as e:
                    logger.error(f"Unexpected error in job {job.job_id}: {str(e)}")
                    jq.finish_fail(job.job_id, error=f"Unexpected error: {str(e)[:200]}")
                
                # Log stats periodically
                if stats.jobs_processed % 10 == 0:
                    current_stats = stats.get_stats()
                    logger.info(f"Worker {worker_id} stats: {current_stats}")
                
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {str(e)}", exc_info=True)
            time.sleep(DEFAULT_SLEEP_SECONDS)
    
    logger.info(f"Worker {worker_id} shutting down")

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Voy8 Background Worker")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, 
                       help="Number of worker processes")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                       help="Jobs to claim per batch")
    parser.add_argument("--sleep", type=int, default=DEFAULT_SLEEP_SECONDS,
                       help="Sleep seconds between batches when no jobs")
    
    args = parser.parse_args()
    
    logger.info(f"Starting Voy8 worker with {args.workers} workers, batch size {args.batch_size}")
    
    if args.workers == 1:
        # Single worker mode
        stats = WorkerStats()
        worker_loop(1, stats)
    else:
        # Multi-worker mode (for future scaling)
        import multiprocessing
        
        processes = []
        for i in range(args.workers):
            p = multiprocessing.Process(
                target=worker_loop, 
                args=(i + 1, WorkerStats())
            )
            p.start()
            processes.append(p)
        
        # Wait for all workers
        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            logger.info("Shutting down workers...")
            for p in processes:
                p.terminate()
                p.join()

if __name__ == "__main__":
    main()
