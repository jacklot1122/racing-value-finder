@echo off
echo ============================================================
echo Racing Value Finder Web Application
echo ============================================================
echo.
echo Installing dependencies...
pip install flask flask-socketio eventlet -q
echo.
echo Starting web server...
echo Open http://localhost:5000 in your browser
echo.
python app.py
pause
