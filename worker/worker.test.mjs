import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import worker from "./worker.js";

const ENV = { GITHUB_TOKEN: "gh-token", NTFY_TOPIC: "my-topic" };
const ON_THE_HOUR = Date.UTC(2026, 10, 6, 9, 0);
const MID_HOUR = Date.UTC(2026, 10, 6, 9, 20);
const realFetch = globalThis.fetch;
const realError = console.error;

afterEach(() => {
  globalThis.fetch = realFetch;
  console.error = realError;
});

// Runs the handler with fake responses for GitHub. Returns the calls made and any final error.
async function fire(scheduledTime, githubResponses) {
  const calls = [];
  console.error = () => {};
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.includes("ntfy.sh")) return new Response(null, { status: 200 });
    const next = githubResponses.shift();
    if (next instanceof Error) throw next;
    return new Response(null, { status: next });
  };
  let waited;
  worker.scheduled({ scheduledTime }, ENV, { waitUntil: (promise) => (waited = promise) });
  const error = await waited.then(() => null, (e) => e);
  return { calls, error };
}

const toGithub = (calls) => calls.filter((c) => c.url.includes("api.github.com"));
const toNtfy = (calls) => calls.filter((c) => c.url.includes("ntfy.sh"));

test("success starts the workflow once with the token and sends no alert", async () => {
  const { calls, error } = await fire(ON_THE_HOUR, [204]);
  assert.equal(error, null);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /Balkaran215\/bruce-2828-alert\/actions\/workflows\/check\.yml\/dispatches$/);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.Authorization, "Bearer gh-token");
  assert.ok(calls[0].init.headers["User-Agent"]);
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main" });
});

test("a temporary failure is retried with no alert", async () => {
  const { calls, error } = await fire(ON_THE_HOUR, [500, new Error("network down"), 204]);
  assert.equal(error, null);
  assert.equal(toGithub(calls).length, 3);
  assert.equal(toNtfy(calls).length, 0);
});

test("repeated failure on the hour alerts the phone and reports the failure", async () => {
  const { calls, error } = await fire(ON_THE_HOUR, [401, 401, 401]);
  assert.equal(error.message, "GitHub returned 401");
  assert.equal(toGithub(calls).length, 3);
  assert.equal(toNtfy(calls).length, 1);
  const body = JSON.parse(toNtfy(calls)[0].init.body);
  assert.equal(body.topic, "my-topic");
  assert.match(body.message, /GitHub returned 401/);
  assert.ok(!JSON.stringify(body).includes("gh-token"));
});

test("repeated failure mid-hour doesn't alert, so alerts stay hourly", async () => {
  const { calls, error } = await fire(MID_HOUR, [500, 500, 500]);
  assert.equal(error.message, "GitHub returned 500");
  assert.equal(toNtfy(calls).length, 0);
});
