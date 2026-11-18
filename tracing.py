"""Zoroval tracing for NL-Calculator, shared by the Slack bot and the terminal app.

Tracing is optional. Without ``ZOROVAL_INGEST_KEY`` the SDK stays off, ``turn`` does
nothing, and the app behaves exactly as it did before tracing existed.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

import zoroval

logger = logging.getLogger("nl_calculator.tracing")

# Explicit export limits for slow links. The SDK exports on its own background thread, so
# none of these can delay a reply; they only bound memory and how long exit may wait.
#  - max_queue_spans / max_queue_bytes: this app emits a few small spans per message, so a
#    small buffer is plenty (oldest spans are dropped when full, never the app).
#  - shutdown_timeout: the longest the process waits at exit to send what is queued.
_EXPORT_LIMITS = {"max_queue_spans": 1000, "max_queue_bytes": 4 * 1024 * 1024, "shutdown_timeout": 3.0}


def setup() -> bool:
    """Start Zoroval tracing if a key is configured. Log one line saying on or off."""
    try:
        # Check the name only. Without a key the SDK would log its own warning, so skip it.
        active = bool(os.environ.get("ZOROVAL_INGEST_KEY", "").strip()) and zoroval.init(**_EXPORT_LIMITS)
    except Exception:
        logger.exception("Zoroval tracing failed to start; continuing without it")
        active = False
    if active:
        logger.info("Zoroval tracing is on")
    else:
        logger.info("Zoroval tracing is off (set ZOROVAL_INGEST_KEY to turn it on)")
    return active


@contextmanager
def turn(conversation_id: str, question: str, user_id: str | None = None) -> Iterator:
    """One user turn inside one Zoroval conversation.

    ``conversation_id`` is the app's own id (Slack ``channel:thread_ts``, terminal
    ``terminal-<uuid>``), so every turn in a thread joins the same conversation.
    """
    with zoroval.conversation(conversation_id, user_id=user_id):
        with zoroval.turn(input=question) as observation:
            yield observation
