# GES Actual vs Expected Intelligence Engine

Catch hidden solar-plant losses (soiling, inverter underperformance, shading) **before they show up on the monthly invoice**. The engine compares live 15-minute production with a machine-learned **expected** kW, opens a high-priority alert after one hour of ≥10% shortfall, and puts a dollar figure on the energy that never reached the grid.

**Business value:** lost energy is converted with `lost_kWh = max(0, expected_kW − actual_kW) × 0.25 h`, then `cash_loss = lost_kWh × $0.10`. A 15% soiling event on a 200 kW block is not “a slightly lower curve” — it is an open work order with a cash-at-risk column.

## Architecture

```
Generator (physical PV + labeled soiling/shading)
    -> POST /ingest  (API key)
        -> upsert DuckDB telemetry (unique inverter_id, ts_utc)
        -> RandomForest expected_power_kw (clipped to nameplate)
        -> 4 consecutive eligible 15-min UTC slots ≥10% low
        -> Postgres alerts (one open row per inverter, rising-edge)
Streamlit cockpit -> GET /alerts  GET /performance-metrics
```

Night and near-zero expected power are **not evaluated** (no divide-by-zero, no overnight false alarms). Streamlit never opens DuckDB; the API is the single writer (`uvicorn --workers 1`).

## Repository layout

```
src/ges_intel/
  domain/           physics, guarded delta, streak rules
  application/      ingest, train, metrics use cases
  infrastructure/   Postgres, DuckDB, generator, RF adapter
  interfaces/api    FastAPI
  interfaces/dashboard  Streamlit (HTTP client)
tests/              pytest, ≥80% coverage on ges_intel
```

## Quick start (local, SQLite)

Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # optional; defaults work for local sqlite

python -m ges_intel.cli --n-days 14
python -m uvicorn ges_intel.interfaces.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

In another shell:

```bash
streamlit run src/ges_intel/interfaces/dashboard/app.py
```

- API: http://127.0.0.1:8000/docs  
- Cockpit: http://127.0.0.1:8501  
- Default API key: `dev-api-key` (`X-API-Key`)

## Docker Compose (Postgres + API + Streamlit)

Credentials are **not** committed. Generate a local `.env` (gitignored), then start the stack:

```bash
python -m ges_intel.cli init-env
docker compose up --build
```

`init-env` refuses to overwrite an existing `.env` unless you pass `--force`. First API start runs Alembic, trains the RandomForest on 30 days of synthetic SCADA (seed `42`, five inverters at a Konya-like site, `Europe/Istanbul`), and ingests history. Volumes keep DuckDB, the model artifact, and Postgres across restarts.

- API http://localhost:8000  
- Dashboard http://localhost:8501  

Rotate `API_KEY` and `POSTGRES_PASSWORD` in `.env` before exposing the stack.

## HTTP API

All routes except `GET /health` require `X-API-Key`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/ingest` | Batch of 1–500 sensor readings. Scores expected power, upserts DuckDB, opens/resolves alerts. |
| GET | `/alerts?status=open\|resolved` | Active or historical alerts, including `lost_kwh` and `cash_loss_usd`. |
| GET | `/performance-metrics?start&end` | Plant and per-inverter kWh, health %, daily series (max 31 days). |

Validation errors return **422**. Unknown inverter ids return **400**. Database failures return **503** without leaking internals.

Example ingest:

```bash
export API_KEY  # same value written by `python -m ges_intel.cli init-env`
curl -s -X POST http://127.0.0.1:8000/ingest \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"readings":[{"inverter_id":"INV-01","timestamp":"2026-06-15T09:00:00Z","irradiance_wm2":850,"temperature_c":25,"actual_power_kw":40}]}'
```

## Model

- Features: irradiance, temperature, hour of day (plant TZ), nameplate capacity  
- Target: actual kW on **non-anomaly** simulator rows only (`is_anomaly` never leaves the generator)  
- Algorithm: `sklearn.ensemble.RandomForestRegressor` (`random_state=42`)  
- Gate: holdout RMSE on daylight rows must beat the simple physical baseline `P = Pdc × G/1000 × (1+γ(T−25)) × PR`  
- Serving clips predictions to `[0, capacity_kw]`

## Tests

```bash
python -m pytest --cov=ges_intel --cov-report=term-missing
```

Named cases cover night gating, 3-vs-4 streak, gap reset, duplicate ingest, kW→kWh (`× 0.25`), training leakage, and API error codes.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `API_KEY` | `dev-api-key` | Shared secret for API and Streamlit |
| `DATABASE_URL` | sqlite `./data/ges.db` | SQLAlchemy URL (required for Compose Postgres) |
| `POSTGRES_PASSWORD` | _(none — set in gitignored `.env`)_ | Postgres password for Compose |
| `DUCKDB_PATH` | `data/telemetry.duckdb` | Telemetry file |
| `MODEL_PATH` | `artifacts/model.joblib` | RF artifact |
| `TARIFF_USD_PER_KWH` | `0.10` | Cash-loss tariff |
| `API_BASE_URL` | `http://127.0.0.1:8000` | Streamlit client |

Synthetic fleet: `INV-01`…`INV-05` at 80–200 kW, 15-minute UTC slots, soiling 12–15% on selected inverter-days, short shading drops that may not meet the one-hour rule (still labeled for evaluation).
