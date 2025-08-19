# System Architecture

## Overview

asktrippy is a distributed, AI-powered venue discovery platform that combines geographic search, semantic understanding, and real-time data enrichment. The system is designed for scalability, reliability, and high performance.

## High-Level Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   Backend API   │    │   Database      │
│   (Streamlit)   │◄──►│   (FastAPI)     │◄──►│   (PostgreSQL)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                       │
                                ▼                       │
                       ┌─────────────────┐              │
                       │   Job Queue     │              │
                       │   System        │              │
                       └─────────────────┘              │
                                │                       │
                                ▼                       │
                       ┌─────────────────┐              │
                       │   Data Pipeline │              │
                       │   (Scraping,    │              │
                       │    Enrichment)  │              │
                       └─────────────────┘              │
                                │                       │
                                ▼                       │
                       ┌─────────────────┐              │
                       │   AI/LLM       │              │
                       │   Services      │              │
                       └─────────────────┘              │
```

## Core Components

### 1. Frontend Layer
- **Technology**: Streamlit
- **Purpose**: User interface for venue discovery and search
- **Components**:
  - Search interface
  - Map visualization
  - Results display
  - Job status monitoring

### 2. API Layer
- **Technology**: FastAPI
- **Purpose**: RESTful API for venue search and data management
- **Key Features**:
  - Geographic search endpoints
  - Vector similarity search
  - Job management
  - Health monitoring

### 3. Database Layer
- **Technology**: PostgreSQL + PostGIS + pgvector
- **Purpose**: Data persistence and spatial operations
- **Extensions**:
  - PostGIS for geographic data
  - pgvector for vector similarity search
- **Tables**:
  - `venues`: Core venue information
  - `embeddings`: Vector representations
  - `enrichment`: Enriched data
  - `jobs`: Processing queue

### 4. Job Queue System
- **Purpose**: Asynchronous task processing
- **Capabilities**:
  - Web scraping jobs
  - Data enrichment
  - LLM processing
  - Priority-based scheduling

### 5. Data Pipeline
- **Components**:
  - Web crawler
  - Data extractor
  - Enrichment processor
  - Quality gates

### 6. AI/LLM Services
- **Technology**: Sentence Transformers
- **Purpose**: Semantic understanding and content generation
- **Features**:
  - Text embeddings (384-dimensional)
  - Content summarization
  - Fact extraction

## Data Flow

### 1. Search Request Flow
```
User Query → Frontend → API → Geographic Search → Vector Rerank → Results
     ↓
Job Queue ← Freshness Check ← Enrichment Data
```

### 2. Data Enrichment Flow
```
Venue ID → Job Queue → Scraping → Extraction → Enrichment → Database
     ↓
LLM Processing → Summary Generation → Vector Embeddings
```

### 3. Real-time Processing
```
Freshness Check → Trigger Real-time Job → High Priority Queue → Immediate Processing
```

## Technology Stack

### Backend
- **Framework**: FastAPI (Python)
- **Database**: PostgreSQL 13+
- **Extensions**: PostGIS, pgvector
- **AI/ML**: sentence-transformers
- **Async**: asyncio, job queues

### Frontend
- **Framework**: Streamlit
- **Maps**: Integration with mapping services
- **UI**: Modern, responsive design

### Infrastructure
- **Database**: PostgreSQL with spatial extensions
- **Vector Database**: pgvector for embeddings
- **Caching**: In-memory caching for embeddings
- **Monitoring**: Health checks and metrics

## Scalability Considerations

### Horizontal Scaling
- **API Instances**: Multiple FastAPI instances behind load balancer
- **Database**: Read replicas for search queries
- **Job Processing**: Multiple worker processes

### Vertical Scaling
- **Database**: Optimized indexes and query patterns
- **Vector Search**: Efficient pgvector indexing
- **Caching**: Embedding model caching

### Performance Optimizations
- **Spatial Indexes**: PostGIS geography indexes
- **Vector Indexes**: pgvector similarity search
- **Lazy Loading**: Embedding model loaded on demand
- **Connection Pooling**: Database connection management

## Security Architecture

### API Security
- **Input Validation**: Pydantic models for request validation
- **Rate Limiting**: Configurable request limits
- **Error Handling**: Secure error messages

### Data Security
- **Database Access**: Connection string security
- **Data Validation**: Input sanitization
- **Audit Logging**: Job and operation tracking

## Monitoring and Observability

### Health Checks
- **API Health**: `/health` endpoint
- **Readiness**: `/ready` endpoint
- **Database**: Connection status
- **Model Status**: Embedding model availability

### Metrics
- **Performance**: Query response times
- **Queue Depth**: Job queue monitoring
- **Database**: Connection pool status
- **Errors**: Error rates and types

### Logging
- **Structured Logging**: JSON format logs
- **Request Tracking**: Request/response logging
- **Error Logging**: Detailed error information
- **Performance Logging**: Slow query identification

## Deployment Architecture

### Development Environment
- **Local Database**: PostgreSQL with extensions
- **Python Environment**: Virtual environment with dependencies
- **Frontend**: Streamlit development server

### Production Environment
- **Containerization**: Docker containers
- **Orchestration**: Kubernetes or Docker Compose
- **Load Balancing**: Nginx or cloud load balancer
- **Monitoring**: Prometheus, Grafana, or cloud monitoring

## Integration Points

### External Services
- **Foursquare API**: Venue data and categories
- **Web Scraping**: Real-time data collection
- **Mapping Services**: Geographic visualization

### Data Sources
- **Venue Information**: Names, categories, locations
- **User Reviews**: Feedback and ratings
- **Business Data**: Hours, contact information
- **Social Media**: Real-time updates

## Future Architecture Considerations

### Microservices Evolution
- **Service Decomposition**: Split into domain services
- **API Gateway**: Centralized routing and authentication
- **Event Streaming**: Kafka or similar for async communication

### Cloud Native
- **Serverless Functions**: Lambda or similar for processing
- **Managed Services**: RDS, ElastiCache, etc.
- **Auto-scaling**: Automatic resource management

### Advanced AI
- **Model Serving**: Dedicated ML serving infrastructure
- **Feature Store**: Centralized feature management
- **A/B Testing**: Experimentation framework

## Related Documentation

- [Development Setup](../development/README.md)
- [API Reference](../api/README.md)
- [Database Schema](../database/README.md)
- [Data Pipeline](../data/README.md)
