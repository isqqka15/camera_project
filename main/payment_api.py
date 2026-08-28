import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from passenger_tracker import PassengerTracker

logger = logging.getLogger(__name__)


class PaymentHandler(BaseHTTPRequestHandler):
    tracker: PassengerTracker

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
        elif self.path == "/sessions":
            self._json(HTTPStatus.OK, self.tracker.active_sessions())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/payments":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            user_id = str(payload["user_id"])
            paid = self.tracker.mark_paid(user_id, payload.get("amount"), payload.get("provider_ref"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if not paid:
            self._json(HTTPStatus.NOT_FOUND, {"error": "no active session"})
            return
        self._json(HTTPStatus.OK, {"user_id": user_id, "status": "PAID"})

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("validator %s", format % args)


def serve_payment_api(tracker: PassengerTracker, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    handler = type("ConfiguredPaymentHandler", (PaymentHandler,), {"tracker": tracker})
    server = ThreadingHTTPServer((host, port), handler)
    logger.info("Payment API listening on %s:%s", host, port)
    return server