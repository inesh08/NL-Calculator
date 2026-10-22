"""Slack Events API and Socket Mode adapter for NL-Calculator.

Run ``python slack_app.py`` with ``SLACK_APP_TOKEN`` configured to use Socket
Mode. The Flask endpoint remains available as an HTTP Events API fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import re
import threading
import time
from collections import OrderedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request
from slack_bolt import App as SlackApp
from slack_bolt.adapter.socket_mode import SocketModeHandler

from calculator import calculate
from interactions import get_tracked_operation
from storage import initialize_database, save_exchange

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nl_calculator.slack")

try:
    initialize_database()
except Exception:
    logger.exception("Database initialization failed; conversation saving will be unavailable")

app = Flask(__name__)
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
slack_app = SlackApp(token=slack_bot_token) if slack_bot_token else None
_events: queue.Queue[dict] = queue.Queue()
_seen_event_ids: OrderedDict[str, None] = OrderedDict()
_seen_lock = threading.Lock()
_pending_clarifications: dict[tuple[str, str, str], tuple[str, str]] = {}
_pending_lock = threading.Lock()


def _verify_slack_request(body: bytes, timestamp: str | None, signature: str | None) -> bool:
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - request_time) > 60 * 5:
        return False
    basestring = b"v0:" + timestamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _post_message(channel: str, text: str, thread_ts: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")
    payload = json.dumps(
        {"channel": channel, "text": text, "thread_ts": thread_ts}
    ).encode("utf-8")
    req = Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Slack message delivery failed: {error}") from error
    if not result.get("ok"):
        raise RuntimeError(f"Slack message delivery failed: {result.get('error', 'unknown_error')}")


def _clean_mention(text: str) -> str:
    # Slack encodes user mentions as <@U123...>; remove them before parsing.
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def _calculate_reply(event: dict) -> None:
    channel = event.get("channel")
    user = event.get("user")
    text = _clean_mention(event.get("text", ""))
    thread_ts = event.get("thread_ts") or event.get("ts")
    source_event_id = event.get("_event_id") or event.get("ts")
    if not channel or not user or not thread_ts:
        return
    if not text:
        _post_message(channel, "Send me a calculation, such as `@NL Calculator 12 times 8`.", thread_ts)
        return

    key = (channel, thread_ts, user)
    visible_question = text
    with _pending_lock:
        pending = _pending_clarifications.pop(key, None)
    if pending:
        original, clarification = pending
        text = (
            f"Original request: {original}\n"
            f"Clarification question: {clarification}\n"
            f"User's clarification: {text}"
        )

    try:
        operation = get_tracked_operation(text)
        if operation.outcome == "out_of_scope":
            reply = operation.message
        elif operation.outcome == "clarification":
            with _pending_lock:
                _pending_clarifications[key] = (text, operation.message)
            reply = operation.message + " Reply in this thread and mention me to clarify."
        else:
            result = calculate(operation)
            reply = f"Result: {result}"
    except ValueError as error:
        reply = f"Unable to calculate: {error}"
    except Exception:
        logger.exception("Unable to process Slack calculation")
        reply = "Unable to process the request right now. Please try again shortly."

    save_exchange(source="slack", conversation_id=f"{channel}:{thread_ts}",
                  source_event_id=source_event_id,
                  question=visible_question, answer=reply)
    _post_message(channel, reply, thread_ts)


def _worker() -> None:
    while True:
        event = _events.get()
        try:
            _calculate_reply(event)
        except Exception:
            logger.exception("Failed to handle Slack event")
        finally:
            _events.task_done()


threading.Thread(target=_worker, name="slack-event-worker", daemon=True).start()


def _accept_event(event: dict, event_id: str | None) -> None:
    if event_id:
        with _seen_lock:
            if event_id in _seen_event_ids:
                return
            _seen_event_ids[event_id] = None
            while len(_seen_event_ids) > 5000:
                _seen_event_ids.popitem(last=False)
    queued_event = dict(event)
    queued_event["_event_id"] = event_id
    _events.put(queued_event)


if slack_app is not None:

    @slack_app.event("app_mention")
    def handle_app_mention(event: dict, body: dict, logger) -> None:
        """Queue a bot mention received over Slack Socket Mode."""
        if event.get("bot_id"):
            return
        _accept_event(event, body.get("event_id"))


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/slack/events")
def slack_events():
    body = request.get_data(cache=False)
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    signature = request.headers.get("X-Slack-Signature")
    if not _verify_slack_request(body, timestamp, signature):
        return jsonify(error="invalid_signature"), 401

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify(error="invalid_json"), 400

    if payload.get("type") == "url_verification":
        return jsonify(challenge=payload.get("challenge", ""))

    if payload.get("type") != "event_callback":
        return jsonify(ok=True)

    event = payload.get("event", {})
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return jsonify(ok=True)

    _accept_event(event, payload.get("event_id"))
    return jsonify(ok=True)


if __name__ == "__main__":
    app_token = os.getenv("SLACK_APP_TOKEN")
    if app_token:
        if slack_app is None:
            raise RuntimeError("SLACK_BOT_TOKEN must be configured for Socket Mode")
        SocketModeHandler(slack_app, app_token).start()
    else:
        # HTTP Events API fallback. Slack requires a publicly reachable HTTPS URL.
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")))
