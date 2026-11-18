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

test("optional values that are unset or blank are omitted, never sent as empty", () => {
	const env = buildContainerEnv(
		{ ...secrets, ZOROVAL_INGEST_KEY: "", ZOROVAL_ENVIRONMENT: "  ", ZOROVAL_INGEST_URL: "" },
		8080,
	);
	assert.equal("ZOROVAL_INGEST_KEY" in env, false);
	assert.equal("ZOROVAL_ENVIRONMENT" in env, false);
	assert.equal("ZOROVAL_CAPTURE_CONTENT" in env, false);
	assert.equal("ZOROVAL_INGEST_URL" in env, false);
	assert.equal(env.PORT, "8080");
	assert.equal(env.LOG_LEVEL, "INFO");
});

test("privacy and environment switches are forwarded when set", () => {
	const env = buildContainerEnv(
		{
			...secrets,
			ZOROVAL_CAPTURE_CONTENT: "false",
			ZOROVAL_ENVIRONMENT: "production",
			ZOROVAL_INGEST_URL: "https://ingest-preview.zoroval.com",
			LOG_LEVEL: "DEBUG",
		},
		8080,
	);
	assert.equal(env.ZOROVAL_CAPTURE_CONTENT, "false");
	assert.equal(env.ZOROVAL_ENVIRONMENT, "production");
	assert.equal(env.ZOROVAL_INGEST_URL, "https://ingest-preview.zoroval.com");
	assert.equal(env.LOG_LEVEL, "DEBUG");
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
