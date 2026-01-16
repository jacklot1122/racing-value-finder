#!/bin/sh
# Use PORT env var if set, otherwise default to 8080
PORT="${PORT:-8080}"
echo "Starting server on port $PORT"
exec gunicorn --worker-class eventlet -w 1 app:app --bind "0.0.0.0:$PORT"
