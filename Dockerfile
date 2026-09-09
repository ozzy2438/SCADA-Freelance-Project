FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir . \
    && chmod +x /app/scripts/entrypoint.sh \
    && mkdir -p /data /artifacts

ENV PYTHONUNBUFFERED=1 \
    DUCKDB_PATH=/data/telemetry.duckdb \
    MODEL_PATH=/artifacts/model.joblib \
    MODEL_METADATA_PATH=/artifacts/model_metadata.json

EXPOSE 8000 8501

CMD ["/app/scripts/entrypoint.sh"]
