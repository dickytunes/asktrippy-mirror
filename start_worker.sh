#!/bin/bash
# Start the Voy8 background worker
# This script ensures we use the correct Python environment

cd "$(dirname "$0")"
source .venv/bin/activate

echo "Starting Voy8 background worker..."
echo "This will process crawl jobs from the queue and enrich venue data."
echo "Press Ctrl+C to stop."

python -m backend.worker --workers 2 --batch-size 8
