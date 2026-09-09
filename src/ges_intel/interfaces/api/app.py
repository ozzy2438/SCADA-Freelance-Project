"""FastAPI application: ingest, alerts, performance metrics."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ges_intel.application.ingest import SensorReading, ingest_readings
from ges_intel.application.metrics import build_performance_report
from ges_intel.config import Settings, get_settings
from ges_intel.domain.errors import (
    MetricsRangeError,
    ModelNotLoadedError,
    PersistenceError,
    UnknownInverterError,
)
from ges_intel.domain.models import AlertStatus
from ges_intel.infrastructure.duckdb_store import DuckDBTelemetryRepo
from ges_intel.infrastructure.ml_model import PhysicsExpectedModel, RandomForestExpectedModel
from ges_intel.infrastructure.postgres import (
    SqlAlchemyAlertRepo,
    SqlAlchemyInverterRepo,
    create_engine_from_url,
    create_session_factory,
    init_schema,
)
from ges_intel.interfaces.api.schemas import (
    AlertOut,
    DailyPointOut,
    ErrorOut,
    IngestRequest,
    IngestResponse,
    InverterMetricsOut,
    PerformanceMetricsOut,
    PlantMetricsOut,
)
from ges_intel.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def _build_runtime(settings: Settings) -> dict:
    engine = create_engine_from_url(settings.database_url)
    init_schema(engine)
    session_factory = create_session_factory(engine)
    inverter_repo = SqlAlchemyInverterRepo(session_factory)
    alert_repo = SqlAlchemyAlertRepo(session_factory)
    telemetry_repo = DuckDBTelemetryRepo(settings.duckdb_path)
    rf = RandomForestExpectedModel(settings.model_path, settings.model_metadata_path)
    try:
        rf.load()
        model = rf
    except ModelNotLoadedError:
        logger.warning("model_missing_using_physics_baseline path=%s", settings.model_path)
        model = PhysicsExpectedModel()
    return {
        "engine": engine,
        "inverter_repo": inverter_repo,
        "alert_repo": alert_repo,
        "telemetry_repo": telemetry_repo,
        "model": model,
        "settings": settings,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    runtime = _build_runtime(settings)
    for key, value in runtime.items():
        setattr(app.state, key, value)
    logger.info("api_started duckdb=%s db=%s", settings.duckdb_path, settings.database_url)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="GES Actual vs Expected Intelligence Engine",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors(), "code": "validation_error"})

    @app.exception_handler(UnknownInverterError)
    async def unknown_inverter_handler(_request: Request, exc: UnknownInverterError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorOut(detail=str(exc), code="unknown_inverter").model_dump(),
        )

    @app.exception_handler(ModelNotLoadedError)
    async def model_handler(_request: Request, exc: ModelNotLoadedError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ErrorOut(detail=str(exc), code="model_not_loaded").model_dump(),
        )

    @app.exception_handler(PersistenceError)
    async def persistence_handler(_request: Request, exc: PersistenceError) -> JSONResponse:
        logger.exception("db_unavailable store=%s", exc.store)
        return JSONResponse(
            status_code=503,
            content=ErrorOut(detail="Database unavailable", code="db_unavailable").model_dump(),
        )

    @app.exception_handler(MetricsRangeError)
    async def range_handler(_request: Request, exc: MetricsRangeError) -> JSONResponse:
        return JSONResponse(status_code=400, content=ErrorOut(detail=str(exc), code="bad_range").model_dump())

    def get_runtime_settings() -> Settings:
        return app.state.settings

    def require_api_key(
        x_api_key: Annotated[str | None, Header()] = None,
        runtime_settings: Settings = Depends(get_runtime_settings),
    ) -> None:
        if not x_api_key or x_api_key != runtime_settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/ingest", response_model=IngestResponse, dependencies=[Depends(require_api_key)])
    def ingest(payload: IngestRequest, request: Request) -> IngestResponse:
        result = ingest_readings(
            [
                SensorReading(
                    inverter_id=item.inverter_id,
                    ts_utc=item.timestamp,
                    irradiance_wm2=item.irradiance_wm2,
                    temperature_c=item.temperature_c,
                    actual_power_kw=item.actual_power_kw,
                )
                for item in payload.readings
            ],
            inverter_repo=request.app.state.inverter_repo,
            telemetry_repo=request.app.state.telemetry_repo,
            alert_repo=request.app.state.alert_repo,
            model=request.app.state.model,
            tariff_usd_per_kwh=request.app.state.settings.tariff_usd_per_kwh,
        )
        return IngestResponse(
            persisted=result.persisted,
            alerts_opened=result.alerts_opened,
            alerts_resolved=result.alerts_resolved,
            alerts_updated=result.alerts_updated,
            inverter_ids=result.inverter_ids,
        )

    @app.get("/alerts", response_model=list[AlertOut], dependencies=[Depends(require_api_key)])
    def list_alerts(
        request: Request,
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[AlertOut]:
        if status is not None:
            try:
                parsed = AlertStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="status must be 'open' or 'resolved'") from exc
        else:
            parsed = None
        alerts = request.app.state.alert_repo.list_alerts(status=parsed, limit=limit)
        return [
            AlertOut(
                id=a.id,
                inverter_id=a.inverter_id,
                status=a.status.value,
                priority=a.priority.value,
                window_start=a.window_start,
                window_end=a.window_end,
                streak_periods=a.streak_periods,
                lost_kwh=a.lost_kwh,
                cash_loss_usd=a.cash_loss_usd,
                model_version=a.model_version,
                created_at=a.created_at,
                resolved_at=a.resolved_at,
            )
            for a in alerts
        ]

    @app.get(
        "/performance-metrics",
        response_model=PerformanceMetricsOut,
        dependencies=[Depends(require_api_key)],
    )
    def performance_metrics(
        request: Request,
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
    ) -> PerformanceMetricsOut:
        settings = request.app.state.settings
        if start is None or end is None:
            span = request.app.state.telemetry_repo.time_range()
            if span is None:
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=7)
            else:
                data_start, data_end = span
                end = data_end + timedelta(minutes=15)
                start = data_start
                if end - start > timedelta(days=settings.metrics_max_span_days):
                    start = end - timedelta(days=settings.metrics_max_span_days)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        report = build_performance_report(
            telemetry_repo=request.app.state.telemetry_repo,
            alert_repo=request.app.state.alert_repo,
            start=start,
            end=end,
            tariff_usd_per_kwh=settings.tariff_usd_per_kwh,
            max_span_days=settings.metrics_max_span_days,
            plant_timezone=settings.plant_timezone,
        )
        return PerformanceMetricsOut(
            start=report.start,
            end=report.end,
            plant=PlantMetricsOut(**report.plant.__dict__),
            inverters=[InverterMetricsOut(**row.__dict__) for row in report.inverters],
            daily=[DailyPointOut(**row.__dict__) for row in report.daily],
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run("ges_intel.interfaces.api.app:app", host="0.0.0.0", port=8000, workers=1)
