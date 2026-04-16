const fs = require("fs");
const path = require("path");

function resolvePythonCommand() {
  if (process.env.CODEWEAVE_PYTHON) {
    return process.env.CODEWEAVE_PYTHON;
  }

  const venvPython = process.platform === "win32"
    ? path.join(".venv", "Scripts", "python.exe")
    : path.join(".venv", "bin", "python");

  return fs.existsSync(venvPython) ? venvPython : "python";
}

/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./smoke",
  timeout: 60000,
  // The Flask test server holds scan state in process memory.
  // Keep CI smoke tests single-worker to avoid cross-test state races.
  workers: process.env.CI ? 1 : undefined,
  fullyParallel: false,
  use: {
    baseURL: process.env.CODEWEAVE_BASE_URL || "http://127.0.0.1:5050",
    headless: true,
  },
  webServer: {
    command: `${resolvePythonCommand()} server/app.py`,
    url: "http://127.0.0.1:5050",
    timeout: 120000,
    reuseExistingServer: true,
    env: {
      ...process.env,
      FLASK_DEBUG: "0",
    },
  },
  reporter: [["list"]],
};

module.exports = config;
