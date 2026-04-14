#!/usr/bin/env bash
# Run signal dashboard → open http://localhost:8080 in Chrome
set -e
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example:  cp .env.example .env"
  exit 1
fi
export PYTHONPATH="${PYTHONPATH:-.}"
exec uvicorn dashboard.app:app --host 0.0.0.0 --port 8080 --reload
