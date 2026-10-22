# NL-Calculator

## Golden dataset

`test case.json` is a versioned, human-reviewable benchmark for the
natural-language calculator. Each line is one independent case. The set has 20
cases: 14 common calculations, 4 edge cases, and 2 adversarial or out-of-scope
requests. Cases cover four operations, number words, operand order, decimals,
negative numbers, chained operations, ambiguity, division by zero, large
numbers, and prompt injection.

Each record contains:

- `input`: the exact user request.
- `expected`: the expected classification and extracted operation/operands.
  `message` is empty for calculations and gives the expected response intent for
  clarification or out-of-scope cases.
- `reference_result`: expected numeric result when the request is executable;
  `null` for rejected calculations or cases with no calculation.
- `checks` and `must_not`: evaluation criteria and safety bounds.
- `metadata`: category, difficulty, and provenance for slicing.

All current examples are synthetic seeds, not production data or SME-approved
gold. Have a domain reviewer verify the reference interpretations before using
this set as a release gate. Add each reviewed failure as a new record with a
stable ID, and retain older cases to detect regressions.

### Evaluation notes

Compare structured fields (`outcome`, `operation`, `a`, and `b`) exactly, using
numeric equality for operands. For clarification and out-of-scope cases, score
the response against the stated intent in `message`, `checks`, and `must_not`
rather than requiring identical wording. For calculation cases, run the
extracted operation through `calculate` and compare against `reference_result`
with a documented tolerance where floating-point results are involved. A case
passes only when its positive checks pass and none of its `must_not` conditions
occur.

The benchmark specifies an `outcome` and `message` in its expected structure.
The current `Operation` model in `prompt.py` does not yet define those fields,
so the dataset is a target contract and cannot be consumed directly by the
current model schema without aligning that schema first.

## Slack integration

The Slack adapter is in `slack_app.py`. Users mention the bot with a calculation
(for example, `@NL Calculator 25 times 4`); the bot replies in a thread. The
adapter uses the same `get_operation()` and `calculate()` functions as the
command-line app. Clarification replies should be sent in the same thread and
mention the bot. The command-line app remains available through `main.py`.

### Slack app configuration

1. Add the bot token scopes `app_mentions:read` and `chat:write`, then subscribe
   to the `app_mention` bot event.
2. Install or reinstall the app in the workspace, then invite the bot to each
   channel where it should answer calculations.
3. Keep Socket Mode enabled, then create an app-level token with the
   `connections:write` scope.
4. Configure `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, and `OPENAI_API_KEY` in a
   `.env` file for local use or as environment variables where the service will
   run. `.env.example` shows the names; copy it to `.env` and fill in your own
   values. Do not commit `.env` or share the secrets. `SLACK_SIGNING_SECRET` is
   only needed if using the HTTP Events API fallback.
5. Configure `DATABASE_URL` with the PostgreSQL connection string. The database
   role needs permission to create the application tables and indexes on first
   startup.

### Run locally

Install dependencies with `python -m pip install -r requirements.txt`, set the
environment variables, and run `python slack_app.py`. With `SLACK_APP_TOKEN`
set, the process connects to Slack through Socket Mode and does not need a
public HTTPS URL. Keep the process running to receive calculations. If using
the HTTP fallback instead, configure a public HTTPS Request URL to
`/slack/events` and run the Flask app with a production WSGI server.

The event queue, duplicate-event cache, and pending clarification context are
kept in process memory. Run one service instance for this initial version; a
multi-instance deployment needs shared durable storage and a shared job queue.

## Conversation persistence and health monitor

Both the terminal app and Slack adapter save user-visible question/answer pairs
to PostgreSQL. Clarification turns are saved as separate exchanges in the same
conversation. Hidden prompts, model payloads, extracted operations, and
calculation intermediates are not saved as conversation content. Operational
LLM success/failure counts and monitor results are kept in separate tables.
Conversation rows also have source, conversation/thread key, Slack event ID for
deduplication, and timestamp metadata; Slack user IDs are not stored.

Install the added dependencies with `python -m pip install -r requirements.txt`.
Set `DATABASE_URL` in the environment or `.env`. Run `python main.py` for the
terminal interface. Run `python monitor.py` as a separate long-lived process to
perform an initial health check and then a check every 2 minutes. Keeping the
monitor separate prevents duplicate scheduled checks when a web server starts
multiple workers.

The monitor checks PostgreSQL connectivity and summarizes real LLM requests
observed in the previous 2 minutes. It does not make a synthetic LLM call, so
when no users have made requests, LLM status is recorded as `unknown` rather
than treated as a failure. Results are written to `monitor_runs`; activity
counts are in `llm_activity`.

### Tests

Run the unit suite with `python -m unittest discover -v`. It covers arithmetic,
model-response validation, terminal and Slack flows, persistence behavior, and
monitor scheduling and status decisions. Database and Slack network calls are
mocked; these unit tests do not replace a live integration check.
