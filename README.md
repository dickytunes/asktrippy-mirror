# asktrippy

A location-based venue discovery and enrichment platform powered by AI and geographic search.

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 13+ with PostGIS extension
- pgvector extension

### 1. Setup Environment
```bash
# Create and activate virtual environment
python -m venv .venv

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
Create a `.env` file in the project root:
```bash
DATABASE_URL=postgresql://username:password@localhost/asktrippy
QUERY_DEFAULT_RADIUS_M=1500
QUERY_MAX_RESULTS=30
MODEL_PREWARM=true
```

### 4. Start Backend
```bash
# Option 1: Using the startup script
# Windows:
start_backend.bat
# Unix/MacOS:
./start_backend.sh

# Option 2: Manual start
cd backend
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

### 5. Start Frontend
```bash
# Option 1: Using the startup script
# Windows:
start_frontend.bat
# Unix/MacOS:
cd frontend
streamlit run app.py --server.port 8501

# Option 2: Manual start
cd frontend
streamlit run app.py --server.port 8501
```

### 6. Access the Application
- **Backend API**: http://localhost:8000
- **Frontend**: http://localhost:8501
- **API Docs**: http://localhost:8000/docs

## 🏗️ Project Overview

asktrippy (Voy8 API) is a sophisticated platform that combines:

- **Geographic Search** - Location-based venue discovery using PostGIS
- **AI-Powered Enrichment** - LLM-based content summarization and analysis
- **Real-time Data Collection** - Web scraping and Foursquare integration
- **Vector Search** - Semantic search capabilities using embeddings
- **Category Intelligence** - Automated venue classification and clustering

## 🏛️ Architecture

- **Backend**: FastAPI with PostgreSQL + PostGIS + pgvector
- **Frontend**: Streamlit-based user interface
- **Data Pipeline**: Asynchronous job queue for enrichment
- **AI Integration**: Sentence transformers for semantic search

## 📚 Documentation

For complete project information, see the [documentation directory](docs/README.md).

## 🔗 Quick Links

- [API Reference](docs/api/README.md)
- [System Architecture](docs/architecture/README.md)
- [Deployment Guide](docs/deployment/README.md)
- [Data Pipeline](docs/data/README.md)

## 📄 Technical Specification

The complete technical specification is available as a PDF document. See [Tech Spec Overview](docs/tech-spec/README.md) for details.

