@echo off
REM Start the asktrippy frontend Streamlit app

cd /d "%~dp0\frontend"
call ..\.venv\Scripts\activate
streamlit run app.py --server.port 8501
