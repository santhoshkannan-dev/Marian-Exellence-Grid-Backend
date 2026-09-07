@echo off
title Marian Backend Server (Django)
echo ========================================================
echo Starting Django Backend Server (Port 8000)...
echo ========================================================
cd /d "%~dp0"
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
    python manage.py runserver 8000
) else (
    echo Error: Virtual environment not found in backend\venv!
    pause
)
