import tempfile
import threading
import unittest
from http.client import HTTPConnection

from payment_api import serve_payment_api
from passenger_tracker import PassengerTracker


class PassengerTrackerTests(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(suffix=".db")
        self.tracker = PassengerTracker(database_path=self.database.name, recognition_threshold=0.9)
        self.tracker.add_user("u1", "Alice", [1.0, 0.0])

    def tearDown(self):
        self.tracker.close()
        self.database.close()

    def test_paid_exit_closes_session_without_violation(self):
        self.tracker.identify_and_register([1.0, 0.0])
        self.assertTrue(self.tracker.mark_paid("u1", amount=2.5, provider_ref="tap-1"))
        self.assertEqual(self.tracker.check_exit("u1", route="R1"), "OK")
        self.assertEqual(self.tracker.connection.execute("SELECT COUNT(*) FROM violations").fetchone()[0], 0)

    def test_unpaid_exit_creates_violation(self):
        self.tracker.identify_and_register([1.0, 0.0])
        self.assertEqual(self.tracker.check_exit("u1", "/tmp/face.jpg", "R1"), "VIOLATION")
        violation = self.tracker.connection.execute("SELECT user_id, route FROM violations").fetchone()
        self.assertEqual((violation["user_id"], violation["route"]), ("u1", "R1"))

    def test_unknown_or_low_similarity_face_is_rejected(self):
        self.assertIsNone(self.tracker.identify([0.0, 1.0]))
        self.tracker.record_unknown_violation("unknown.jpg", "R1")
        self.assertEqual(self.tracker.connection.execute("SELECT COUNT(*) FROM violations").fetchone()[0], 1)

    def test_payment_api_updates_active_session(self):
        self.tracker.identify_and_register([1.0, 0.0])
        server = serve_payment_api(self.tracker, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request(
                "POST",
                "/payments",
                '{"user_id":"u1","amount":2.5,"provider_ref":"tap-1"}',
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(self.tracker.active_sessions()[0]["status"], "PAID")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()