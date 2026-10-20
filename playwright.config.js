import { defineConfig } from "@playwright/test";
import path from "node:path";
const baseURL = process.env.NS_E2E_URL || "http://localhost:8000";
export default defineConfig({
  testDir: "./tests/browser",
  timeout: 60000,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalTeardown: "./tests/browser/teardown.js",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${process.env.PYTHON_BIN || "python"}" scripts/dev.py`,
    url: `${baseURL}/health/ready`,
    reuseExistingServer: false,
    timeout: 60000,
    env: {
      ...process.env,
      NS_PUBLIC_URL: baseURL,
      NS_DEV_PORT: new URL(baseURL).port || "8000",
      NS_DEV_STOP_FILE: path.resolve(".runtime/e2e-stop"),
    },
  },
});
