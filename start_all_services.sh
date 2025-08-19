#!/bin/bash
# Start all Voy8 services
# This script launches the complete system in the correct order

cd "$(dirname "$0")"
source .venv/bin/activate

echo "🚀 Starting Voy8 - Complete Travel Intelligence System"
echo "=================================================="

# Function to check if a port is available
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null ; then
        echo "❌ Port $1 is already in use"
        return 1
    else
        echo "✅ Port $1 is available"
        return 0
    fi
}

# Function to wait for a service to be ready
wait_for_service() {
    local url=$1
    local service_name=$2
    local max_attempts=30
    local attempt=1
    
    echo "⏳ Waiting for $service_name to be ready..."
    while [ $attempt -le $max_attempts ]; do
        if curl -s "$url/health" >/dev/null 2>&1; then
            echo "✅ $service_name is ready!"
            return 0
        fi
        echo "   Attempt $attempt/$max_attempts..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "❌ $service_name failed to start after $max_attempts attempts"
    return 1
}

# Check ports
echo "🔍 Checking port availability..."
check_port 8000 || exit 1  # Backend API
check_port 8501 || exit 1  # Frontend Streamlit
check_port 5432 || echo "⚠️  PostgreSQL port 5432 check skipped (may be running in Docker)"

echo ""
echo "📊 Starting services..."

# 1. Start Backend API
echo "1️⃣  Starting Backend API (FastAPI)..."
python -m uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
sleep 3

# Wait for backend to be ready
if ! wait_for_service "http://localhost:8000" "Backend API"; then
    echo "❌ Backend failed to start. Stopping all services..."
    kill $BACKEND_PID 2>/dev/null
    exit 1
fi

# 2. Start Background Worker
echo "2️⃣  Starting Background Worker..."
python -m backend.worker --workers 2 --batch-size 8 &
WORKER_PID=$!
sleep 2

# 3. Start Background Scheduler
echo "3️⃣  Starting Background Scheduler..."
python -m backend.scheduler --sleep-seconds 300 --batch-size 50 &
SCHEDULER_PID=$!
sleep 2

# 4. Start Embedding Job
echo "4️⃣  Starting Embedding Population Job..."
python -m backend.embedding_job --continuous --batch-size 100 --sleep-seconds 60 &
EMBEDDING_PID=$!
sleep 2

# 5. Start Frontend
echo "5️⃣  Starting Frontend (Streamlit)..."
cd frontend
streamlit run app.py --server.port 8501 &
FRONTEND_PID=$!
cd ..
sleep 3

# Wait for frontend to be ready
if ! wait_for_service "http://localhost:8501" "Frontend"; then
    echo "❌ Frontend failed to start. Stopping all services..."
    kill $BACKEND_PID $WORKER_PID $SCHEDULER_PID $EMBEDDING_PID $FRONTEND_PID 2>/dev/null
    exit 1
fi

echo ""
echo "🎉 All services started successfully!"
echo "=================================================="
echo "🌐 Frontend:     http://localhost:8501"
echo "🔌 Backend API:  http://localhost:8000"
echo "📚 API Docs:     http://localhost:8000/docs"
echo "💼 Worker:       Running (PID: $WORKER_PID)"
echo "⏰ Scheduler:    Running (PID: $SCHEDULER_PID)"
echo "🧠 Embedding:    Running (PID: $EMBEDDING_PID)"
echo ""
echo "📋 Service Status:"
echo "   Backend API:  $(curl -s http://localhost:8000/health | grep -o '"ok":[^,]*' | cut -d: -f2)"
echo "   Queue Depth:  $(curl -s http://localhost:8000/health | grep -o '"queue_depth":[^,]*' | cut -d: -f2)"
echo ""
echo "🛑 To stop all services, run: ./stop_all_services.sh"
echo "   Or press Ctrl+C in this terminal"

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down all services..."
    kill $BACKEND_PID $WORKER_PID $SCHEDULER_PID $EMBEDDING_PID $FRONTEND_PID 2>/dev/null
    echo "✅ All services stopped"
    exit 0
}

# Set trap to cleanup on script exit
trap cleanup SIGINT SIGTERM

# Keep script running and monitor services
echo "👀 Monitoring services... (Press Ctrl+C to stop all)"
while true; do
    sleep 10
    
    # Check if any service has died
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "❌ Backend API has stopped unexpectedly"
        cleanup
    fi
    
    if ! kill -0 $WORKER_PID 2>/dev/null; then
        echo "❌ Background Worker has stopped unexpectedly"
        cleanup
    fi
    
    if ! kill -0 $SCHEDULER_PID 2>/dev/null; then
        echo "❌ Background Scheduler has stopped unexpectedly"
        cleanup
    fi
    
    if ! kill -0 $EMBEDDING_PID 2>/dev/null; then
        echo "❌ Embedding Job has stopped unexpectedly"
        cleanup
    fi
    
    if ! kill -0 $FRONTEND_PID 2>/dev/null; then
        echo "❌ Frontend has stopped unexpectedly"
        cleanup
    fi
    
    # Show status every minute
    if [ $((SECONDS % 60)) -eq 0 ]; then
        echo "✅ All services running normally"
    fi
done
