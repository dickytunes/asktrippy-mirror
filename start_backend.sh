#!/bin/bash
# Start the asktrippy backend server
# This script ensures we use the correct Python environment

cd "$(dirname "$0")"
source .venv/bin/activate
python -m uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
