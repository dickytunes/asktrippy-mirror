@echo off
REM Start the asktrippy backend server
REM This script ensures we use the correct Python environment

cd /d "%~dp0"
call .venv\Scripts\activate
python -m uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
