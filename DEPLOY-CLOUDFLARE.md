# Deploying NL-Calculator on Cloudflare Containers

Slack (HTTP Events) -> Cloudflare Worker (`src/index.ts`) -> one Durable Object -> one
container running gunicorn (`slack_app:app`) and `monitor.py`. Postgres is external.

Why HTTP Events and not Socket Mode: Socket Mode opens no port, so the Worker could neither
reach the container nor keep it awake.

Why exactly one container: dedupe and pending clarifications live in process memory. The
Worker always talks to the one Durable Object named `singleton`.

## What you do yourself (nothing here is automated)

1. **Workers Paid plan.** Containers need the Workers Paid plan ($5 USD/month); the Free plan
   has no Containers usage.
   https://developers.cloudflare.com/containers/platform/pricing/
2. **Postgres.** Create a hosted Postgres (for example Neon) and copy its connection string.
   Use one that is reachable from the internet with TLS (`?sslmode=require`).
3. **Cloudflare API token** for CI: dashboard -> Account API tokens -> Create Token ->
   Custom -> **Edit Cloudflare Workers**, scoped to your one account only. Copy your
   **Account ID** too.
   https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/
   If the first deploy fails with a permission error naming something else, add only that
   permission and re-run; do not widen the scope blindly.
4. **GitHub repo secrets** (Settings -> Secrets and variables -> Actions):
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
5. **First deploy** (this creates the Worker so you can set its secrets). Either push to
   `main` / run the "Deploy to Cloudflare" workflow manually, or from your laptop with Docker
   running: `npx wrangler login` then `npx wrangler deploy`.
   Wait several minutes after the first deploy before expecting container requests to work.
6. **App secrets**, one command each (it prompts; the value never enters shell history):
   ```
   npx wrangler secret put SLACK_BOT_TOKEN
   npx wrangler secret put SLACK_SIGNING_SECRET
   npx wrangler secret put OPENAI_API_KEY
   npx wrangler secret put DATABASE_URL
   npx wrangler secret put ZOROVAL_INGEST_KEY   # optional; empty/unset = tracing off
   ```
   To make the production ingest key: run
   `zoroval login --app-url https://app-preview.zoroval.com --name "NL-Calculator production" --env-file .env.production --replace`,
   copy the key from `.env.production` into `npx wrangler secret put ZOROVAL_INGEST_KEY`, then
   delete `.env.production`.
   The Worker passes these to the container explicitly when it starts it. If a required one
   is missing, requests get a 503 that names the missing secret. A secret changed later (for
   example a rotation) is applied automatically within about a minute: the next cron tick
   notices the change and restarts the container (see "How it behaves").
   Optional, non-secret settings live in `wrangler.jsonc` `vars` and follow the same path:
   `ZOROVAL_ENVIRONMENT` (set to `production`) and `ZOROVAL_CAPTURE_CONTENT` (not set, so the
   SDK default applies and question/answer text IS sent to Zoroval; add
   `"ZOROVAL_CAPTURE_CONTENT": "false"` and redeploy to stop sending text). `ZOROVAL_INGEST_URL`
   selects the Zoroval ingest: `https://ingest-preview.zoroval.com` for a preview key (as set in
   `wrangler.jsonc`), or removed for a production key, where the SDK default ingest is used.
   Unset or empty optional values are not passed to the container at all.
   https://developers.cloudflare.com/workers/configuration/secrets/
7. **Slack app:** turn **Socket Mode off**, then Event Subscriptions -> enable -> Request URL
   `https://<worker>.<your-subdomain>.workers.dev/slack/events` (it must show "Verified"),
   subscribed to the `app_mention` bot event. `SLACK_APP_TOKEN` is not needed any more.
   Find the Worker URL in the dashboard or the `wrangler deploy` output.
8. **Check:** `curl https://<worker>/health` returns `{"status":"ok"}`;
   `npx wrangler containers list`; `npx wrangler tail` for logs; the `monitor_runs` table
   should gain a row every 2 minutes.

## How it behaves

- **Cold start and Slack's 3 s window.** A cold container takes seconds (Cloudflare says often
  1-3 s, plus Python imports here). If Slack's 3 s ack window passes, Slack retries the event
  (header `X-Slack-Retry-Num`). The app de-duplicates by `event_id`, so the retry does not
  produce a second answer. The cron below makes cold starts rare.
  https://developers.cloudflare.com/containers/faq/
- **Keep-warm.** A cron every minute calls `/health`. The container's inactivity timeout is
  10 minutes, comfortably above the 1-minute cron and the 2-minute monitor.
- **Failures are worded, logged once, and not retried in a loop.**
  - Container not ready within about 15-45 s, start fails, or secrets missing: HTTP 503 with a
    JSON `reason`; for 30 s after a failed start further requests get the same 503 instantly
    instead of trying again. Slack and the next cron tick do the retrying.
  - Container accepted the request but did not answer in 20 s: 504 `forward_timeout`.
  - Connection to the container broke: 502 `forward_failed`.
- **Container exit.** If gunicorn or the monitor exits, the entrypoint stops the other and
  exits non-zero; the Worker logs it and starts a fresh container on the next request or cron.
  In-memory state (dedupe, clarifications) is lost on every restart; that is inherent to the
  current app design.
- **No `--preload`.** The Zoroval SDK initialises at import time and skips its exit flush in
  forked children, so with preload queued traces would be lost on shutdown. Without it, init
  runs in the worker and gunicorn's graceful SIGTERM exit flushes. `--graceful-timeout 20`
  leaves room; Cloudflare waits up to 15 minutes after SIGTERM before SIGKILL.
- **New deploys.** With the `durable_object` scheduling policy a deploy does not restart a
  running container, and the cron keeps it from ever sleeping. The Durable Object therefore
  compares the running image with the new one on every request and restarts it if they
  differ. https://developers.cloudflare.com/containers/guides/image-management/
- **Graceful swap.** The restart is not a hard kill. The Worker sends SIGTERM
  (`container.signal(15)`) so gunicorn drains its queue (`--graceful-timeout 20`) and the
  Zoroval SDK flushes traces, waits up to 30 s for the container to exit, and calls
  `destroy()` (immediate stop, work lost) only if it has not exited by then. Both outcomes
  are logged once. While the swap runs, requests get a 503 with `reason: "restarting"` at
  once (Slack retries; the app de-duplicates); the next request or cron tick starts the new
  container. Concurrent requests share one swap.
- **Changed secrets and vars.** At start the Worker stores a SHA-256 fingerprint of the
  environment it passed to the container (in Durable Object storage; never the values). On
  every request and cron tick it compares the current environment with it; if it differs it
  logs "configuration changed; restarting container" (no values) and does the same graceful
  swap. After `wrangler secret put` the change takes effect within about a minute. The first
  deploy of this version restarts the container once, because no fingerprint was recorded yet.

## Deploy options: GitHub Actions (recommended) vs Workers Builds

Docs confirm both build Dockerfile containers: `wrangler deploy` builds and pushes the image;
Workers Builds can run Dockerfile builds in its environment but its default non-production
command `wrangler versions upload` does not update images.
https://developers.cloudflare.com/containers/guides/deploy/

| | GitHub Actions (`.github/workflows/deploy-cloudflare.yml`) | Workers Builds (dashboard Git integration) |
|---|---|---|
| Tests before deploy | Yes: `python -m unittest discover`, then type-check, then deploy | No Python test gate by default |
| Secrets | 2 GitHub repo secrets | Token created by Cloudflare |
| Setup | Workflow already in repo | Dashboard -> Worker -> Settings -> Builds -> Connect; Worker name must equal `name` in `wrangler.jsonc` (`nl-calculator`) |

**Recommendation: GitHub Actions**, because the tests gate the deploy and the permissions are
explicit and minimal. Use only one of the two, or every push deploys twice.
https://developers.cloudflare.com/workers/ci-cd/builds/configuration/

## Cost (from the pricing page; my arithmetic, check the dashboard)

Billed per 10 ms while the container runs, so with the keep-warm cron it is effectively
24/7. Instance type is `basic` (1/4 vCPU, 1 GiB, 4 GB disk) for headroom over the OpenAI and
Postgres libraries; `lite` (256 MiB) would be cheaper but may run out of memory.
Included on Workers Paid: 25 GiB-hours memory, 375 vCPU-minutes, 200 GB-hours disk.
Extra: $0.0000025/GiB-s memory, $0.000020/vCPU-s (active use only), $0.00000007/GB-s disk.
Roughly 24/7 on `basic`: about $6.35 memory + $0.70 disk + a small CPU amount, plus the
$5 plan, so about $12/month. With `lite`, about $2 + the plan. Egress is far below the 1 TB
included. Container logs move to Observability pricing from 1 December 2026.
https://developers.cloudflare.com/containers/platform/pricing/

## Rollback and restarts

- Worker and image: `npx wrangler rollback` (or dashboard -> Worker -> Deployments ->
  Rollback). The running container swaps to the rolled-back image on the next request, same
  mechanism as a deploy. If you deleted the old image (`wrangler containers images delete`),
  rolling back to it will not work. Images live in Cloudflare's registry (50 GB per account).
  https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/
- A changed secret or var restarts the container by itself (see "Changed secrets and vars").
  To force a restart anyway, change any var or secret value, or deploy new code. Not retrying
  forever: a restart is only considered when a request or the 1-minute cron arrives.
- Stop spending: delete the Cron Trigger (remove `triggers` and redeploy); the container
  sleeps after 10 idle minutes.

## Low bandwidth (2G)

The Worker adds one hop in Cloudflare's network and no extra round trips from the client's
point of view: Slack talks to Cloudflare, not to you. Requests are small signed JSON bodies,
forwarded unchanged. Nothing loops: readiness polling is bounded (30 probes, 0.5 s apart,
1 s each), failures are cached for 30 s, and the only repeat is the once-a-minute cron.
Deploys from a slow connection: use the GitHub Actions path so the image build and push
happen on GitHub's network, not yours. A local `wrangler deploy` uploads the whole image
(about 170 MB) the first time, then only changed layers.

## Docs relied on

- https://developers.cloudflare.com/containers/get-started/
- https://developers.cloudflare.com/containers/api/durable-object-container/
- https://developers.cloudflare.com/containers/configuration/scheduling-policy/
- https://developers.cloudflare.com/containers/examples/status-hooks/
- https://developers.cloudflare.com/containers/examples/env-vars-and-secrets/
- https://developers.cloudflare.com/containers/guides/deploy/
- https://developers.cloudflare.com/containers/guides/image-management/
- https://developers.cloudflare.com/containers/guides/migrate-to-durable-object-container-api/
- https://developers.cloudflare.com/containers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/
