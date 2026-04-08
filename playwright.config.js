/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./smoke",
  timeout: 60000,
  use: {
    baseURL: process.env.CODEWEAVE_BASE_URL || "http://127.0.0.1:5050",
    headless: true,
  },
  webServer: {
    command: ".venv\\Scripts\\python.exe server\\app.py",
    url: "http://127.0.0.1:5050",
    timeout: 120000,
    reuseExistingServer: true,
  },
  reporter: [["list"]],
};

module.exports = config;
