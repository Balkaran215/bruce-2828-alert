// Cloudflare Worker: starts the GitHub checker on a timer, because GitHub's own schedule runs hours apart.
// Needs a cron trigger of */5 * * * * and two secrets: GITHUB_TOKEN and NTFY_TOPIC.
const DISPATCH_URL =
  "https://api.github.com/repos/Balkaran215/bruce-2828-alert/actions/workflows/check.yml/dispatches";
const ATTEMPTS = 3;
const RETRY_DELAY_MS = 3000;

async function dispatch(env) {
  const response = await fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "bruce-2828-alert-timer",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (response.status !== 204) {
    throw new Error(`GitHub returned ${response.status}`);
  }
}

async function alertPhone(env, problem) {
  await fetch("https://ntfy.sh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      topic: env.NTFY_TOPIC,
      title: "2828 timer can't start the checker",
      message: `${problem}. The site isn't being checked reliably, so check it yourself. If GitHub returned 401, the token has expired.`,
      priority: 4,
      click: "https://2828.brucepower.com/",
    }),
  });
}

async function run(event, env) {
  let problem;
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    try {
      await dispatch(env);
      return;
    } catch (error) {
      problem = error.message;
      console.error(`Attempt ${attempt} failed: ${problem}`);
    }
    if (attempt < ATTEMPTS) {
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }
  }
  // The timer fires every 5 minutes, so this limits alerts to one an hour during an outage.
  if (new Date(event.scheduledTime).getUTCMinutes() < 5) {
    await alertPhone(env, problem);
  }
  throw new Error(problem);
}

export default {
  scheduled(event, env, ctx) {
    ctx.waitUntil(run(event, env));
  },
};
