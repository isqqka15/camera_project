from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rfid_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    face_encoding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    trips: Mapped[list["TripLog"]] = relationship(back_populates="user")


class TripLog(Base):
    __tablename__ = "trip_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    detected_by_camera: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_via_rfid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    payment_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fine_issued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user: Mapped[User] = relationship(back_populates="trips")
    fine: Mapped["Fine | None"] = relationship(back_populates="trip", uselist=False)


class PaymentLog(Base):
    __tablename__ = "payment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trip_logs.id"), nullable=False)
    rfid_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Fine(Base):
    __tablename__ = "fines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trip_logs.id"), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="UNPAID", nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False, default="EXIT_WITHOUT_PAYMENT")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    trip: Mapped[TripLog] = relationship(back_populates="fine")
