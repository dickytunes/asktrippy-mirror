#!/usr/bin/env python3
"""
Embedding population job for Voy8.

This script:
1. Finds venues with enrichment data but no embeddings
2. Generates embeddings for category + description text
3. Inserts vectors into the embeddings table
4. Can run as a one-off job or continuously

Usage:
    python -m backend.embedding_job [--continuous] [--batch-size N] [--sleep-seconds N]

Environment:
    DATABASE_URL - PostgreSQL connection string
    EMBEDDING_MODEL - HuggingFace model name (default: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
"""

import os
import sys
import time
import signal
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.crawler.io.read import get_venue, get_enrichment

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))
DEFAULT_SLEEP_SECONDS = int(os.getenv("EMBEDDING_SLEEP_SECONDS", "60"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

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
class EmbeddingStats:
    """Track embedding job performance metrics."""
    venues_processed: int = 0
    embeddings_created: int = 0
    embeddings_skipped: int = 0
    errors: int = 0
    start_time: float = time.time()

    def add_result(self, created: bool, error: bool = False):
        self.venues_processed += 1
        if error:
            self.errors += 1
        elif created:
            self.embeddings_created += 1
        else:
            self.embeddings_skipped += 1

    def get_stats(self) -> dict:
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": uptime,
            "venues_processed": self.venues_processed,
            "embeddings_created": self.embeddings_created,
            "embeddings_skipped": self.embeddings_skipped,
            "errors": self.errors,
            "success_rate": (self.embeddings_created + self.embeddings_skipped) / self.venues_processed if self.venues_processed > 0 else 0,
            "venues_per_minute": (self.venues_processed / uptime) * 60 if uptime > 0 else 0
        }

def _get_conn():
    """Get database connection."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)

def _ensure_embedder():
    """Lazy-load the embedding model."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info(f"Loaded embedding model: {EMBEDDING_MODEL}")
        return model
    except ImportError:
        logger.error("sentence-transformers not installed. Install with: pip install sentence-transformers")
        raise
    except Exception as e:
        logger.error(f"Failed to load embedding model: {str(e)}")
        raise

def _embed_text(text: str) -> List[float]:
    """Generate embedding for a single text string."""
    model = _ensure_embedder()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()

def _build_venue_text(venue: Dict[str, Any], enrichment: Dict[str, Any]) -> str:
    """
    Build text representation of venue for embedding.
    
    Combines:
    - Category name
    - Description (if available)
    - Features/amenities (if available)
    - Price range (if available)
    """
    parts = []
    
    # Category is always available
    if venue.get("category_name"):
        parts.append(venue["category_name"])
    
    # Description from enrichment
    if enrichment.get("description"):
        parts.append(enrichment["description"])
    
    # Features/amenities
    features = []
    if enrichment.get("features"):
        features.extend(enrichment["features"])
    if enrichment.get("amenities"):
        features.extend(enrichment["amenities"])
    if features:
        parts.append("Features: " + ", ".join(features[:10]))  # Limit to first 10
    
    # Price range
    if enrichment.get("price_range"):
        parts.append(f"Price: {enrichment['price_range']}")
    
    # Hours (brief)
    if enrichment.get("hours"):
        hours = enrichment["hours"]
        if isinstance(hours, dict):
            open_days = [day for day, times in hours.items() if times]
            if open_days:
                parts.append(f"Open: {', '.join(open_days[:3])}")  # First 3 days
    
    return " | ".join(parts)

def find_venues_needing_embeddings(batch_size: int) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """
    Find venues that have enrichment data but no embeddings.
    
    Returns:
        List of (fsq_place_id, venue_data, enrichment_data) tuples
    """
    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.fsq_place_id, v.name, v.category_name, v.latitude, v.longitude
            FROM venues v
            INNER JOIN enrichment e ON v.fsq_place_id = e.fsq_place_id
            LEFT JOIN embeddings emb ON v.fsq_place_id = emb.fsq_place_id
            WHERE emb.fsq_place_id IS NULL
              AND (e.description IS NOT NULL OR e.features IS NOT NULL OR e.amenities IS NOT NULL)
              AND (e.description != '' OR e.features != '[]'::jsonb OR e.amenities != '[]'::jsonb)
            ORDER BY v.popularity_confidence DESC NULLS LAST
            LIMIT %s
            """,
            (batch_size,)
        )
        
        venues = []
        for row in cur.fetchall():
            fsq_id = row[0]
            venue_data = {
                "name": row[1],
                "category_name": row[2],
                "latitude": row[3],
                "longitude": row[4]
            }
            
            # Get enrichment data
            enrichment = get_enrichment(fsq_id) or {}
            if enrichment:
                venues.append((fsq_id, venue_data, enrichment))
        
        return venues

def create_embedding(fsq_place_id: str, venue: Dict[str, Any], enrichment: Dict[str, Any]) -> bool:
    """
    Create and store embedding for a venue.
    
    Returns:
        True if embedding was created successfully, False otherwise
    """
    try:
        # Build text representation
        venue_text = _build_venue_text(venue, enrichment)
        if not venue_text.strip():
            logger.warning(f"Empty venue text for {fsq_place_id}, skipping")
            return False
        
        # Generate embedding
        vector = _embed_text(venue_text)
        
        # Store in database
        with _get_conn() as conn, conn.cursor() as cur:
            # Check if embeddings table exists
            cur.execute("SELECT to_regclass('public.embeddings')")
            if cur.fetchone()[0] is None:
                logger.error("embeddings table does not exist")
                return False
            
            # Insert embedding
            vec_literal = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
            cur.execute(
                """
                INSERT INTO embeddings (fsq_place_id, vector, valid_until)
                VALUES (%s, %s::vector, NOW() + INTERVAL '30 days')
                ON CONFLICT (fsq_place_id) DO UPDATE
                SET vector = EXCLUDED.vector,
                    valid_until = EXCLUDED.valid_until
                """,
                (fsq_place_id, vec_literal)
            )
            conn.commit()
            
            logger.info(f"Created embedding for {fsq_place_id} ({len(venue_text)} chars)")
            return True
            
    except Exception as e:
        logger.error(f"Error creating embedding for {fsq_place_id}: {str(e)}")
        return False

def process_batch(batch_size: int, stats: EmbeddingStats) -> int:
    """
    Process a batch of venues needing embeddings.
    
    Returns:
        Number of venues processed in this batch
    """
    venues = find_venues_needing_embeddings(batch_size)
    
    if not venues:
        logger.info("No venues found needing embeddings")
        return 0
    
    logger.info(f"Processing {len(venues)} venues for embeddings")
    
    for fsq_id, venue, enrichment in venues:
        if shutdown_requested:
            break
        
        try:
            success = create_embedding(fsq_id, venue, enrichment)
            stats.add_result(success)
        except Exception as e:
            logger.error(f"Unexpected error processing {fsq_id}: {str(e)}")
            stats.add_result(False, error=True)
    
    return len(venues)

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Voy8 Embedding Population Job")
    parser.add_argument("--continuous", action="store_true",
                       help="Run continuously instead of one batch")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                       help="Number of venues to process per batch")
    parser.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS,
                       help="Sleep seconds between batches (continuous mode)")
    
    args = parser.parse_args()
    
    logger.info(f"Starting embedding job (continuous: {args.continuous}, batch size: {args.batch_size})")
    
    stats = EmbeddingStats()
    
    try:
        if args.continuous:
            # Continuous mode
            while not shutdown_requested:
                processed = process_batch(args.batch_size, stats)
                
                if processed == 0:
                    logger.info("No more venues to process, sleeping...")
                    time.sleep(args.sleep_seconds)
                else:
                    # Log stats every batch
                    current_stats = stats.get_stats()
                    logger.info(f"Batch complete. Stats: {current_stats}")
                    
                    # Brief pause between batches
                    time.sleep(1)
        else:
            # One-off mode
            processed = process_batch(args.batch_size, stats)
            current_stats = stats.get_stats()
            logger.info(f"Job complete. Final stats: {current_stats}")
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
    finally:
        final_stats = stats.get_stats()
        logger.info(f"Final stats: {final_stats}")

if __name__ == "__main__":
    main()
