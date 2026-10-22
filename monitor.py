"""Standalone 30-minute PostgreSQL and observed-LLM health monitor."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()

from storage import check_database, get_llm_activity, initialize_database, record_monitor_run

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nl_calculator.monitor")


def check_health() -> None:
    now = datetime.now(timezone.utc)
    try:
        check_database()
        activity = get_llm_activity(now - timedelta(minutes=30))
        successes = activity["successes"]
        failures = activity["failures"]
        if successes == 0 and failures == 0:
            llm_status = "unknown"
            details = "No LLM requests observed in the last 30 minutes; no synthetic probe was run."
        elif failures:
            llm_status = "degraded"
            details = "One or more LLM requests failed in the last 30 minutes."
        else:
            llm_status = "healthy"
            details = "Observed LLM requests succeeded in the last 30 minutes."
        record_monitor_run(database_ok=True, llm_status=llm_status,
                           successes=successes, failures=failures, details=details)
        logger.info("Health check: database=healthy llm=%s successes=%d failures=%d",
                    llm_status, successes, failures)
    except Exception as error:
        logger.exception("Health check failed")
        # If PostgreSQL is unavailable, its monitor_runs table cannot record this failure.
        try:
            record_monitor_run(database_ok=False, llm_status="unknown", successes=0,
                               failures=0, details=f"Database/monitor check failed: {error}")
        except Exception:
            logger.exception("Could not persist health-check failure")


def main() -> None:
    initialize_database()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_health, trigger="cron", minute="*/30",
                      id="postgres_and_llm_health", replace_existing=True,
                      max_instances=1, coalesce=True)
    check_health()
    logger.info("Starting health checks every 30 minutes")
    scheduler.start()


if __name__ == "__main__":
    main()
