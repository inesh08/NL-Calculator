import hashlib
import hmac
import importlib
import json
import os
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

from prompt import Operation


class FakeSlackApp:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def event(self, _name):
        return lambda handler: handler


with patch.dict(os.environ, {"OPENAI_API_KEY": "test-api-key", "SLACK_BOT_TOKEN": "xoxb-test"}), \
     patch("storage.initialize_database"), \
     patch("slack_bolt.App", FakeSlackApp):
    slack_app_module = importlib.import_module("slack_app")


class SlackHelperTests(unittest.TestCase):
    def tearDown(self):
        with slack_app_module._pending_lock:
            slack_app_module._pending_clarifications.clear()
        with slack_app_module._seen_lock:
            slack_app_module._seen_event_ids.clear()
        while True:
            try:
                slack_app_module._events.get_nowait()
                slack_app_module._events.task_done()
            except queue.Empty:
                break

    def test_verify_slack_request_accepts_valid_signature_and_rejects_invalid_or_stale(self):
        body = b'{"type":"event_callback"}'
        timestamp = str(int(time.time()))
        base = b"v0:" + timestamp.encode() + b":" + body
        signature = "v0=" + hmac.new(b"secret", base, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "secret"}):
            self.assertTrue(slack_app_module._verify_slack_request(body, timestamp, signature))
            self.assertFalse(slack_app_module._verify_slack_request(body, timestamp, "v0=bad"))
            self.assertFalse(slack_app_module._verify_slack_request(body, "not-a-time", signature))
            stale = str(int(time.time()) - 600)
            self.assertFalse(slack_app_module._verify_slack_request(body, stale, signature))

    def test_clean_mention_removes_user_tags_and_whitespace(self):
        self.assertEqual(slack_app_module._clean_mention(" <@U123> 12 times 8 "), "12 times 8")

    def test_post_message_sends_slack_payload(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok": true}'
        with patch.object(slack_app_module.os, "getenv", return_value="xoxb-token"), \
             patch.object(slack_app_module, "urlopen", return_value=response) as urlopen:
            slack_app_module._post_message("C1", "Result: 8", "123.45")
        req = urlopen.call_args.args[0]
        self.assertEqual(json.loads(req.data), {"channel": "C1", "text": "Result: 8", "thread_ts": "123.45"})

    def test_post_message_raises_for_transport_and_slack_api_errors(self):
        with patch.object(slack_app_module.os, "getenv", return_value="xoxb-token"), \
             patch.object(slack_app_module, "urlopen", side_effect=URLError("offline")), \
             self.assertRaisesRegex(RuntimeError, "delivery failed"):
            slack_app_module._post_message("C1", "text", "123")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok": false, "error": "not_in_channel"}'
        with patch.object(slack_app_module.os, "getenv", return_value="xoxb-token"), \
             patch.object(slack_app_module, "urlopen", return_value=response), \
             self.assertRaisesRegex(RuntimeError, "not_in_channel"):
            slack_app_module._post_message("C1", "text", "123")
        with patch.object(slack_app_module.os, "getenv", return_value=""), \
             self.assertRaisesRegex(RuntimeError, "SLACK_BOT_TOKEN"):
            slack_app_module._post_message("C1", "text", "123")

    def test_calculate_reply_saves_and_posts_calculation(self):
        op = Operation(outcome="calculation", message="", operation="multiply", a=4.0, b=3.0)
        event = {"channel": "C1", "user": "U1", "text": "<@B1> 4 times 3", "ts": "123", "_event_id": "E1"}
        with patch.object(slack_app_module, "get_tracked_operation", return_value=op), \
             patch.object(slack_app_module, "calculate", return_value=12), \
             patch.object(slack_app_module, "save_exchange") as save, \
             patch.object(slack_app_module, "_post_message") as post:
            slack_app_module._calculate_reply(event)
        self.assertEqual(save.call_args.kwargs["question"], "4 times 3")
        self.assertEqual(save.call_args.kwargs["answer"], "Result: 12")
        self.assertEqual(save.call_args.kwargs["source_event_id"], "E1")
        post.assert_called_once_with("C1", "Result: 12", "123")

    def test_calculate_reply_handles_clarification_and_subsequent_context(self):
        event = {"channel": "C1", "user": "U1", "text": "ambiguous", "ts": "123"}
        clarification = Operation(outcome="clarification", message="Which one?", operation=None, a=None, b=None)
        with patch.object(slack_app_module, "get_tracked_operation", return_value=clarification), \
             patch.object(slack_app_module, "save_exchange") as save, \
             patch.object(slack_app_module, "_post_message") as post:
            slack_app_module._calculate_reply(event)
        self.assertEqual(save.call_args.kwargs["answer"], "Which one? Reply in this thread and mention me to clarify.")
        post.assert_called_once()

        next_event = {"channel": "C1", "user": "U1", "text": "second option", "ts": "124", "thread_ts": "123"}
        final = Operation(outcome="out_of_scope", message="No calculation.", operation=None, a=None, b=None)
        with patch.object(slack_app_module, "get_tracked_operation", return_value=final) as tracked, \
             patch.object(slack_app_module, "save_exchange") as save, \
             patch.object(slack_app_module, "_post_message"):
            slack_app_module._calculate_reply(next_event)
        self.assertEqual(tracked.call_args.args[0], "Original request: ambiguous\nClarification question: Which one?\nUser's clarification: second option")
        self.assertEqual(save.call_args.kwargs["question"], "second option")

    def test_calculate_reply_handles_bad_events_empty_text_and_errors(self):
        with patch.object(slack_app_module, "_post_message") as post:
            slack_app_module._calculate_reply({"channel": "C1"})
            self.assertFalse(post.called)
            slack_app_module._calculate_reply({"channel": "C1", "user": "U1", "text": "<@B1>", "ts": "1"})
            self.assertIn("Send me a calculation", post.call_args.args[1])

        event = {"channel": "C1", "user": "U1", "text": "bad", "ts": "125"}
        with patch.object(slack_app_module, "get_tracked_operation", side_effect=ValueError("bad input")), \
             patch.object(slack_app_module, "save_exchange") as save, \
             patch.object(slack_app_module, "_post_message") as post:
            slack_app_module._calculate_reply(event)
        self.assertEqual(save.call_args.kwargs["answer"], "Unable to calculate: bad input")
        post.assert_called_once()

        with patch.object(slack_app_module, "get_tracked_operation", side_effect=RuntimeError("provider")), \
             patch.object(slack_app_module, "save_exchange") as save, \
             patch.object(slack_app_module, "_post_message") as post, \
             self.assertLogs("nl_calculator.slack", level="ERROR"):
            slack_app_module._calculate_reply(event)
        self.assertEqual(save.call_args.kwargs["answer"], "Unable to process the request right now. Please try again shortly.")
        post.assert_called_once()

    def test_worker_processes_item_and_always_marks_queue_task_done(self):
        class OneThenStop:
            def __init__(self):
                self.calls = 0
                self.done = 0

            def get(self):
                self.calls += 1
                if self.calls == 1:
                    return {"id": 1}
                raise StopIteration

            def task_done(self):
                self.done += 1

        fake_queue = OneThenStop()
        with patch.object(slack_app_module, "_events", fake_queue), \
             patch.object(slack_app_module, "_calculate_reply", side_effect=ValueError("bad")), \
             self.assertLogs("nl_calculator.slack", level="ERROR"):
            with self.assertRaises(StopIteration):
                slack_app_module._worker()
        self.assertEqual(fake_queue.done, 1)

    def test_accept_event_queues_once_per_event_id(self):
        slack_app_module._accept_event({"text": "calc"}, "E2")
        slack_app_module._accept_event({"text": "duplicate"}, "E2")
        self.assertEqual(slack_app_module._events.qsize(), 1)
        self.assertEqual(slack_app_module._events.get_nowait()["_event_id"], "E2")
        slack_app_module._events.task_done()
        slack_app_module._accept_event({"text": "no id"}, None)
        self.assertEqual(slack_app_module._events.qsize(), 1)

    def test_app_mention_handler_ignores_bot_and_accepts_user(self):
        with patch.object(slack_app_module, "_accept_event") as accept:
            slack_app_module.handle_app_mention({"bot_id": "B1"}, {"event_id": "E"}, Mock())
            accept.assert_not_called()
            event = {"user": "U1", "text": "3 plus 4"}
            slack_app_module.handle_app_mention(event, {"event_id": "E"}, Mock())
        accept.assert_called_once_with(event, "E")

    def test_flask_health_and_slack_event_routes(self):
        client = slack_app_module.app.test_client()
        self.assertEqual(client.get("/health").json, {"status": "ok"})
        with patch.object(slack_app_module, "_verify_slack_request", return_value=False):
            self.assertEqual(client.post("/slack/events", data="{}").status_code, 401)
        with patch.object(slack_app_module, "_verify_slack_request", return_value=True):
            challenge = client.post("/slack/events", json={"type": "url_verification", "challenge": "abc"})
            self.assertEqual(challenge.json, {"challenge": "abc"})
            invalid_json = client.post("/slack/events", data=b"not-json")
            self.assertEqual(invalid_json.status_code, 400)
            ignored = client.post("/slack/events", json={"type": "event_callback", "event": {"type": "message"}})
            self.assertEqual(ignored.json, {"ok": True})
            with patch.object(slack_app_module, "_accept_event") as accept:
                accepted = client.post("/slack/events", json={"type": "event_callback", "event_id": "E3",
                                                               "event": {"type": "app_mention", "text": "calc"}})
            self.assertEqual(accepted.json, {"ok": True})
            accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
