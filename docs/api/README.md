# API Reference

## Overview

The asktrippy API is a FastAPI-based REST service that provides location-based venue discovery and enrichment capabilities.

## Base URL

```
http://localhost:8000
```

## Authentication

Currently, the API does not require authentication.

## Endpoints

### Health & Status

#### GET `/health`
Check the health status of the API and job queue.

**Response:**
```json
{
  "ok": true,
  "db": "ok",
  "queue_depth": 0,
  "version": "0.1.0"
}
```

#### GET `/ready`
Check if the service is ready to handle requests.

**Response:**
```json
{
  "ready": true,
  "db": true,
  "model": true
}
```

### Core Search

#### POST `/query`
Search for venues based on location and query text.

**Request Body:**
```json
{
  "query": "coffee shops",
  "lat": 40.7128,
  "lon": -74.0060,
  "radius_m": 1500,
  "limit": 15,
  "category": "cafe"
}
```

**Parameters:**
- `query` (string, required): Search query text
- `lat` (float, required): Latitude coordinate
- `lon` (float, required): Longitude coordinate
- `radius_m` (integer, optional): Search radius in meters (default: 1500, max: 100000)
- `limit` (integer, optional): Maximum number of results (default: 15, max: 30)
- `category` (string, optional): Filter by category name

**Response:**
```json
{
  "results": [
    {
      "fsq_place_id": "fsq_id_123",
      "name": "Coffee Corner",
      "category_name": "Cafe",
      "latitude": 40.7128,
      "longitude": -74.0060,
      "distance_m": 150,
      "popularity_confidence": 0.85,
      "freshness": {
        "missing": ["hours"],
        "stale": ["reviews"],
        "fresh": ["name", "location"],
        "last_enriched_at": "2024-01-15T10:30:00"
      },
      "sources_count": 3,
      "summary": "Popular coffee shop known for artisanal brews...",
      "job_id": 123
    }
  ]
}
```

### Data Enrichment

#### POST `/scrape`
Enqueue scraping jobs for venue enrichment.

**Request Body:**
```json
{
  "fsq_place_ids": ["fsq_id_123", "fsq_id_456"],
  "mode": "realtime",
  "priority": 10
}
```

**Parameters:**
- `fsq_place_ids` (array, required): List of Foursquare place IDs
- `mode` (string, optional): "realtime" or "background" (default: "realtime")
- `priority` (integer, optional): Job priority 0-10 (default: 10)

**Response:**
```json
{
  "job_ids": [123, 124]
}
```

#### GET `/scrape/{job_id}`
Get the status of a scraping job.

**Response:**
```json
{
  "job_id": 123,
  "state": "completed",
  "started_at": "2024-01-15T10:30:00",
  "finished_at": "2024-01-15T10:35:00",
  "error": null,
  "updated_fields": null
}
```

### AI & Embeddings

#### POST `/embed`
Generate vector embeddings for text.

**Request Body:**
```json
{
  "text": ["coffee shop", "restaurant"],
  "upsert_for_fsq": ["fsq_id_123"],
  "valid_until_days": 30
}
```

**Response:**
```json
{
  "vectors": [[0.1, 0.2, ...], [0.3, 0.4, ...]],
  "dimension": 384
}
```

#### POST `/rank`
Rank venues by relevance to a query.

**Request Body:**
```json
{
  "ids": ["fsq_id_123", "fsq_id_456"],
  "query": "best coffee"
}
```

**Response:**
```json
{
  "ids": ["fsq_id_123", "fsq_id_456"]
}
```

## Data Models

### QueryRequest
```python
class QueryRequest(BaseModel):
    query: str
    lat: float
    lon: float
    radius_m: int = 1500
    limit: int = 15
    category: Optional[str] = None
```

### ResultCard
```python
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
    job_id: Optional[int]
```

## Error Handling

The API returns standard HTTP status codes:

- `200`: Success
- `400`: Bad Request (invalid parameters)
- `404`: Not Found (job not found)
- `500`: Internal Server Error

Error responses include a `detail` field with error information:

```json
{
  "detail": "Empty query"
}
```

## Rate Limiting

Currently, no rate limiting is implemented.

## Development

### Running Locally

```bash
cd backend
uvicorn api:app --reload
```

### API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Testing

```bash
# Test health endpoint
curl http://localhost:8000/health

# Test query endpoint
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{"query": "coffee", "lat": 40.7128, "lon": -74.0060}'
```

## Related Documentation

- [Development Setup](../development/README.md)
- [System Architecture](../architecture/README.md)
- [Database Schema](../database/README.md)
