import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { join } from "node:path";
import test from "node:test";

const HOST = "127.0.0.1";
const SERVER_READY_TIMEOUT_MS = 30_000;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on("error", reject);
    server.listen(0, HOST, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Could not allocate a TCP port")));
        return;
      }
      const { port } = address;
      server.close(() => resolve(port));
    });
  });
}

function stopProcessTree(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    return;
  }
  child.kill("SIGTERM");
}

async function waitForServer(baseUrl, child, logs) {
  const deadline = Date.now() + SERVER_READY_TIMEOUT_MS;
  let lastError = "";

  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Next server exited before it became ready.\n${logs()}`);
    }

    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }

    await delay(250);
  }

  throw new Error(`Next server did not become ready: ${lastError}\n${logs()}`);
}

async function fetchHtml(baseUrl, path) {
  const response = await fetch(new URL(path, baseUrl));
  const html = await response.text();
  assert.equal(response.status, 200, `${path} should render successfully`);
  return html;
}

test("built app renders core public user flow routes", async (t) => {
  const port = await getFreePort();
  const baseUrl = `http://${HOST}:${port}`;
  const nextBin = join(process.cwd(), "node_modules", "next", "dist", "bin", "next");
  let logs = "";

  const server = spawn(
    process.execPath,
    [nextBin, "start", "--hostname", HOST, "--port", String(port)],
    {
      cwd: process.cwd(),
      env: { ...process.env, PORT: String(port) },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  server.stdout.on("data", (chunk) => {
    logs += chunk.toString();
  });
  server.stderr.on("data", (chunk) => {
    logs += chunk.toString();
  });

  t.after(() => stopProcessTree(server));

  await waitForServer(baseUrl, server, () => logs);

  const routes = [
    {
      path: "/login",
      expected: ['data-page="login"', 'data-flow="identity-login"', 'data-flow="social-login"'],
    },
    {
      path: "/signup",
      expected: ['data-page="signup"', 'data-flow="identity-signup"'],
    },
    {
      path: "/pricing",
      expected: ['data-page="pricing"', 'data-flow="billing-monthly"', 'data-flow="start-subscription"'],
    },
  ];

  for (const route of routes) {
    const html = await fetchHtml(baseUrl, route.path);
    for (const expected of route.expected) {
      assert.ok(html.includes(expected), `${route.path} should include ${expected}`);
    }
  }
});
