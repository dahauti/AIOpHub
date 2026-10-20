@echo off
cd /d C:\AIOpHub
start http://localhost:8000
.\.venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload