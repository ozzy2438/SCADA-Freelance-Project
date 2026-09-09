"""PostgreSQL / SQLite SQLAlchemy adapters for inverters and alerts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ges_intel.domain.alerting import to_utc
from ges_intel.domain.models import Alert, AlertPriority, AlertStatus, Inverter


class Base(DeclarativeBase):
    pass


class InverterRow(Base):
    __tablename__ = "inverters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    capacity_kw: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_one_open_per_inverter",
            "inverter_id",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    streak_periods: Mapped[int] = mapped_column(nullable=False)
    lost_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    cash_loss_usd: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def create_engine_from_url(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def _inverter_from_row(row: InverterRow) -> Inverter:
    return Inverter(
        id=row.id,
        name=row.name,
        capacity_kw=row.capacity_kw,
        latitude=row.latitude,
        longitude=row.longitude,
        timezone=row.timezone,
    )


def _alert_from_row(row: AlertRow) -> Alert:
    return Alert(
        id=row.id,
        inverter_id=row.inverter_id,
        status=AlertStatus(row.status),
        priority=AlertPriority(row.priority),
        window_start=to_utc(row.window_start),
        window_end=to_utc(row.window_end),
        streak_periods=row.streak_periods,
        lost_kwh=row.lost_kwh,
        cash_loss_usd=row.cash_loss_usd,
        model_version=row.model_version,
        created_at=to_utc(row.created_at),
        resolved_at=to_utc(row.resolved_at) if row.resolved_at else None,
    )


class SqlAlchemyInverterRepo:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def get(self, inverter_id: str) -> Inverter | None:
        with self._session_factory() as session:
            row = session.get(InverterRow, inverter_id)
            return _inverter_from_row(row) if row else None

    def list_all(self) -> list[Inverter]:
        with self._session_factory() as session:
            rows = session.scalars(select(InverterRow).order_by(InverterRow.id)).all()
            return [_inverter_from_row(r) for r in rows]

    def upsert(self, inverter: Inverter) -> None:
        with self._session_factory() as session:
            row = session.get(InverterRow, inverter.id)
            if row is None:
                row = InverterRow(id=inverter.id)
                session.add(row)
            row.name = inverter.name
            row.capacity_kw = inverter.capacity_kw
            row.latitude = inverter.latitude
            row.longitude = inverter.longitude
            row.timezone = inverter.timezone
            session.commit()


class SqlAlchemyAlertRepo:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def get_open(self, inverter_id: str) -> Alert | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(AlertRow).where(
                    AlertRow.inverter_id == inverter_id, AlertRow.status == AlertStatus.OPEN.value
                )
            ).first()
            return _alert_from_row(row) if row else None

    def insert(self, alert: Alert) -> Alert:
        alert_id = alert.id or str(uuid.uuid4())
        with self._session_factory() as session:
            row = AlertRow(
                id=alert_id,
                inverter_id=alert.inverter_id,
                status=alert.status.value,
                priority=alert.priority.value,
                window_start=alert.window_start,
                window_end=alert.window_end,
                streak_periods=alert.streak_periods,
                lost_kwh=alert.lost_kwh,
                cash_loss_usd=alert.cash_loss_usd,
                model_version=alert.model_version,
                created_at=alert.created_at,
                resolved_at=alert.resolved_at,
            )
            session.add(row)
            session.commit()
        alert.id = alert_id
        return alert

    def update(self, alert: Alert) -> None:
        if not alert.id:
            raise ValueError("alert.id is required for update")
        with self._session_factory() as session:
            row = session.get(AlertRow, alert.id)
            if row is None:
                raise KeyError(alert.id)
            row.status = alert.status.value
            row.priority = alert.priority.value
            row.window_start = alert.window_start
            row.window_end = alert.window_end
            row.streak_periods = alert.streak_periods
            row.lost_kwh = alert.lost_kwh
            row.cash_loss_usd = alert.cash_loss_usd
            row.model_version = alert.model_version
            row.resolved_at = alert.resolved_at
            session.commit()

    def list_alerts(self, status: AlertStatus | str | None = None, limit: int = 200) -> list[Alert]:
        with self._session_factory() as session:
            stmt = select(AlertRow).order_by(AlertRow.created_at.desc()).limit(limit)
            if status is not None:
                value = status.value if isinstance(status, AlertStatus) else status
                stmt = stmt.where(AlertRow.status == value)
            rows = session.scalars(stmt).all()
            return [_alert_from_row(r) for r in rows]

    def count_open(self) -> int:
        with self._session_factory() as session:
            rows = session.scalars(select(AlertRow).where(AlertRow.status == AlertStatus.OPEN.value)).all()
            return len(rows)
