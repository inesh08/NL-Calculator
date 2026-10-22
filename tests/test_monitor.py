import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import monitor


class MonitorTests(unittest.TestCase):
    def test_health_records_unknown_when_no_recent_llm_activity(self):
        activity = {"successes": 0, "failures": 0, "last_success": None}
        with patch("monitor.check_database") as check_db, \
             patch("monitor.get_llm_activity", return_value=activity) as get_activity, \
             patch("monitor.record_monitor_run") as record:
            monitor.check_health()
        check_db.assert_called_once()
        since = get_activity.call_args.args[0]
        self.assertLessEqual((datetime.now(timezone.utc) - since).total_seconds(), 125)
        self.assertEqual(record.call_args.kwargs["llm_status"], "unknown")
        self.assertTrue(record.call_args.kwargs["database_ok"])

    def test_health_records_healthy_and_degraded_statuses(self):
        for successes, failures, expected in [(2, 0, "healthy"), (1, 1, "degraded"), (0, 1, "degraded")]:
            activity = {"successes": successes, "failures": failures, "last_success": None}
            with self.subTest(expected=expected), \
                 patch("monitor.check_database"), \
                 patch("monitor.get_llm_activity", return_value=activity), \
                 patch("monitor.record_monitor_run") as record:
                monitor.check_health()
            self.assertEqual(record.call_args.kwargs["llm_status"], expected)
            self.assertEqual(record.call_args.kwargs["successes"], successes)
            self.assertEqual(record.call_args.kwargs["failures"], failures)

    def test_health_logs_and_attempts_failure_record_when_check_fails(self):
        with patch("monitor.check_database", side_effect=RuntimeError("db down")), \
             patch("monitor.record_monitor_run") as record, \
             self.assertLogs("nl_calculator.monitor", level="ERROR"):
            monitor.check_health()
        self.assertFalse(record.call_args.kwargs["database_ok"])
        self.assertEqual(record.call_args.kwargs["llm_status"], "unknown")

    def test_health_handles_failure_to_persist_failed_check(self):
        with patch("monitor.check_database", side_effect=RuntimeError("db down")), \
             patch("monitor.record_monitor_run", side_effect=RuntimeError("still down")), \
             self.assertLogs("nl_calculator.monitor", level="ERROR"):
            monitor.check_health()  # Errors are contained and logged.

    def test_main_initializes_runs_immediate_check_and_schedules_every_two_minutes(self):
        scheduler = Mock()
        scheduler_type = Mock(return_value=scheduler)
        with patch("monitor.initialize_database") as initialize, \
             patch("monitor.BlockingScheduler", scheduler_type), \
             patch("monitor.check_health") as check:
            monitor.main()
        initialize.assert_called_once()
        check.assert_called_once()
        self.assertEqual(scheduler.add_job.call_args.kwargs["minute"], "*/2")
        scheduler.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
