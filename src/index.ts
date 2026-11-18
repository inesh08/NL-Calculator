// Cloudflare Worker in front of the NL-Calculator container.
//
// The steps, in order:
//   1. fetch():      only /slack/events and /health are forwarded; all else is 404.
//   2. The Durable Object NlCalculatorContainer owns the ONE container (the app
//      keeps dedupe and clarification state in memory, so exactly one instance).
//   3. Before forwarding it makes sure the container is running, current and
//      healthy. If that cannot be done, it answers 503 with a reason. Slack
//      retries and the app de-duplicates by event_id.
//   4. "Current" means: the container runs the newest image AND was started with
//      the same environment (a SHA-256 fingerprint stored at start; never the
//      values). If either differs, the container is stopped gracefully (SIGTERM,
//      wait up to 30 s so queued events and traces drain, destroy() only as a
//      fallback). While that swap runs, requests get a 503 "restarting" at once.
//      The next request or cron tick starts the new one.
//   5. scheduled(): a cron pings /health every minute so the container stays
//      awake for its 2-minute monitor, and so a deploy or a rotated secret is
//      applied within a minute.
//
// Docs: https://developers.cloudflare.com/containers/api/durable-object-container/
import { DurableObject } from "cloudflare:workers";
import {
	buildContainerEnv,
	fingerprintEnv,
	missingSecrets,
} from "./config.ts";

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

// SIGTERM, then wait this long for the app to exit: gunicorn --graceful-timeout 20 s
// plus the 3 s trace flush, plus margin. Only after this does destroy() run.
const GRACEFUL_STOP_MS = 30_000;
const SIGTERM = 15;
// After destroy(), wait this long for monitor() to settle before giving up on it.
const POST_DESTROY_WAIT_MS = 5_000;
// Durable Object storage key holding the SHA-256 of the env the container started with.
const ENV_FINGERPRINT_KEY = "containerEnvFingerprint";

interface Env {
	NL_CALCULATOR: DurableObjectNamespace<NlCalculatorContainer>;
	LOG_LEVEL?: string;
	SLACK_BOT_TOKEN?: string;
	SLACK_SIGNING_SECRET?: string;
	OPENAI_API_KEY?: string;
	DATABASE_URL?: string;
	ZOROVAL_INGEST_KEY?: string;
	ZOROVAL_CAPTURE_CONTENT?: string;
	ZOROVAL_ENVIRONMENT?: string;
	ZOROVAL_INGEST_URL?: string;
}

/** True if the promise settles within ms; false on timeout. Never throws. */
async function settlesWithin(promise: Promise<unknown> | undefined, ms: number): Promise<boolean> {
	if (!promise) return true;
	let timer: number | null = null;
	const timeout = new Promise<false>((resolve) => {
		timer = setTimeout(() => resolve(false), ms);
	});
	const settled = promise.then(
		() => true as const,
		() => true as const,
	);
	try {
		return await Promise.race([settled, timeout]);
	} finally {
		clearTimeout(timer);
	}
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
	// Set while the running container is being stopped for a restart. One at a time.
	private swap: Promise<void> | undefined;
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
		if (this.swap) return this.restarting();
		const staleReason = container.running ? await this.staleReason() : undefined;
		if (staleReason) {
			this.beginSwap(staleReason);
			return this.restarting();
		}
		if (container.running && (await this.healthy())) return null;

		if (this.failure && Date.now() - this.failure.at < FAILURE_COOLDOWN_MS) {
			return unavailable(503, this.failure.reason, this.failure.message);
		}

		const missing = missingSecrets(this.env);
		if (missing.length > 0) {
			return this.fail(
				"missing_secrets",
				`Worker secrets not set: ${missing.join(", ")}. Run wrangler secret put for each.`,
			);
		}

		try {
			if (!container.running) {
				const env = this.containerEnv();
				// Record before starting: a fingerprint without a running container is harmless.
				await this.ctx.storage.put(ENV_FINGERPRINT_KEY, await fingerprintEnv(env));
				container.start({
					image: container.images.base,
					instance: "basic",
					// Slack, OpenAI and Postgres are all outbound internet calls.
					enableInternet: true,
					env,
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
	 * Why the running container must be replaced, or undefined if it is current.
	 * 1. Image: with the durable_object policy a deploy does NOT restart a running
	 *    container, and the cron keeps it awake, so it would never pick up a new image.
	 *    Docs: https://developers.cloudflare.com/containers/guides/image-management/
	 * 2. Environment: env is read only at start(), so a rotated secret or changed var
	 *    needs a restart. Compared by SHA-256 fingerprint, never by value.
	 */
	private async staleReason(): Promise<string | undefined> {
		const container = this.ctx.container!;
		const info = await container.inspect();
		// An empty image means "still starting"; never replace in that case.
		if (info && info.image !== "" && info.image !== container.images.base) {
			return "Container image is outdated after a deploy; restarting container on the new image";
		}
		const current = await fingerprintEnv(this.containerEnv());
		const recorded = await this.ctx.storage.get<string>(ENV_FINGERPRINT_KEY);
		if (recorded === undefined) {
			return "configuration not recorded for the running container; restarting container once";
		}
		if (recorded !== current) return "configuration changed; restarting container";
		return undefined;
	}

	/** Starts the one graceful stop in the background; callers get a 503 meanwhile. */
	private beginSwap(reason: string): void {
		console.warn(reason);
		this.swap = this.stopGracefully()
			.catch((error: unknown) => console.error("Stopping the container failed:", error))
			.finally(() => {
				this.swap = undefined;
			});
		this.ctx.waitUntil(this.swap);
	}

	/**
	 * SIGTERM, wait for exit (bounded), then destroy() only as a fallback.
	 * Docs: https://developers.cloudflare.com/containers/api/durable-object-container/
	 * (Container.signal(signo), Container.monitor(), Container.destroy()).
	 */
	private async stopGracefully(): Promise<void> {
		const container = this.ctx.container!;
		this.observeExit();
		const exited = this.monitoring;
		let exitedInTime = false;
		try {
			container.signal(SIGTERM);
			exitedInTime = await settlesWithin(exited, GRACEFUL_STOP_MS);
		} catch (error) {
			console.error("Could not send SIGTERM to the container:", error);
		}
		if (exitedInTime) {
			console.warn("Container stopped gracefully after SIGTERM");
			return;
		}
		console.error(
			`Container did not exit within ${GRACEFUL_STOP_MS / 1000}s of SIGTERM; destroying it (queued work may be lost)`,
		);
		await container.destroy();
		await settlesWithin(exited, POST_DESTROY_WAIT_MS);
	}

	private restarting(): Response {
		return unavailable(
			503,
			"restarting",
			"The container is restarting to apply a new version or configuration. Slack will retry.",
		);
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
		return buildContainerEnv(this.env, PORT);
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
