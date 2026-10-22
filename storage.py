"""PostgreSQL persistence for visible exchanges and operational health data."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("nl_calculator.storage")


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return url


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row, connect_timeout=5)


def initialize_database() -> None:
    """Create the application-owned tables if they do not already exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_exchanges (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                source TEXT NOT NULL CHECK (source IN ('slack', 'terminal')),
                conversation_id TEXT NOT NULL,
                source_event_id TEXT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source, source_event_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS conversation_exchanges_created_at_idx "
            "ON conversation_exchanges (created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS conversation_exchanges_conversation_idx "
            "ON conversation_exchanges (source, conversation_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_activity (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS llm_activity_occurred_at_idx "
            "ON llm_activity (occurred_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_runs (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                database_ok BOOLEAN NOT NULL,
                llm_status TEXT NOT NULL CHECK (llm_status IN ('healthy', 'degraded', 'unknown')),
                llm_successes INTEGER NOT NULL,
                llm_failures INTEGER NOT NULL,
                checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                details TEXT NOT NULL DEFAULT ''
            )
            """
        )


def save_exchange(
    *,
    source: str,
    conversation_id: str,
    question: str,
    answer: str,
    source_event_id: str | None = None,
) -> bool:
    """Save only the user-visible question and answer, plus routing metadata."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_exchanges
                    (source, conversation_id, source_event_id, question, answer)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source, source_event_id) DO NOTHING
                RETURNING id
                """,
                (source, conversation_id, source_event_id, question, answer),
            )
            return cursor.fetchone() is not None
    except Exception:
        logger.exception("Could not save %s conversation exchange", source)
        return False


def record_llm_activity(outcome: str) -> None:
    """Record success/failure only; never persist prompts or model output here."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO llm_activity (outcome) VALUES (%s)", (outcome,)
            )
    except Exception:
        logger.exception("Could not record LLM activity")


def get_llm_activity(since: datetime) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE outcome = 'success')::int AS successes,
                count(*) FILTER (WHERE outcome = 'failure')::int AS failures,
                max(occurred_at) FILTER (WHERE outcome = 'success') AS last_success
            FROM llm_activity
            WHERE occurred_at >= %s
            """,
            (since,),
        ).fetchone()
        return row or {"successes": 0, "failures": 0, "last_success": None}


def record_monitor_run(
    *, database_ok: bool, llm_status: str, successes: int, failures: int, details: str = ""
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO monitor_runs
                (database_ok, llm_status, llm_successes, llm_failures, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (database_ok, llm_status, successes, failures, details),
        )


def check_database() -> None:
    with _connect() as conn:
        conn.execute("SELECT 1")

