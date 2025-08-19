#!/bin/bash
# Start the Voy8 background scheduler
# This script ensures we use the correct Python environment

cd "$(dirname "$0")"
source .venv/bin/activate

echo "Starting Voy8 background scheduler..."
echo "This will continuously monitor data freshness and schedule background crawl jobs."
echo "Press Ctrl+C to stop."

python -m backend.scheduler --sleep-seconds 300 --batch-size 50
