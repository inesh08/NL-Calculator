import io
import os
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest.mock import MagicMock, patch

import main
import tracing
from prompt import Operation
from tests.test_slack_app import slack_app_module


def _env_without_zoroval():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZOROVAL_")}
    return patch.dict(os.environ, env, clear=True)


class RecordingTurn:
    """Stand-in for tracing.turn that records (conversation_id, question, user_id)."""

    def __init__(self):
        self.calls = []
        self.outputs = []

    @contextmanager
    def __call__(self, conversation_id, question, user_id=None):
        self.calls.append((conversation_id, question, user_id))
        observation = MagicMock()
        observation.set_output.side_effect = self.outputs.append
        yield observation


class TracingOffTests(unittest.TestCase):
    def test_setup_without_key_is_off_logs_one_line_and_makes_no_network_call(self):
        with _env_without_zoroval(), \
             patch("http.client.HTTPSConnection") as https, patch("urllib.request.urlopen") as urlopen, \
             self.assertLogs("nl_calculator.tracing", level="INFO") as logs:
            self.assertFalse(tracing.setup())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Zoroval tracing is off", logs.output[0])
        https.assert_not_called()
        urlopen.assert_not_called()

    def test_setup_with_disabled_flag_is_off(self):
        with patch.dict(os.environ, {"ZOROVAL_ENABLED": "0"}), \
             self.assertLogs("nl_calculator.tracing", level="INFO") as logs:
            self.assertFalse(tracing.setup())
        self.assertIn("off", logs.output[0])

    def test_setup_logs_on_without_printing_the_key(self):
        secret = "zv_ingest_abcdefghijkl_SECRETSECRETSECRETSECRET"
        with patch.dict(os.environ, {"ZOROVAL_INGEST_KEY": secret}), \
             patch("tracing.zoroval.init", return_value=True) as init, \
             self.assertLogs("nl_calculator.tracing", level="INFO") as logs:
            self.assertTrue(tracing.setup())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Zoroval tracing is on", logs.output[0])
        self.assertNotIn(secret, logs.output[0])
        self.assertNotIn(secret, repr(init.call_args))

    def test_setup_survives_an_sdk_failure(self):
        with patch("tracing.zoroval.init", side_effect=RuntimeError("boom")), \
             self.assertLogs("nl_calculator.tracing", level="INFO"):
            self.assertFalse(tracing.setup())

    def test_turn_is_a_no_op_when_tracing_is_off_and_does_not_hide_errors(self):
        with _env_without_zoroval():
            with tracing.turn("c1", "2 plus 3") as traced:
                traced.set_output("Result: 5")
            with self.assertRaises(ValueError):
                with tracing.turn("c1", "2 plus 3"):
                    raise ValueError("calculator error")

    def test_turn_opens_one_conversation_with_the_given_id(self):
        with patch("tracing.zoroval.conversation") as conversation, patch("tracing.zoroval.turn") as turn:
            with tracing.turn("C1:123", "2 plus 3", user_id="U1"):
                pass
        conversation.assert_called_once_with("C1:123", user_id="U1")
        turn.assert_called_once_with(input="2 plus 3")


class SlackConversationTests(unittest.TestCase):
    def tearDown(self):
        with slack_app_module._pending_lock:
            slack_app_module._pending_clarifications.clear()

    def test_thread_and_its_clarification_reply_share_one_conversation_id(self):
        recorder = RecordingTurn()
        first = {"channel": "C1", "user": "U1", "text": "<@B1> add two", "ts": "123"}
        reply = {"channel": "C1", "user": "U1", "text": "<@B1> two and three", "ts": "124", "thread_ts": "123"}
        clarification = Operation(outcome="clarification", message="Which one?", operation=None, a=None, b=None)
        calculation = Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0)
        with patch.object(slack_app_module.tracing, "turn", recorder), \
             patch.object(slack_app_module, "get_tracked_operation", side_effect=[clarification, calculation]), \
             patch.object(slack_app_module, "calculate", return_value=5), \
             patch.object(slack_app_module, "save_exchange"), patch.object(slack_app_module, "_post_message"):
            slack_app_module._calculate_reply(first)
            slack_app_module._calculate_reply(reply)
        self.assertEqual([c[0] for c in recorder.calls], ["C1:123", "C1:123"])
        self.assertEqual([c[1] for c in recorder.calls], ["add two", "two and three"])
        self.assertEqual(recorder.calls[0][2], "U1")
        self.assertEqual(recorder.outputs[-1], "Result: 5")

    def test_different_threads_are_different_conversations(self):
        recorder = RecordingTurn()
        op = Operation(outcome="out_of_scope", message="No.", operation=None, a=None, b=None)
        with patch.object(slack_app_module.tracing, "turn", recorder), \
             patch.object(slack_app_module, "get_tracked_operation", return_value=op), \
             patch.object(slack_app_module, "save_exchange"), patch.object(slack_app_module, "_post_message"):
            slack_app_module._calculate_reply({"channel": "C1", "user": "U1", "text": "hi", "ts": "1"})
            slack_app_module._calculate_reply({"channel": "C1", "user": "U1", "text": "hi", "ts": "2"})
        self.assertEqual([c[0] for c in recorder.calls], ["C1:1", "C1:2"])

    def test_slack_reply_still_posts_when_tracing_is_off(self):
        op = Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0)
        with patch.object(slack_app_module, "get_tracked_operation", return_value=op), \
             patch.object(slack_app_module, "calculate", return_value=5), \
             patch.object(slack_app_module, "save_exchange"), \
             patch.object(slack_app_module, "_post_message") as post:
            slack_app_module._calculate_reply({"channel": "C1", "user": "U1", "text": "2 plus 3", "ts": "9"})
        post.assert_called_once_with("C1", "Result: 5", "9")


class TerminalConversationTests(unittest.TestCase):
    def test_clarification_turns_share_one_terminal_conversation_id(self):
        recorder = RecordingTurn()
        clarification = Operation(outcome="clarification", message="Which one?", operation=None, a=None, b=None)
        calculation = Operation(outcome="calculation", message="", operation="add", a=2.0, b=3.0)
        output = io.StringIO()
        with patch("main.initialize_database"), patch("main.save_exchange"), \
             patch("main.tracing.turn", recorder), \
             patch("builtins.input", side_effect=["add two", "two and three"]), \
             patch("main.get_tracked_operation", side_effect=[clarification, calculation]), \
             patch("main.calculate", return_value=5), redirect_stdout(output):
            main.main()
        ids = [c[0] for c in recorder.calls]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        self.assertTrue(ids[0].startswith("terminal-"))
        self.assertEqual([c[1] for c in recorder.calls], ["add two", "two and three"])
        self.assertIn("Result: 5", output.getvalue())


if __name__ == "__main__":
    unittest.main()
