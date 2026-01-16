#!/bin/sh
exec gunicorn --worker-class eventlet -w 1 app:app --bind "0.0.0.0:${PORT:-8080}"
