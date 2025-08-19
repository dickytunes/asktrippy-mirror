#!/bin/bash
# Start the Voy8 embedding population job
# This script ensures we use the correct Python environment

cd "$(dirname "$0")"
source .venv/bin/activate

echo "Starting Voy8 embedding population job..."
echo "This will create embeddings for venues with enrichment data."
echo "Press Ctrl+C to stop."

# Run continuously to process new venues as they get enriched
python -m backend.embedding_job --continuous --batch-size 100 --sleep-seconds 60
