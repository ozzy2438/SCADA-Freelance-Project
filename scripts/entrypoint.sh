#!/bin/sh
set -eu
cd /app
python -m alembic upgrade head
python -m ges_intel.cli --if-needed --n-days "${SEED_DAYS:-30}"
exec python -m uvicorn ges_intel.interfaces.api.app:app --host 0.0.0.0 --port 8000 --workers 1
