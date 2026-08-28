# passenger_tracker.py
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PassengerTracker:
    def __init__(self, session_timeout: int = 300, database_path: str = "fare_enforcement.db", recognition_threshold: float = 0.6, exit_debounce_seconds: float = 15):
        self.session_timeout = session_timeout
        self.database_path = database_path
        self.recognition_threshold = recognition_threshold
        self.exit_debounce_seconds = exit_debounce_seconds
        self.in_bus_passengers = {}
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                embedding TEXT NOT NULL,
                account_ref TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ride_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('UNPAID', 'PAID')),
                entry_time TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                exit_time TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                amount REAL,
                provider_ref TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES ride_sessions(session_id)
            );
            CREATE TABLE IF NOT EXISTS violations (
                violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                photo_path TEXT,
                route TEXT,
                violation_type TEXT NOT NULL,
                fine_amount REAL,
                penalty_status TEXT NOT NULL DEFAULT 'ISSUED',
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(violations)")}
        if "fine_amount" not in columns:
            self.connection.execute("ALTER TABLE violations ADD COLUMN fine_amount REAL")
        if "penalty_status" not in columns:
            self.connection.execute("ALTER TABLE violations ADD COLUMN penalty_status TEXT NOT NULL DEFAULT 'ISSUED'")
        self.connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalise(embedding: Sequence[float]) -> np.ndarray:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vector)
        if vector.size == 0 or norm == 0:
            raise ValueError("Embedding must be a non-zero vector")
        return vector / norm

    def add_user(self, user_id: str, name: str, embedding: Sequence[float], account_ref: Optional[str] = None) -> None:
        vector = self._normalise(embedding)
        self.connection.execute(
            "INSERT OR REPLACE INTO users(user_id, name, embedding, account_ref, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, json.dumps(vector.tolist()), account_ref, self._now()),
        )
        self.connection.commit()

    def identify(self, embedding: Sequence[float]) -> Optional[dict]:
        query = self._normalise(embedding)
        best_match = None
        best_score = -1.0
        for row in self.connection.execute("SELECT user_id, name, embedding, account_ref FROM users"):
            stored = self._normalise(json.loads(row["embedding"]))
            if stored.shape != query.shape:
                continue
            score = float(np.dot(query, stored))
            if score > best_score:
                best_score = score
                best_match = dict(row)
        if best_match is None or best_score < self.recognition_threshold:
            return None
        best_match["similarity"] = best_score
        return best_match

    def register_entry(self, user_id: str, embedding: Sequence[float]) -> bool:
        """Create one unpaid ride session for a recognized user."""
        with self.lock:
            now = self._now()
            active = self.connection.execute(
                "SELECT session_id FROM ride_sessions WHERE user_id = ? AND exit_time IS NULL", (user_id,)
            ).fetchone()
            if active:
                self.connection.execute("UPDATE ride_sessions SET last_seen = ? WHERE session_id = ?", (now, active["session_id"]))
                self.connection.commit()
                return False
            self.connection.execute(
                "INSERT INTO ride_sessions(user_id, status, entry_time, last_seen) VALUES (?, 'UNPAID', ?, ?)",
                (user_id, now, now),
            )
            self.connection.commit()
            self.in_bus_passengers[user_id] = {"paid": False, "entry_time": time.monotonic()}
        logger.info("[ENTRY] Passenger %s entered.", user_id)
        return True

    def identify_and_register(self, embedding: Sequence[float]) -> Optional[dict]:
        user = self.identify(embedding)
        if user:
            self.register_entry(user["user_id"], embedding)
        return user

    def mark_paid(self, user_id: str, amount: Optional[float] = None, provider_ref: Optional[str] = None) -> bool:
        with self.lock:
            session = self.connection.execute(
                "SELECT session_id FROM ride_sessions WHERE user_id = ? AND exit_time IS NULL ORDER BY session_id DESC LIMIT 1", (user_id,)
            ).fetchone()
            if not session:
                logger.warning("[PAYMENT] No active session for %s", user_id)
                return False
            self.connection.execute("UPDATE ride_sessions SET status = 'PAID' WHERE session_id = ?", (session["session_id"],))
            self.connection.execute(
                "INSERT INTO transactions(session_id, user_id, amount, provider_ref, created_at) VALUES (?, ?, ?, ?, ?)",
                (session["session_id"], user_id, amount, provider_ref, self._now()),
            )
            self.connection.commit()
            if user_id in self.in_bus_passengers:
                self.in_bus_passengers[user_id]["paid"] = True
        logger.info("[PAYMENT] Passenger %s paid.", user_id)
        return True

    def check_exit(
        self,
        user_id: str,
        photo_path: Optional[str] = None,
        route: Optional[str] = None,
        fine_amount: Optional[float] = None,
    ) -> Optional[str]:
        session = self.connection.execute(
            "SELECT session_id, status FROM ride_sessions WHERE user_id = ? AND exit_time IS NULL ORDER BY session_id DESC LIMIT 1", (user_id,)
        ).fetchone()
        if not session:
            return None
        now = self._now()
        self.connection.execute("UPDATE ride_sessions SET exit_time = ? WHERE session_id = ?", (now, session["session_id"]))
        if session["status"] == "UNPAID":
            self.connection.execute(
                "INSERT INTO violations(user_id, photo_path, route, violation_type, fine_amount, created_at) VALUES (?, ?, ?, 'EXIT_WITHOUT_PAYMENT', ?, ?)",
                (user_id, photo_path, route, fine_amount, now),
            )
            result = "VIOLATION"
            logger.error("[VIOLATION] Passenger %s exited without payment.", user_id)
        else:
            result = "OK"
            logger.info("[EXIT] Passenger %s exited after payment.", user_id)
        self.connection.commit()
        self.in_bus_passengers.pop(user_id, None)
        return result

    def process_face_event(self, user_id: str, photo_path: Optional[str] = None, route: Optional[str] = None, fine_amount: Optional[float] = None) -> str:
        """Turn a recognized face into ENTRY, DEBOUNCED, or an exit result."""
        with self.lock:
            state = self.in_bus_passengers.get(user_id)
            if state is None:
                self.register_entry(user_id, [])
                return "ENTRY"
            if time.monotonic() - state["entry_time"] < self.exit_debounce_seconds:
                return "DEBOUNCED"
            return self.check_exit(user_id, photo_path, route, fine_amount) or "EXIT"

    def cleanup_stale_sessions(self):
        """Удаление зависших сессий."""
        cutoff = datetime.fromtimestamp(time.time() - self.session_timeout, timezone.utc).isoformat()
        stale = self.connection.execute(
            "SELECT user_id FROM ride_sessions WHERE exit_time IS NULL AND last_seen < ?", (cutoff,)
        ).fetchall()
        for row in stale:
            logger.warning("[TIMEOUT] Closing stale session for %s.", row["user_id"])
        self.connection.execute("UPDATE ride_sessions SET exit_time = ? WHERE exit_time IS NULL AND last_seen < ?", (self._now(), cutoff))
        self.connection.commit()

    def active_sessions(self) -> List[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT user_id, status FROM ride_sessions WHERE exit_time IS NULL ORDER BY session_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_unknown_violation(self, photo_path: str, route: Optional[str] = None) -> None:
        self.connection.execute(
            "INSERT INTO violations(user_id, photo_path, route, violation_type, penalty_status, created_at) VALUES (NULL, ?, ?, 'UNKNOWN_FACE', 'REVIEW', ?)",
            (photo_path, route, self._now()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
