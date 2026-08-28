import asyncio
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import face_recognition
import numpy as np
import serial
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, User
from .passenger_tracker import PassengerTracker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("fare-enforcement")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fare_enforcement.db")
FINE_AMOUNT = float(os.getenv("FINE_AMOUNT", "50.00"))
FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.6"))
EXIT_DEBOUNCE_SECONDS = float(os.getenv("EXIT_DEBOUNCE_SECONDS", "15"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD_RATE = int(os.getenv("SERIAL_BAUD_RATE", "115200"))
SERIAL_REQUIRED = os.getenv("SERIAL_REQUIRED", "false").lower() == "true"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
tracker = PassengerTracker(SessionLocal, FINE_AMOUNT, EXIT_DEBOUNCE_SECONDS)


class ScanResult(BaseModel):
    matched: bool
    user_id: int | None = None
    name: str | None = None
    event: str
    similarity: float | None = None


class PaymentRequest(BaseModel):
    rfid: str


def normalize_uid(value: str) -> str:
    return value.strip().replace(":", "").replace(" ", "").upper()


def find_user_by_face(database: Session, encoding: np.ndarray) -> tuple[User | None, float]:
    query = encoding.astype(np.float64)
    norm = np.linalg.norm(query)
    if not norm:
        return None, -1.0
    query /= norm
    best_user, best_score = None, -1.0
    for user in database.scalars(select(User)):
        stored = np.frombuffer(user.face_encoding, dtype=np.float64)
        stored_norm = np.linalg.norm(stored)
        if stored.shape != query.shape or not stored_norm:
            continue
        score = float(np.dot(query, stored / stored_norm))
        if score > best_score:
            best_user, best_score = user, score
    return (best_user, best_score) if best_score >= FACE_MATCH_THRESHOLD else (None, best_score)


def serial_device_path() -> str | None:
    if SERIAL_PORT != "auto":
        return SERIAL_PORT
    return next((path for path in ("/dev/ttyACM0", "/dev/ttyUSB0") if Path(path).exists()), None)


def handle_rfid(rfid: str) -> bool:
    normalized = normalize_uid(rfid)
    with SessionLocal() as database:
        user = tracker.user_for_rfid(database, normalized)
        return user is not None and tracker.mark_paid(user.id, normalized)


async def serial_worker() -> None:
    device = serial_device_path()
    if device is None or not Path(device).exists():
        message = "No Arduino serial device found"
        if SERIAL_REQUIRED:
            logger.error(message)
        else:
            logger.warning("%s; continuing without RFID reader", message)
        return
    try:
        with serial.Serial(device, SERIAL_BAUD_RATE, timeout=1) as port:
            logger.info("Listening for RFID events on %s", device)
            while True:
                line = await asyncio.to_thread(port.readline)
                if not line:
                    continue
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                    rfid = payload.get("rfid")
                    if isinstance(rfid, str):
                        handle_rfid(rfid)
                    else:
                        logger.warning("RFID payload has no string rfid field")
                except (json.JSONDecodeError, UnicodeError) as exc:
                    logger.warning("Ignoring malformed RFID payload: %s", exc)
    except asyncio.CancelledError:
        raise
    except (serial.SerialException, OSError) as exc:
        logger.error("RFID serial reader stopped: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    task = asyncio.create_task(serial_worker(), name="rfid-reader")
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    engine.dispose()


app = FastAPI(title="Public Transport Fare Enforcement API", lifespan=lifespan)


def get_db():
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()


@app.get("/health")
def health(database: Session = Depends(get_db)):
    database.execute(select(1))
    return {"status": "ok", "active_passengers": len(tracker.in_bus_passengers)}


@app.post("/api/scan-face", response_model=ScanResult)
async def scan_face(image: Annotated[UploadFile, File(...)], database: Session = Depends(get_db)):
    if image.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")
    content = await image.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")
    try:
        frame = face_recognition.load_image_file(io.BytesIO(content))
        encodings = face_recognition.face_encodings(frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    if not encodings:
        return ScanResult(matched=False, event="NO_FACE")
    user, score = find_user_by_face(database, encodings[0])
    if user is None:
        return ScanResult(matched=False, event="UNKNOWN_FACE", similarity=score)
    event, _ = tracker.enter_or_exit(user)
    return ScanResult(matched=True, user_id=user.id, name=user.name, event=event, similarity=score)


@app.post("/api/register-user")
async def register_user(
    name: Annotated[str, Form(...)],
    rfid_uid: Annotated[str, Form(...)],
    image: Annotated[UploadFile, File(...)],
    database: Session = Depends(get_db),
):
    if image.content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")
    try:
        frame = face_recognition.load_image_file(io.BytesIO(await image.read()))
        encodings = face_recognition.face_encodings(frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    if len(encodings) != 1:
        raise HTTPException(status_code=400, detail="Registration image must contain exactly one face")
    normalized = normalize_uid(rfid_uid)
    if database.scalar(select(User).where(User.rfid_uid == normalized)):
        raise HTTPException(status_code=409, detail="RFID UID is already registered")
    user = User(name=name.strip(), rfid_uid=normalized, face_encoding=encodings[0].astype(np.float64).tobytes())
    database.add(user)
    database.commit()
    database.refresh(user)
    return {"id": user.id, "name": user.name, "rfid_uid": user.rfid_uid}


@app.post("/api/payment")
def payment(payload: PaymentRequest):
    if not handle_rfid(payload.rfid):
        raise HTTPException(status_code=404, detail="Unknown RFID or no active passenger")
    return {"status": "PAID", "rfid": normalize_uid(payload.rfid)}
