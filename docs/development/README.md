# Development Setup

## 🚀 Getting Started

This guide will help you set up the asktrippy development environment.

## 📋 Prerequisites

- Python 3.8+
- PostgreSQL 13+ with PostGIS extension
- pgvector extension
- Node.js (for frontend development)

## 🐍 Backend Setup

### 1. Python Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Unix/MacOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Database Setup
```bash
# Create database
createdb asktrippy

# Enable PostGIS and pgvector extensions
psql -d asktrippy -c "CREATE EXTENSION IF NOT EXISTS postgis;"
psql -d asktrippy -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run migrations
psql -d asktrippy -f infra/migrations/20250815_0001_init.sql
```

### 3. Environment Variables
Create a `.env` file in the backend directory:
```bash
DATABASE_URL=postgresql://username:password@localhost/asktrippy
QUERY_DEFAULT_RADIUS_M=1500
QUERY_MAX_RESULTS=30
MODEL_PREWARM=true
```

### 4. Run Backend
```bash
cd backend
uvicorn api:app --reload
```

## 🎨 Frontend Setup

### 1. Install Dependencies
```bash
cd frontend
npm install
```

### 2. Run Frontend
```bash
npm run dev
```

## 🔧 Development Tools

### Code Quality
- **Linting**: Use your preferred Python linter (flake8, pylint, etc.)
- **Formatting**: Black for Python code formatting
- **Type Checking**: mypy for type annotations

### Testing
```bash
# Run tests
pytest

# Run with coverage
pytest --cov=backend
```

### Database Management
```bash
# Connect to database
psql -d asktrippy

# View tables
\dt

# Check PostGIS functions
\df *postgis*
```

## 📚 Key Development Concepts

### Job Queue System
The system uses an asynchronous job queue for:
- Web scraping
- Data enrichment
- LLM processing

### Vector Embeddings
- Uses sentence-transformers for text embeddings
- pgvector for similarity search
- Lazy loading to avoid startup delays

### Geographic Search
- PostGIS for spatial queries
- Haversine distance calculations
- Radius-based venue discovery

## 🐛 Common Issues

### Database Connection
- Ensure PostgreSQL is running
- Check DATABASE_URL format
- Verify PostGIS and pgvector extensions

### Dependencies
- Update pip: `pip install --upgrade pip`
- Clear cache: `pip cache purge`
- Reinstall: `pip install -r requirements.txt --force-reinstall`

### Vector Operations
- Ensure pgvector extension is installed
- Check vector dimensions match (384 for current model)
- Verify embeddings table exists

## 🔗 Additional Resources

- [API Reference](../api/README.md)
- [Architecture Overview](../architecture/README.md)
- [Tech Spec](../tech-spec/README.md)
