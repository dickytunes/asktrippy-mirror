#!/bin/bash
# Stop all Voy8 services
# This script stops all running Voy8 processes

echo "🛑 Stopping all Voy8 services..."

# Function to stop processes by name pattern
stop_processes() {
    local pattern=$1
    local name=$2
    
    echo "Stopping $name processes..."
    pids=$(pgrep -f "$pattern" | tr '\n' ' ')
    
    if [ -n "$pids" ]; then
        echo "Found PIDs: $pids"
        kill $pids 2>/dev/null
        
        # Wait a bit and force kill if still running
        sleep 2
        still_running=$(pgrep -f "$pattern")
        if [ -n "$still_running" ]; then
            echo "Force killing remaining $name processes..."
            kill -9 $still_running 2>/dev/null
        fi
        
        echo "✅ $name processes stopped"
    else
        echo "ℹ️  No $name processes found"
    fi
}

# Stop each service type
stop_processes "backend.worker" "Background Worker"
stop_processes "backend.scheduler" "Background Scheduler"
stop_processes "backend.embedding_job" "Embedding Job"
stop_processes "uvicorn.*backend.api" "Backend API"
stop_processes "streamlit.*app.py" "Frontend Streamlit"

# Check for any remaining Voy8 processes
remaining=$(pgrep -f "backend\." | tr '\n' ' ')
if [ -n "$remaining" ]; then
    echo "⚠️  Some processes may still be running: $remaining"
    echo "You can force kill them with: kill -9 $remaining"
else
    echo "✅ All Voy8 services stopped successfully"
fi

echo ""
echo "🔍 Checking if ports are free..."
if ! lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "✅ Port 8000 (Backend) is free"
else
    echo "❌ Port 8000 (Backend) is still in use"
fi

if ! lsof -Pi :8501 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "✅ Port 8501 (Frontend) is free"
else
    echo "❌ Port 8501 (Frontend) is still in use"
fi
