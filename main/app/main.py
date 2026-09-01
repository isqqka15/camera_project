import asyncio
import io
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import face_recognition
import numpy as np
import serial
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, User
from .passenger_tracker import PassengerTracker

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("fare-enforcement")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fare_enforcement.db")
FINE_AMOUNT = float(os.getenv("FINE_AMOUNT", "50.00"))
FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.6"))
EXIT_DEBOUNCE_SECONDS = float(os.getenv("EXIT_DEBOUNCE_SECONDS", "15"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD_RATE = int(os.getenv("SERIAL_BAUD_RATE", "115200"))
SERIAL_REQUIRED = os.getenv("SERIAL_REQUIRED", "false").lower() == "true"

connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
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
    return re.sub(r"[^A-Fa-f0-9]", "", str(value)).upper()


def parse_rfid_payload(raw: str) -> str | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("rfid", "uid", "card_id", "card", "tag"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return normalize_uid(value)
    if isinstance(payload, str) and payload.strip():
        return normalize_uid(payload)
    for key in ("rfid", "uid", "card_id", "card", "tag"):
        match = re.search(rf"(?:{key})\s*[:=]\s*([A-Fa-f0-9:\s]+)", text, re.IGNORECASE)
        if match:
            return normalize_uid(match.group(1))
    candidate = re.sub(r"[^A-Fa-f0-9]", "", text)
    return candidate.upper() if candidate else None


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
    normalized = parse_rfid_payload(rfid)
    if not normalized:
        logger.warning("Ignoring malformed RFID payload: %r", rfid)
        return False
    with SessionLocal() as database:
        user = tracker.user_for_rfid(database, normalized)
        return user is not None and tracker.mark_paid(user.id, normalized)


async def serial_worker() -> None:
    retry_delay = 2
    while True:
        device = serial_device_path()
        if device is None or not Path(device).exists():
            message = "No Arduino serial device found"
            if SERIAL_REQUIRED:
                logger.error(message)
                return
            logger.warning("%s; continuing without RFID reader", message)
            await asyncio.sleep(5)
            continue
        try:
            with serial.Serial(device, SERIAL_BAUD_RATE, timeout=1, write_timeout=1) as port:
                logger.info("Listening for RFID events on %s", device)
                retry_delay = 2
                while True:
                    try:
                        line = await asyncio.to_thread(port.readline)
                    except serial.SerialException as exc:
                        logger.warning("RFID read failed on %s: %s; reconnecting", device, exc)
                        break
                    if not line:
                        continue
                    raw = line.decode("utf-8", errors="replace").strip()
                    if not raw:
                        continue
                    rfid = parse_rfid_payload(raw)
                    if rfid is None:
                        logger.warning("Ignoring malformed RFID payload: %r", raw)
                        continue
                    if not handle_rfid(rfid):
                        logger.info("RFID %s not recognized or no active passenger", rfid)
        except asyncio.CancelledError:
            raise
        except (serial.SerialException, OSError) as exc:
            logger.warning("RFID serial reader disconnected on %s: %s; retrying in %ss", device, exc, retry_delay)
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)


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
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")
    try:
        frame = face_recognition.load_image_file(io.BytesIO(content))
        encodings = face_recognition.face_encodings(frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    if not encodings:
        raise HTTPException(status_code=404, detail="No face found in the provided image")
    user, score = find_user_by_face(database, encodings[0])
    if user is None:
        raise HTTPException(status_code=404, detail="Face did not match any registered user")
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
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")
    try:
        frame = face_recognition.load_image_file(io.BytesIO(content))
        encodings = face_recognition.face_encodings(frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    if len(encodings) != 1:
        raise HTTPException(status_code=400, detail="Registration image must contain exactly one face")
    normalized = normalize_uid(rfid_uid)
    if not normalized:
        raise HTTPException(status_code=422, detail="RFID UID is empty or invalid")
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
