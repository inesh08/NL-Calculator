import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import storage


class FakeConnection:
    def __init__(self, row=None):
        self.row = row
        self.statements = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True

    def execute(self, query, params=None):
        self.statements.append((query, params))
        return self

    def fetchone(self):
        return self.row


class StorageTests(unittest.TestCase):
    def test_database_url_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            storage._database_url()

    def test_database_url_returns_configured_value(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example/db"}, clear=True):
            self.assertEqual(storage._database_url(), "postgresql://example/db")

    def test_connect_uses_psycopg_and_configured_url(self):
        with patch("storage._database_url", return_value="postgresql://example/db"), \
             patch("storage.psycopg.connect", return_value="connection") as connect:
            self.assertEqual(storage._connect(), "connection")
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 5)

    def test_initialize_database_creates_tables_and_indexes(self):
        conn = FakeConnection()
        with patch("storage._connect", return_value=conn):
            storage.initialize_database()
        sql = "\n".join(statement for statement, _ in conn.statements)
        for name in ("conversation_exchanges", "llm_activity", "monitor_runs",
                     "conversation_exchanges_created_at_idx", "llm_activity_occurred_at_idx"):
            with self.subTest(name=name):
                self.assertIn(name, sql)

    def test_save_exchange_inserts_visible_fields(self):
        conn = FakeConnection(row={"id": 12})
        with patch("storage._connect", return_value=conn):
            saved = storage.save_exchange(source="terminal", conversation_id="term-1",
                                          question="2 plus 3", answer="Result: 5")
        self.assertTrue(saved)
        self.assertEqual(conn.statements[0][1], ("terminal", "term-1", None, "2 plus 3", "Result: 5"))

    def test_save_exchange_reports_duplicate_and_database_error(self):
        with patch("storage._connect", return_value=FakeConnection(row=None)):
            self.assertFalse(storage.save_exchange(source="slack", conversation_id="C:T",
                                                   question="2+3", answer="5", source_event_id="evt"))
        with patch("storage._connect", side_effect=RuntimeError("offline")), \
             self.assertLogs("nl_calculator.storage", level="ERROR"):
            self.assertFalse(storage.save_exchange(source="terminal", conversation_id="term",
                                                   question="2+3", answer="5"))

    def test_record_llm_activity_inserts_outcome_and_handles_failure(self):
        conn = FakeConnection()
        with patch("storage._connect", return_value=conn):
            storage.record_llm_activity("success")
        self.assertEqual(conn.statements[0][1], ("success",))
        with patch("storage._connect", side_effect=RuntimeError("offline")), \
             self.assertLogs("nl_calculator.storage", level="ERROR"):
            storage.record_llm_activity("failure")  # Persistence failure is logged, not raised.

    def test_get_llm_activity_returns_counts_since_cutoff(self):
        row = {"successes": 2, "failures": 1, "last_success": datetime.now(timezone.utc)}
        conn = FakeConnection(row=row)
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("storage._connect", return_value=conn):
            self.assertEqual(storage.get_llm_activity(cutoff), row)
        self.assertEqual(conn.statements[0][1], (cutoff,))

    def test_get_llm_activity_fallback_for_no_row(self):
        with patch("storage._connect", return_value=FakeConnection(row=None)):
            self.assertEqual(storage.get_llm_activity(datetime.now(timezone.utc)),
                             {"successes": 0, "failures": 0, "last_success": None})

    def test_record_monitor_run_persists_status_fields(self):
        conn = FakeConnection()
        with patch("storage._connect", return_value=conn):
            storage.record_monitor_run(database_ok=True, llm_status="healthy", successes=3,
                                       failures=0, details="ok")
        self.assertEqual(conn.statements[0][1], (True, "healthy", 3, 0, "ok"))

    def test_check_database_runs_probe_and_propagates_errors(self):
        conn = FakeConnection()
        with patch("storage._connect", return_value=conn):
            storage.check_database()
        self.assertEqual(conn.statements[0][0], "SELECT 1")
        with patch("storage._connect", side_effect=RuntimeError("offline")), \
             self.assertRaisesRegex(RuntimeError, "offline"):
            storage.check_database()


if __name__ == "__main__":
    unittest.main()
