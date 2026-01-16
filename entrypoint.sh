#!/bin/sh
echo "Starting server on port 8080"
exec gunicorn --worker-class eventlet -w 1 app:app --bind 0.0.0.0:8080
