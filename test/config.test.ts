// Run: npm test   (node's built-in test runner)
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildContainerEnv, fingerprintEnv, missingSecrets } from "../src/config.ts";

const secrets = {
	SLACK_BOT_TOKEN: "a",
	SLACK_SIGNING_SECRET: "b",
	OPENAI_API_KEY: "c",
	DATABASE_URL: "d",
};

test("a blank or whitespace-only LOG_LEVEL falls back to the default, never sent as empty", () => {
	const env = buildContainerEnv({ ...secrets, LOG_LEVEL: "  " }, 8080);
	assert.equal(env.LOG_LEVEL, "INFO");
	assert.equal(env.PORT, "8080");
});

test("LOG_LEVEL is forwarded when set", () => {
	assert.equal(buildContainerEnv({ ...secrets, LOG_LEVEL: "DEBUG" }, 8080).LOG_LEVEL, "DEBUG");
});

test("values are forwarded trimmed, so a pasted space or newline is never sent", () => {
	const env = buildContainerEnv({ ...secrets, OPENAI_API_KEY: " value\n", LOG_LEVEL: " DEBUG " }, 8080);
	assert.equal(env.OPENAI_API_KEY, "value");
	assert.equal(env.LOG_LEVEL, "DEBUG");
});

test("blank required secrets are omitted from the container environment", () => {
	const env = buildContainerEnv({ ...secrets, DATABASE_URL: " " }, 8080);
	assert.equal("DATABASE_URL" in env, false);
	assert.equal(env.OPENAI_API_KEY, "c");
});

test("unrelated bindings are not forwarded", () => {
	const env = buildContainerEnv({ ...secrets, NL_CALCULATOR: {}, SOMETHING_ELSE: "x" }, 8080);
	assert.equal("NL_CALCULATOR" in env, false);
	assert.equal("SOMETHING_ELSE" in env, false);
});

test("missingSecrets names blank and absent required secrets", () => {
	assert.deepEqual(missingSecrets({ ...secrets, OPENAI_API_KEY: "" }), ["OPENAI_API_KEY"]);
	assert.deepEqual(missingSecrets(secrets), []);
	assert.equal(missingSecrets({}).length, 4);
});

test("fingerprint is stable, order-independent, and changes when a value rotates", async () => {
	const a = await fingerprintEnv({ X: "1", Y: "2" });
	assert.match(a, /^[0-9a-f]{64}$/);
	assert.equal(await fingerprintEnv({ Y: "2", X: "1" }), a);
	assert.notEqual(await fingerprintEnv({ X: "1", Y: "3" }), a);
});

test("fingerprint does not contain the values", async () => {
	assert.equal((await fingerprintEnv({ OPENAI_API_KEY: "sk-secret" })).includes("sk-secret"), false);
});
