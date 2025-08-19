# Voy8 Background Services

This document explains the background services that power Voy8's real-time enrichment and semantic search capabilities.

## 🏗️ Architecture Overview

Voy8 now has a complete background processing system that includes:

1. **Background Worker** - Processes crawl jobs from the queue
2. **Background Scheduler** - Monitors data freshness and schedules updates
3. **Embedding Job** - Creates semantic vectors for venues
4. **API Server** - Handles real-time requests and enqueues jobs
5. **Frontend** - Streamlit UI with real-time updates

## 🚀 Quick Start

### Option 1: Start Everything at Once
```bash
./start_all_services.sh
```

This will start all services in the correct order and monitor their health.

### Option 2: Start Services Individually
```bash
# Terminal 1: Backend API
./start_backend.sh

# Terminal 2: Background Worker
./start_worker.sh

# Terminal 3: Background Scheduler
./start_scheduler.sh

# Terminal 4: Embedding Job
./start_embedding_job.sh

# Terminal 5: Frontend
./start_frontend.sh
```

### Stop All Services
```bash
./stop_all_services.sh
```

## 🔧 Service Details

### Background Worker (`backend/worker.py`)
- **Purpose**: Processes crawl jobs from the queue
- **What it does**: 
  - Claims jobs (realtime and background modes)
  - Runs crawler pipeline for each venue
  - Extracts enrichment data using schema.org and facts extraction
  - Updates the enrichment table
  - Marks jobs as success/fail
- **Configuration**: 
  - `WORKER_COUNT`: Number of worker processes (default: 1)
  - `WORKER_BATCH_SIZE`: Jobs to claim per batch (default: 8)
  - `WORKER_SLEEP_SECONDS`: Sleep between batches (default: 1)

### Background Scheduler (`backend/scheduler.py`)
- **Purpose**: Monitors data freshness and schedules background updates
- **What it does**:
  - Checks enrichment data freshness windows
  - Enqueues background crawl jobs for stale data
  - Prioritizes high-popularity venues
  - Boosts venues in recent search areas
- **Configuration**:
  - `SCHEDULER_SLEEP_SECONDS`: Time between scheduling cycles (default: 300)
  - `SCHEDULER_BATCH_SIZE`: Max venues to process per cycle (default: 50)
  - `SCHEDULER_TOP_PERCENTILE`: Top % of venues to always refresh (default: 0.9)

### Embedding Job (`backend/embedding_job.py`)
- **Purpose**: Creates semantic vectors for semantic search
- **What it does**:
  - Finds venues with enrichment data but no embeddings
  - Generates embeddings from category + description + features
  - Stores vectors in the embeddings table
  - Can run continuously or as one-off jobs
- **Configuration**:
  - `EMBEDDING_BATCH_SIZE`: Venues to process per batch (default: 100)
  - `EMBEDDING_SLEEP_SECONDS`: Sleep between batches (default: 60)
  - `EMBEDDING_MODEL`: HuggingFace model name (default: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

## 📊 Monitoring & Health Checks

### API Health Endpoint
```bash
curl http://localhost:8000/health
```

Returns:
```json
{
  "ok": true,
  "db": "ok",
  "queue_depth": 0,
  "version": "0.1.0"
}
```

### Queue Health (from Scheduler)
The scheduler logs queue health information:
- Queue depth (pending jobs)
- Running jobs count
- Recent success rate
- Average job duration
- Active worker count

### Worker Statistics
Workers log performance metrics:
- Jobs processed per minute
- Success/failure rates
- Average crawl time
- Uptime statistics

## 🔍 Troubleshooting

### Common Issues

1. **Worker not processing jobs**
   - Check if worker is running: `ps aux | grep backend.worker`
   - Check queue depth: `curl http://localhost:8000/health`
   - Check worker logs for errors

2. **High queue depth**
   - Add more workers: `python -m backend.worker --workers 4`
   - Check if crawler is hitting rate limits
   - Verify database connectivity

3. **No embeddings being created**
   - Check if embedding job is running: `ps aux | grep embedding_job`
   - Verify sentence-transformers is installed: `pip install sentence-transformers`
   - Check if venues have enrichment data

4. **Scheduler not working**
   - Check if scheduler is running: `ps aux | grep backend.scheduler`
   - Verify freshness window configuration
   - Check database permissions

### Logs
All services log to stdout with structured logging:
- Worker: Job processing, success/failure, performance stats
- Scheduler: Queue health, jobs enqueued, venue selection
- Embedding: Venues processed, embeddings created, errors

### Performance Tuning

1. **Increase Worker Count**
   ```bash
   python -m backend.worker --workers 4 --batch-size 16
   ```

2. **Adjust Scheduler Frequency**
   ```bash
   python -m backend.scheduler --sleep-seconds 60 --batch-size 100
   ```

3. **Optimize Embedding Batch Size**
   ```bash
   python -m backend.embedding_job --continuous --batch-size 200
   ```

## 🎯 Expected Performance

With the background services running:

- **Search Response Time**: <3s for cached results, <10s for fresh scraping
- **Background Enrichment**: Continuous updates keeping data fresh
- **Semantic Search**: Vector-based similarity search when embeddings exist
- **Queue Processing**: Jobs processed within seconds of being enqueued

## 🔄 Data Flow

1. **User Search** → API enqueues realtime crawl if data is stale
2. **Worker** → Claims job, runs crawler, extracts enrichment
3. **Scheduler** → Monitors freshness, enqueues background jobs
4. **Embedding Job** → Creates vectors for enriched venues
5. **API** → Returns results with semantic search when possible

## 🚨 Important Notes

- **Database Required**: All services require `DATABASE_URL` environment variable
- **Dependencies**: Install `sentence-transformers` for embeddings
- **Concurrency**: Workers respect per-host and global crawl limits
- **Graceful Shutdown**: All services handle SIGINT/SIGTERM properly
- **Error Handling**: Failed jobs are logged with detailed error messages

## 📈 Scaling

The system is designed to scale horizontally:

- **Multiple Workers**: Run multiple worker instances on different machines
- **Load Balancing**: Use a load balancer for multiple API instances
- **Database**: Scale PostgreSQL with read replicas
- **Caching**: Add Redis for job queue and result caching

## 🔗 Related Files

- `backend/worker.py` - Background worker implementation
- `backend/scheduler.py` - Background scheduler implementation  
- `backend/embedding_job.py` - Embedding population job
- `backend/crawler/io/write.py` - Database write operations
- `start_all_services.sh` - Complete system startup script
- `stop_all_services.sh` - Complete system shutdown script
