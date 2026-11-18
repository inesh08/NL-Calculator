// Pure helpers for the configuration the Worker hands to the container.
// No Cloudflare APIs here, so they run under plain Node tests (test/config.test.ts).

// Required secrets: without all of them the app cannot work.
export const REQUIRED_SECRETS = [
	"SLACK_BOT_TOKEN",
	"SLACK_SIGNING_SECRET",
	"OPENAI_API_KEY",
	"DATABASE_URL",
] as const;
// Optional secret: missing means Zoroval tracing is off.
export const OPTIONAL_SECRETS = ["ZOROVAL_INGEST_KEY"] as const;
// Optional plain config (wrangler.jsonc `vars`), forwarded only when set.
// ZOROVAL_CAPTURE_CONTENT=false stops question/answer text being sent to Zoroval.
// ZOROVAL_INGEST_URL selects the Zoroval ingest (unset = SDK default production ingest).
export const OPTIONAL_VARS = [
	"ZOROVAL_CAPTURE_CONTENT",
	"ZOROVAL_ENVIRONMENT",
	"ZOROVAL_INGEST_URL",
] as const;

export type RawEnv = object;

function get(env: RawEnv, name: string): string | undefined {
	return nonEmpty((env as Record<string, unknown>)[name]);
}

/** Trimmed value, or undefined when unset or blank. Whitespace from a paste is never forwarded. */
function nonEmpty(value: unknown): string | undefined {
	if (typeof value !== "string") return undefined;
	const trimmed = value.trim();
	return trimmed === "" ? undefined : trimmed;
}

/** Names of required secrets that are unset or blank. */
export function missingSecrets(env: RawEnv): string[] {
	return REQUIRED_SECRETS.filter((name) => get(env, name) === undefined);
}

/**
 * The exact environment the container is started with. Optional values that are
 * unset or blank are left out (never sent as ""), so the app uses its own default.
 */
export function buildContainerEnv(env: RawEnv, port: number): Record<string, string> {
	const out: Record<string, string> = {
		PORT: String(port),
		LOG_LEVEL: get(env, "LOG_LEVEL") ?? "INFO",
	};
	for (const name of [...REQUIRED_SECRETS, ...OPTIONAL_SECRETS, ...OPTIONAL_VARS]) {
		const value = get(env, name);
		if (value !== undefined) out[name] = value;
	}
	return out;
}

/**
 * SHA-256 (hex) of the environment. Names are sorted so key order never matters.
 * Only this digest is ever stored; the values themselves are not.
 */
export async function fingerprintEnv(env: Record<string, string>): Promise<string> {
	const canonical = JSON.stringify(Object.entries(env).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
	return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}
