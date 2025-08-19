#!/bin/bash
# Start the asktrippy frontend Streamlit app
# This script ensures we use the correct Python environment

cd "$(dirname "$0")/frontend"
source ../.venv/bin/activate
streamlit run app.py --server.port 8501
