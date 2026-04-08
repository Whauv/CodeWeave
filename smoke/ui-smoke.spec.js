const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { test, expect } = require("@playwright/test");

const fixturePath = path.resolve(__dirname, "fixtures", "typescript_demo");
let gitHistoryFixturePath = "";

function runGit(args, cwd) {
  execFileSync("git", args, { cwd, stdio: "ignore" });
}

function createGitHistoryFixture() {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "codeweave-history-"));
  fs.cpSync(fixturePath, tempRoot, { recursive: true });
  runGit(["init"], tempRoot);
  runGit(["config", "user.name", "CodeWeave Smoke"], tempRoot);
  runGit(["config", "user.email", "smoke@example.com"], tempRoot);
  runGit(["add", "."], tempRoot);
  runGit(["commit", "-m", "Initial fixture commit"], tempRoot);
  fs.appendFileSync(
    path.join(tempRoot, "src", "index.ts"),
    "\nexport function describeScore(score: number): string {\n  return formatScore(score);\n}\n"
  );
  runGit(["add", "."], tempRoot);
  runGit(["commit", "-m", "Add describeScore helper"], tempRoot);
  return tempRoot;
}

test.describe("CodeWeave smoke flow", () => {
  test.beforeAll(() => {
    gitHistoryFixturePath = createGitHistoryFixture();
  });

  test("loads the dashboard shell", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "CodeMapper" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Scan Project" })).toBeVisible();
    await expect(page.locator("#graph-empty-state")).toContainText("Scan a codebase to light up the map.");
  });

  test("shows core graph controls before scan", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Tree Layout" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Force Layout" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Evolution" })).toBeVisible();
    await expect(page.locator("#history-status")).toContainText("Load a scanned git repo to begin.");
  });

  test("scans a local fixture and opens a node detail panel", async ({ page }) => {
    await page.goto("/");
    await page.selectOption("#language-input", "typescript");
    await page.locator("#path-input").fill(fixturePath);
    await page.getByRole("button", { name: "Scan Project" }).click();

    await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });
    await expect(page.locator("#graph-svg circle")).toHaveCount(4, { timeout: 30000 });

    await page.locator("#graph-svg circle").last().click({ force: true });
    await expect(page.locator("#detail-panel")).toBeVisible();
    await expect(page.locator("#node-name")).not.toHaveText("Select a node");
  });

  test("persists theme and recent scan history across reloads", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Light Theme" }).click();
    await expect(page.locator("body")).toHaveAttribute("data-theme", "light");

    await page.selectOption("#language-input", "typescript");
    await page.locator("#path-input").fill(fixturePath);
    await page.getByRole("button", { name: "Scan Project" }).click();
    await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });

    await page.reload();
    await expect(page.locator("body")).toHaveAttribute("data-theme", "light");
    await expect(page.locator("#scan-history-list")).toContainText("typescript_demo");
  });

  test("opens evolution mode for a git-backed repo with multiple commits", async ({ page }) => {
    await page.goto("/");
    await page.selectOption("#language-input", "typescript");
    await page.locator("#path-input").fill(gitHistoryFixturePath);
    await page.getByRole("button", { name: "Scan Project" }).click();
    await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });

    await page.getByRole("button", { name: "Evolution" }).click();
    await expect(page.locator("#history-overlay")).toHaveClass(/visible/, { timeout: 30000 });
    await expect(page.locator("#history-commit-meta")).toContainText("2 commits available", { timeout: 30000 });
    await expect(page.getByRole("button", { name: "Play Timeline" })).toBeEnabled();
  });
});
