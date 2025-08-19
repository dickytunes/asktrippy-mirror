# Deployment Guide

## Overview

This guide covers deploying the asktrippy platform to production environments. The system can be deployed using Docker containers, traditional server deployment, or cloud-native approaches.

## Prerequisites

### System Requirements
- **CPU**: 4+ cores recommended
- **Memory**: 8GB+ RAM (16GB+ for production)
- **Storage**: 100GB+ SSD storage
- **Network**: Stable internet connection for external APIs

### Software Requirements
- **Operating System**: Linux (Ubuntu 20.04+ recommended)
- **Docker**: 20.10+ (if using containers)
- **Python**: 3.8+ (if not using containers)
- **PostgreSQL**: 13+ with PostGIS and pgvector extensions

## Deployment Options

### Option 1: Docker Deployment (Recommended)

#### 1.1 Docker Compose Setup
Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg15
    environment:
      POSTGRES_DB: asktrippy
      POSTGRES_USER: asktrippy_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./infra/migrations:/docker-entrypoint-initdb.d
    ports:
      - "5432:5432"
    command: >
      postgres
      -c shared_preload_libraries=vector
      -c max_connections=100
      -c shared_buffers=256MB
      -c effective_cache_size=1GB

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql://asktrippy_user:${DB_PASSWORD}@postgres:5432/asktrippy
      QUERY_DEFAULT_RADIUS_M: 1500
      QUERY_MAX_RESULTS: 30
      MODEL_PREWARM: "true"
    ports:
      - "8000:8000"
    depends_on:
      - postgres
    volumes:
      - ./backend:/app
      - model_cache:/root/.cache

  frontend:
    build: ./frontend
    ports:
      - "8501:8501"
    depends_on:
      - backend
    environment:
      BACKEND_URL: http://backend:8000

volumes:
  postgres_data:
  model_cache:
```

#### 1.2 Environment Variables
Create a `.env` file:

```bash
# Database
DB_PASSWORD=your_secure_password_here

# API Configuration
QUERY_DEFAULT_RADIUS_M=1500
QUERY_MAX_RESULTS=30
MODEL_PREWARM=true

# Optional: External Services
FOURSQUARE_API_KEY=your_foursquare_key
```

#### 1.3 Deploy
```bash
# Build and start services
docker-compose up -d --build

# Check status
docker-compose ps

# View logs
docker-compose logs -f backend
```

### Option 2: Traditional Server Deployment

#### 2.1 Server Setup
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib

# Install PostGIS
sudo apt install -y postgis postgresql-15-postgis-3

# Install pgvector
sudo apt install -y postgresql-15-pgvector
```

#### 2.2 Database Setup
```bash
# Create database user
sudo -u postgres createuser --interactive asktrippy_user

# Create database
sudo -u postgres createdb asktrippy

# Enable extensions
sudo -u postgres psql -d asktrippy -c "CREATE EXTENSION IF NOT EXISTS postgis;"
sudo -u postgres psql -d asktrippy -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run migrations
sudo -u postgres psql -d asktrippy -f infra/migrations/20250815_0001_init.sql
```

#### 2.3 Application Deployment
```bash
# Clone repository
git clone <your-repo> asktrippy
cd asktrippy

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL="postgresql://asktrippy_user:password@localhost/asktrippy"
export QUERY_DEFAULT_RADIUS_M=1500
export QUERY_MAX_RESULTS=30
export MODEL_PREWARM=true

# Start backend
cd backend
uvicorn api:app --host 0.0.0.0 --port 8000

# Start frontend (in another terminal)
cd frontend
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

### Option 3: Cloud Deployment

#### 3.1 AWS Deployment
```bash
# Install AWS CLI
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Configure AWS
aws configure

# Create ECS cluster
aws ecs create-cluster --cluster-name asktrippy

# Deploy using ECS (requires task definitions)
```

#### 3.2 Google Cloud Deployment
```bash
# Install gcloud CLI
curl https://sdk.cloud.google.com | bash
exec -l $SHELL

# Configure project
gcloud config set project YOUR_PROJECT_ID

# Deploy to Cloud Run
gcloud run deploy asktrippy-backend --source ./backend
gcloud run deploy asktrippy-frontend --source ./frontend
```

## Production Considerations

### Security
```bash
# Firewall configuration
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 8000/tcp  # Backend API
sudo ufw allow 8501/tcp  # Frontend
sudo ufw enable

# SSL/TLS certificates
sudo apt install -y certbot
sudo certbot --nginx -d yourdomain.com
```

### Monitoring
```bash
# Install monitoring tools
sudo apt install -y htop iotop nethogs

# Set up log rotation
sudo logrotate -f /etc/logrotate.conf
```

### Backup Strategy
```bash
# Database backup script
#!/bin/bash
BACKUP_DIR="/backups"
DATE=$(date +%Y%m%d_%H%M%S)
pg_dump -h localhost -U asktrippy_user -d asktrippy > "$BACKUP_DIR/backup_$DATE.sql"

# Keep only last 7 days
find $BACKUP_DIR -name "backup_*.sql" -mtime +7 -delete
```

## Performance Tuning

### Database Optimization
```sql
-- PostgreSQL configuration
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';

-- Reload configuration
SELECT pg_reload_conf();
```

### Application Optimization
```python
# Gunicorn configuration for production
gunicorn api:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 2
```

## Health Checks

### API Health
```bash
# Check backend health
curl http://localhost:8000/health

# Check readiness
curl http://localhost:8000/ready

# Expected response
{
  "ok": true,
  "db": "ok",
  "queue_depth": 0,
  "version": "0.1.0"
}
```

### Database Health
```bash
# Check database connection
psql -h localhost -U asktrippy_user -d asktrippy -c "SELECT 1;"

# Check extensions
psql -h localhost -U asktrippy_user -d asktrippy -c "SELECT * FROM pg_extension;"
```

## Troubleshooting

### Common Issues

#### Database Connection Failures
```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Check logs
sudo tail -f /var/log/postgresql/postgresql-15-main.log

# Restart service
sudo systemctl restart postgresql
```

#### Memory Issues
```bash
# Check memory usage
free -h

# Check swap
swapon --show

# Add swap if needed
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

#### Port Conflicts
```bash
# Check what's using ports
sudo netstat -tulpn | grep :8000
sudo netstat -tulpn | grep :8501

# Kill conflicting processes
sudo kill -9 <PID>
```

## Scaling

### Horizontal Scaling
```bash
# Load balancer configuration (nginx)
upstream backend {
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
}

server {
    listen 80;
    location / {
        proxy_pass http://backend;
    }
}
```

### Vertical Scaling
```bash
# Increase worker processes
gunicorn api:app --workers 8 --worker-class uvicorn.workers.UvicornWorker

# Increase database connections
ALTER SYSTEM SET max_connections = 200;
```

## Maintenance

### Regular Tasks
```bash
# Database maintenance
psql -d asktrippy -c "VACUUM ANALYZE;"

# Log rotation
sudo logrotate -f /etc/logrotate.conf

# Security updates
sudo apt update && sudo apt upgrade -y
```

### Monitoring
```bash
# Set up monitoring alerts
# Use tools like Prometheus, Grafana, or cloud monitoring
# Monitor:
# - API response times
# - Database performance
# - Memory usage
# - Disk space
# - Error rates
```

## Related Documentation

- [Development Setup](../development/README.md)
- [System Architecture](../architecture/README.md)
- [Database Schema](../database/README.md)
- [API Reference](../api/README.md)
