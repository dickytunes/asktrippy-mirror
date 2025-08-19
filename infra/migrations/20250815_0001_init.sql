-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- venues
CREATE TABLE IF NOT EXISTS venues (
  fsq_place_id            TEXT PRIMARY KEY,
  name                    TEXT NOT NULL,
  category_id             TEXT,
  category_name           TEXT,
  latitude                DOUBLE PRECISION NOT NULL,
  longitude               DOUBLE PRECISION NOT NULL,
  address_full            TEXT,
  address_components      JSONB,
  phone                   TEXT,
  email                   TEXT,
  website                 TEXT,
  price_range             TEXT,
  popularity_confidence   DOUBLE PRECISION,
  category_weight         DOUBLE PRECISION,
  last_enriched_at        TIMESTAMPTZ
);

-- helpful indexes
CREATE INDEX IF NOT EXISTS idx_venues_last_enriched_at ON venues (last_enriched_at);
CREATE INDEX IF NOT EXISTS idx_venues_category ON venues (category_name);
CREATE INDEX IF NOT EXISTS idx_venues_name_trgm ON venues USING gin (name gin_trgm_ops);

-- scraped_pages
CREATE TABLE IF NOT EXISTS scraped_pages (
  page_id         BIGSERIAL PRIMARY KEY,
  fsq_place_id    TEXT REFERENCES venues(fsq_place_id),
  url             TEXT NOT NULL,
  page_type       TEXT NOT NULL, -- homepage, menu, hours, contact, about, fees, other
  fetched_at      TIMESTAMPTZ NOT NULL,
  valid_until     TIMESTAMPTZ,
  http_status     INT NOT NULL,
  content_type    TEXT,
  content_hash    TEXT UNIQUE,
  cleaned_text    TEXT,
  -- raw_html      BYTEA, -- optional (not required for MVP)
  source_method   TEXT,   -- direct_url | search_api | heuristic
  redirect_chain  JSONB,
  reason          TEXT,   -- Section 3F reason codes
  size_bytes      INT,
  duration_ms     INT,
  first_byte_ms   INT
);
CREATE INDEX IF NOT EXISTS idx_scraped_pages_fsq_type ON scraped_pages (fsq_place_id, page_type);
CREATE INDEX IF NOT EXISTS idx_scraped_pages_fetched_at ON scraped_pages (fetched_at DESC);

-- enrichment
CREATE TABLE IF NOT EXISTS enrichment (
  fsq_place_id               TEXT PRIMARY KEY REFERENCES venues(fsq_place_id),
  hours                      JSONB,
  contact_details            JSONB,
  description                TEXT,
  features                   JSONB,
  -- category-specific / extras
  menu_url                   TEXT,
  menu_items                 JSONB,
  price_range                TEXT,
  amenities                  JSONB,
  fees                       TEXT,
  -- freshness stamps
  hours_last_updated         TIMESTAMPTZ,
  contact_last_updated       TIMESTAMPTZ,
  description_last_updated   TIMESTAMPTZ,
  menu_last_updated          TIMESTAMPTZ,
  price_last_updated         TIMESTAMPTZ,
  features_last_updated      TIMESTAMPTZ,
  -- traceability
  sources                    JSONB
);
-- indexes for freshness queries
CREATE INDEX IF NOT EXISTS idx_enr_hours_lu       ON enrichment (hours_last_updated);
CREATE INDEX IF NOT EXISTS idx_enr_contact_lu     ON enrichment (contact_last_updated);
CREATE INDEX IF NOT EXISTS idx_enr_desc_lu        ON enrichment (description_last_updated);
CREATE INDEX IF NOT EXISTS idx_enr_menu_lu        ON enrichment (menu_last_updated);
CREATE INDEX IF NOT EXISTS idx_enr_price_lu       ON enrichment (price_last_updated);
CREATE INDEX IF NOT EXISTS idx_enr_features_lu    ON enrichment (features_last_updated);

-- crawl_jobs
CREATE TABLE IF NOT EXISTS crawl_jobs (
  job_id        BIGSERIAL PRIMARY KEY,
  fsq_place_id  TEXT REFERENCES venues(fsq_place_id),
  mode          TEXT NOT NULL,        -- realtime | background
  priority      INT  NOT NULL DEFAULT 5,
  state         TEXT NOT NULL DEFAULT 'pending',  -- pending | running | success | fail
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_state_prio  ON crawl_jobs (state, priority, job_id);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_started_at  ON crawl_jobs (started_at DESC);

-- recovery_candidates
CREATE TABLE IF NOT EXISTS recovery_candidates (
  candidate_id  BIGSERIAL PRIMARY KEY,
  fsq_place_id  TEXT REFERENCES venues(fsq_place_id),
  url           TEXT NOT NULL,
  confidence    DOUBLE PRECISION NOT NULL,
  method        TEXT NOT NULL,   -- email_domain | search | social
  is_chosen     BOOLEAN NOT NULL DEFAULT FALSE
);

-- embeddings (pgvector)
CREATE TABLE IF NOT EXISTS embeddings (
  fsq_place_id  TEXT PRIMARY KEY REFERENCES venues(fsq_place_id),
  vector        VECTOR(384) NOT NULL,
  valid_until   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_embeddings_valid_until ON embeddings (valid_until);
