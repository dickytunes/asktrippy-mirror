#!/bin/bash
# Start both asktrippy backend and frontend services
# This script ensures we use the correct Python environment

echo "Starting asktrippy services..."

# Start backend in background
echo "Starting backend on port 8000..."
./start_backend.sh &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 3

# Start frontend in background
echo "Starting frontend on port 8501..."
./start_frontend.sh &
FRONTEND_PID=$!

echo "Services started!"
echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"
echo ""
echo "Access your services at:"
echo "  Backend API: http://localhost:8000"
echo "  Frontend App: http://localhost:8501"
echo ""
echo "To stop services, run: kill $BACKEND_PID $FRONTEND_PID"
echo "Or use: pkill -f 'uvicorn\|streamlit'"
