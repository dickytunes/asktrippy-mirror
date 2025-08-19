#!/bin/bash
# Stop all asktrippy services

echo "Stopping asktrippy services..."

# Kill uvicorn processes (backend)
echo "Stopping backend..."
pkill -f "uvicorn.*backend.api:app" || echo "No backend processes found"

# Kill streamlit processes (frontend)
echo "Stopping frontend..."
pkill -f "streamlit.*app.py" || echo "No frontend processes found"

echo "All services stopped!"
