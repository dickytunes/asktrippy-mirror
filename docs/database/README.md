# Database Schema

## Overview

The asktrippy database uses PostgreSQL with PostGIS extensions for geographic data and pgvector for vector embeddings. The system is designed to store venue information, enrichment data, and vector embeddings for semantic search.

## Database Extensions

### Required Extensions
```sql
-- Geographic data support
CREATE EXTENSION IF NOT EXISTS postgis;

-- Vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;
```

## Core Tables

### Venues Table
Stores basic venue information and geographic data.

```sql
CREATE TABLE venues (
    fsq_place_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    category_name VARCHAR,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    popularity_confidence DOUBLE PRECISION,
    last_enriched_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Geographic index for spatial queries
CREATE INDEX idx_venues_geography ON venues 
USING GIST (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography);

-- Popularity index for ranking
CREATE INDEX idx_venues_popularity ON venues (popularity_confidence DESC NULLS LAST);
```

### Embeddings Table
Stores vector embeddings for semantic search.

```sql
CREATE TABLE embeddings (
    fsq_place_id VARCHAR PRIMARY KEY REFERENCES venues(fsq_place_id),
    vector vector(384) NOT NULL,
    text_source TEXT,
    valid_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vector similarity index
CREATE INDEX idx_embeddings_vector ON embeddings USING ivfflat (vector vector_cosine_ops);
```

### Enrichment Table
Stores enriched venue data from multiple sources.

```sql
CREATE TABLE enrichment (
    fsq_place_id VARCHAR PRIMARY KEY REFERENCES venues(fsq_place_id),
    sources JSONB,
    summary TEXT,
    facts JSONB,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Jobs Table
Tracks asynchronous processing jobs.

```sql
CREATE TABLE jobs (
    id SERIAL PRIMARY KEY,
    fsq_place_id VARCHAR REFERENCES venues(fsq_place_id),
    state VARCHAR NOT NULL DEFAULT 'pending',
    mode VARCHAR NOT NULL DEFAULT 'background',
    priority INTEGER DEFAULT 10,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Job status index
CREATE INDEX idx_jobs_state ON jobs (state);
CREATE INDEX idx_jobs_priority ON jobs (priority DESC);
```

## Data Types

### Geographic Data
- **Coordinates**: Stored as `DOUBLE PRECISION` (latitude, longitude)
- **Geography**: PostGIS `geography` type for spatial operations
- **Distance**: Calculated using PostGIS functions and Haversine formula

### Vector Data
- **Embeddings**: 384-dimensional vectors using pgvector
- **Model**: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
- **Normalization**: Vectors are normalized for cosine similarity

### JSON Data
- **Sources**: Array of data source information
- **Facts**: Extracted factual information
- **Metadata**: Additional venue metadata
- **Freshness**: Data freshness tracking

## Indexes and Performance

### Spatial Indexes
```sql
-- Geography index for radius queries
CREATE INDEX idx_venues_geography ON venues 
USING GIST (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography);

-- Spatial index for distance calculations
CREATE INDEX idx_venues_spatial ON venues 
USING GIST (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326));
```

### Vector Indexes
```sql
-- Vector similarity search
CREATE INDEX idx_embeddings_vector ON embeddings 
USING ivfflat (vector vector_cosine_ops);

-- Alternative: HNSW index for better performance
-- CREATE INDEX idx_embeddings_vector ON embeddings 
-- USING hnsw (vector vector_cosine_ops);
```

### Composite Indexes
```sql
-- Category + location for filtered searches
CREATE INDEX idx_venues_category_location ON venues (category_name, latitude, longitude);

-- Popularity + location for ranking
CREATE INDEX idx_venues_popularity_location ON venues (popularity_confidence DESC, latitude, longitude);
```

## Migrations

### Initial Migration (20250815_0001_init.sql)
The initial migration creates the core table structure and indexes.

```bash
# Run migration
psql -d asktrippy -f infra/migrations/20250815_0001_init.sql
```

## Query Patterns

### Geographic Search
```sql
-- Find venues within radius
SELECT v.*, 
       ST_Distance(
           ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
           ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography
       ) as distance_m
FROM venues v
WHERE ST_DWithin(
    ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
    ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography,
    $3
)
ORDER BY distance_m
LIMIT $4;
```

### Vector Similarity Search
```sql
-- Find similar venues using embeddings
SELECT v.*, (e.vector <=> $1::vector) as distance
FROM venues v
JOIN embeddings e ON v.fsq_place_id = e.fsq_place_id
ORDER BY distance
LIMIT $2;
```

### Popularity + Location Ranking
```sql
-- Rank by popularity within geographic area
SELECT v.*
FROM venues v
WHERE ST_DWithin(
    ST_SetSRID(ST_MakePoint(v.longitude, v.latitude), 4326)::geography,
    ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography,
    $3
)
ORDER BY v.popularity_confidence DESC NULLS LAST
LIMIT $4;
```

## Data Maintenance

### Cleanup Jobs
```sql
-- Remove expired embeddings
DELETE FROM embeddings WHERE valid_until < CURRENT_TIMESTAMP;

-- Clean up old jobs
DELETE FROM jobs WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '30 days';
```

### Vacuum and Analyze
```sql
-- Regular maintenance
VACUUM ANALYZE venues;
VACUUM ANALYZE embeddings;
VACUUM ANALYZE jobs;
```

## Backup and Recovery

### Backup Strategy
```bash
# Full database backup
pg_dump -h localhost -U username -d asktrippy > backup_$(date +%Y%m%d).sql

# Backup with custom format
pg_dump -h localhost -U username -d asktrippy -Fc > backup_$(date +%Y%m%d).dump
```

### Recovery
```bash
# Restore from SQL backup
psql -h localhost -U username -d asktrippy < backup_20240115.sql

# Restore from custom format
pg_restore -h localhost -U username -d asktrippy backup_20240115.dump
```

## Monitoring

### Key Metrics
- **Table sizes**: Monitor growth of venues and embeddings tables
- **Index usage**: Track spatial and vector index performance
- **Query performance**: Monitor slow queries and execution plans
- **Job queue depth**: Track processing backlog

### Useful Queries
```sql
-- Table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Index usage
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```

## Related Documentation

- [Development Setup](../development/README.md)
- [API Reference](../api/README.md)
- [System Architecture](../architecture/README.md)
