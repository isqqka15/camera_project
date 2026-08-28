import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import Fine, TripLog, User, utc_now

logger = logging.getLogger(__name__)


class PassengerTracker:
    """Owns the entry/payment/exit state for the current bus trip."""

    def __init__(self, sessions: sessionmaker, fine_amount: float, cooldown_seconds: float = 15):
        self.sessions = sessions
        self.fine_amount = fine_amount
        self.cooldown_seconds = cooldown_seconds
        self.in_bus_passengers: dict[int, dict] = {}
        self.lock = threading.RLock()

    def enter_or_exit(self, user: User) -> tuple[str, TripLog | None]:
        now = datetime.now(timezone.utc)
        with self.lock, self.sessions.begin() as database:
            state = self.in_bus_passengers.get(user.id)
            if state is None:
                trip = TripLog(user_id=user.id, timestamp=now, detected_by_camera=True)
                database.add(trip)
                database.flush()
                self.in_bus_passengers[user.id] = {"paid": False, "entry_time": now, "trip_id": trip.id}
                logger.info("[ENTRY] Passenger %s entered", user.id)
                return "ENTRY", trip

            elapsed = (now - state["entry_time"]).total_seconds()
            if elapsed < self.cooldown_seconds:
                return "DEBOUNCED", None

            trip = database.get(TripLog, state["trip_id"])
            if trip is not None:
                trip.active = False
                trip.closed_at = now
                if not state["paid"]:
                    trip.fine_issued = True
                    database.add(Fine(trip_id=trip.id, user_id=user.id, amount=self.fine_amount, timestamp=now))
                    event = "VIOLATION"
                else:
                    event = "EXIT_PAID"
            else:
                event = "EXIT"
            self.in_bus_passengers.pop(user.id, None)
            logger.info("[%s] Passenger %s exited", event, user.id)
            return event, trip

    def mark_paid(self, user_id: int, rfid_uid: str | None = None) -> bool:
        with self.lock, self.sessions.begin() as database:
            state = self.in_bus_passengers.get(user_id)
            if state is None:
                return False
            state["paid"] = True
            trip = database.get(TripLog, state["trip_id"])
            if trip is not None:
                trip.paid_via_rfid = True
                trip.payment_timestamp = utc_now()
            return True

    def user_for_rfid(self, database: Session, rfid_uid: str) -> User | None:
        return database.scalar(select(User).where(User.rfid_uid == rfid_uid))