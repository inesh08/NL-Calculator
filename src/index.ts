// Cloudflare Worker in front of the NL-Calculator container.
//
// The steps, in order:
//   1. fetch():      only /slack/events and /health are forwarded; all else is 404.
//   2. The Durable Object NlCalculatorContainer owns the ONE container (the app
//      keeps dedupe and clarification state in memory, so exactly one instance).
//   3. Before forwarding it makes sure the container is running and healthy.
//      If that cannot be done, it answers 503 with a reason. Slack retries and
//      the app de-duplicates by event_id.
//   4. scheduled(): a cron pings /health every minute so the container stays
//      awake for its 2-minute monitor.
//
// Docs: https://developers.cloudflare.com/containers/api/durable-object-container/
import { DurableObject } from "cloudflare:workers";

const PORT = 8080;
const INSTANCE_NAME = "singleton";
// Must comfortably exceed the 1-minute cron interval and the 2-minute monitor.
const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000;
const READY_ATTEMPTS = 30;
const READY_WAIT_MS = 500;
const PROBE_TIMEOUT_MS = 1000;
const FORWARD_TIMEOUT_MS = 20_000;
// After a failed start, answer 503 immediately for this long instead of retrying.
const FAILURE_COOLDOWN_MS = 30_000;

const REQUIRED_SECRETS = [
	"SLACK_BOT_TOKEN",
	"SLACK_SIGNING_SECRET",
	"OPENAI_API_KEY",
	"DATABASE_URL",
] as const;
// Optional: empty/missing means Zoroval tracing is off.
const OPTIONAL_SECRETS = ["ZOROVAL_INGEST_KEY"] as const;

interface Env {
	NL_CALCULATOR: DurableObjectNamespace<NlCalculatorContainer>;
	LOG_LEVEL?: string;
	SLACK_BOT_TOKEN?: string;
	SLACK_SIGNING_SECRET?: string;
	OPENAI_API_KEY?: string;
	DATABASE_URL?: string;
	ZOROVAL_INGEST_KEY?: string;
}

function unavailable(status: number, reason: string, message: string): Response {
	return Response.json(
		{ status: "unavailable", reason, message },
		{ status, headers: { "Retry-After": "5" } },
	);
}

export class NlCalculatorContainer extends DurableObject<Env> {
	private starting: Promise<Response | null> | undefined;
	private monitoring: Promise<void> | undefined;
	private failure: { at: number; reason: string; message: string } | undefined;

	constructor(ctx: DurableObjectState, env: Env) {
		super(ctx, env);
		const container = ctx.container;
		if (container?.running) {
			// Durable Object restarted while the container kept running:
			// restore the inactivity timeout and exit logging.
			void ctx.blockConcurrencyWhile(() =>
				container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS),
			);
			this.observeExit();
		}
	}

	async fetch(request: Request): Promise<Response> {
		// Concurrent requests share one start-up, so the container starts once.
		this.starting ??= this.ensureReady().finally(() => {
			this.starting = undefined;
		});
		const notReady = await this.starting;
		if (notReady) return notReady;
		return this.forward(request);
	}

	/** Returns null when the container is ready, or a 503 response saying why. */
	private async ensureReady(): Promise<Response | null> {
		const container = this.ctx.container!;
		await this.replaceOutdatedContainer();
		if (container.running && (await this.healthy())) return null;

		if (this.failure && Date.now() - this.failure.at < FAILURE_COOLDOWN_MS) {
			return unavailable(503, this.failure.reason, this.failure.message);
		}

		const missing = REQUIRED_SECRETS.filter((name) => !this.env[name]);
		if (missing.length > 0) {
			return this.fail(
				"missing_secrets",
				`Worker secrets not set: ${missing.join(", ")}. Run wrangler secret put for each.`,
			);
		}

		try {
			if (!container.running) {
				container.start({
					image: container.images.base,
					instance: "basic",
					// Slack, OpenAI and Postgres are all outbound internet calls.
					enableInternet: true,
					env: this.containerEnv(),
				});
			}
			this.observeExit();
			await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);
			for (let attempt = 0; attempt < READY_ATTEMPTS; attempt++) {
				if (await this.healthy()) {
					this.failure = undefined;
					return null;
				}
				await scheduler.wait(READY_WAIT_MS);
			}
			return this.fail(
				"start_timeout",
				"The container started but did not pass its health check in time. Slack will retry.",
			);
		} catch (error) {
			return this.fail(
				"start_failed",
				`The container could not be started: ${String(error)}`,
			);
		}
	}

	/**
	 * With the durable_object policy a deploy does NOT restart a running container.
	 * Because the cron keeps it awake, it would never pick up a new image on its
	 * own, so stop it here and let ensureReady start the new image.
	 * Docs: https://developers.cloudflare.com/containers/guides/image-management/
	 */
	private async replaceOutdatedContainer(): Promise<void> {
		const container = this.ctx.container!;
		if (!container.running) return;
		const info = await container.inspect();
		// An empty image means "still starting"; never replace in that case.
		if (info && info.image !== "" && info.image !== container.images.base) {
			console.warn("Container image is outdated after a deploy; restarting it on the new image");
			await container.destroy();
			await this.monitoring;
		}
	}

	private async healthy(): Promise<boolean> {
		try {
			const response = await this.ctx
				.container!.getTcpPort(PORT)
				.fetch("http://container/health", {
					signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
				});
			await response.body?.cancel();
			return response.ok;
		} catch {
			return false;
		}
	}

	private async forward(request: Request): Promise<Response> {
		const url = new URL(request.url);
		url.protocol = "http:";
		url.host = "container";
		const forwarded = new Request(url, request);
		forwarded.headers.delete("host");
		try {
			return await this.ctx.container!.getTcpPort(PORT).fetch(forwarded, {
				signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
			});
		} catch (error) {
			const timedOut = error instanceof Error && error.name === "TimeoutError";
			console.error(`Forward to container failed (${timedOut ? "timeout" : "error"}):`, error);
			return unavailable(
				timedOut ? 504 : 502,
				timedOut ? "forward_timeout" : "forward_failed",
				timedOut
					? `The container did not answer within ${FORWARD_TIMEOUT_MS / 1000}s. Slack will retry.`
					: "The container connection failed. Slack will retry.",
			);
		}
	}

	private fail(reason: string, message: string): Response {
		this.failure = { at: Date.now(), reason, message };
		console.error(`Container unavailable (${reason}): ${message}`);
		return unavailable(503, reason, message);
	}

	private containerEnv(): Record<string, string> {
		const env: Record<string, string> = { PORT: String(PORT), LOG_LEVEL: this.env.LOG_LEVEL ?? "INFO" };
		for (const name of [...REQUIRED_SECRETS, ...OPTIONAL_SECRETS]) {
			env[name] = this.env[name] ?? "";
		}
		return env;
	}

	private observeExit(): void {
		if (this.monitoring) return;
		this.monitoring = this.ctx
			.container!.monitor()
			.then(() => console.error("Container exited (it should never exit on its own)"))
			.catch((error: unknown) => console.error("Container failed:", error))
			.finally(() => {
				this.monitoring = undefined;
			});
		this.ctx.waitUntil(this.monitoring);
	}
}

const FORWARDED: Record<string, string> = {
	"/slack/events": "POST",
	"/health": "GET",
};

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const { pathname } = new URL(request.url);
		const method = FORWARDED[pathname];
		if (!method) return new Response("Not found", { status: 404 });
		if (request.method !== method) {
			return new Response("Method not allowed", { status: 405, headers: { Allow: method } });
		}
		return env.NL_CALCULATOR.getByName(INSTANCE_NAME).fetch(request);
	},

	// Cron: keeps the container awake so its 2-minute monitor keeps running.
	async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
		ctx.waitUntil(
			env.NL_CALCULATOR.getByName(INSTANCE_NAME)
				.fetch("https://worker/health")
				.then((response) => {
					if (!response.ok) console.error(`Keep-warm ping failed: HTTP ${response.status}`);
				})
				.catch((error: unknown) => console.error("Keep-warm ping error:", error)),
		);
	},
} satisfies ExportedHandler<Env>;
