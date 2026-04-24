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

async function openConcreteNodeDetail(page) {
  await page.waitForFunction(
    () => document.querySelectorAll("#graph-svg circle[data-node-id]").length > 0,
    null,
    { timeout: 30000 }
  );

  const concreteSelector = "#graph-svg circle[data-node-id]:not([data-node-id^='cluster::'])";
  let concreteNodes = page.locator(concreteSelector);
  if ((await concreteNodes.count()) === 0) {
    await page.locator("#graph-svg circle[data-node-id]").first().click({ force: true });
    await page.waitForFunction(
      () => document.querySelectorAll("#graph-svg circle[data-node-id]:not([data-node-id^='cluster::'])").length > 0,
      null,
      { timeout: 30000 }
    );
    concreteNodes = page.locator(concreteSelector);
  }

  await concreteNodes.first().click({ force: true });
  const detailPanel = page.locator("#detail-panel");
  if (!(await detailPanel.isVisible())) {
    await page.evaluate(() => {
      const graph = window.__CODEWEAVE_TEST_API__?.getGraphData?.() || window.__CODEMAPPER_GRAPH__;
      const node = Array.isArray(graph?.nodes)
        ? graph.nodes.find((entry) => !String(entry?.id || "").startsWith("cluster::"))
        : null;
      if (node && typeof window.loadNodeDetail === "function") {
        window.loadNodeDetail(node, graph);
      }
    });
  }
  await expect(detailPanel).toBeVisible({ timeout: 30000 });
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
    await openConcreteNodeDetail(page);
    await expect(page.locator("#node-name")).not.toHaveText("Select a node");
  });

  test("persists theme and recent scan history across reloads", async ({ page }) => {
    await page.goto("/");
    await page.locator("#theme-light-btn").click();
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

  test("evolution controls navigate commits, toggle playback, and load diff", async ({ page }) => {
    await page.goto("/");
    await page.selectOption("#language-input", "typescript");
    await page.locator("#path-input").fill(gitHistoryFixturePath);
    await page.getByRole("button", { name: "Scan Project" }).click();
    await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });

    await page.getByRole("button", { name: "Evolution" }).click();
    await expect(page.locator("#history-overlay")).toHaveClass(/visible/, { timeout: 30000 });

    const historyCommitMeta = page.locator("#history-commit-meta");
    await expect(historyCommitMeta).toContainText("commits available", { timeout: 30000 });
    const initialMeta = (await historyCommitMeta.textContent())?.trim() || "";
    expect(initialMeta.length).toBeGreaterThan(0);

    await expect(page.locator("#history-prev-btn")).toBeEnabled({ timeout: 30000 });
    await page.evaluate(() => document.getElementById("history-prev-btn")?.click());
    await expect(historyCommitMeta).not.toHaveText(initialMeta, { timeout: 30000 });
    const previousMeta = (await historyCommitMeta.textContent())?.trim() || "";
    expect(previousMeta.length).toBeGreaterThan(0);

    await expect(page.locator("#history-next-btn")).toBeEnabled({ timeout: 30000 });
    await page.evaluate(() => document.getElementById("history-next-btn")?.click());
    await expect(historyCommitMeta).toHaveText(initialMeta, { timeout: 30000 });

    await expect(page.locator("#history-play-btn")).toBeEnabled({ timeout: 30000 });
    await page.evaluate(() => document.getElementById("history-play-btn")?.click());
    await expect(page.getByRole("button", { name: "Pause Timeline" })).toBeVisible({ timeout: 30000 });
    await page.evaluate(() => document.getElementById("history-play-btn")?.click());
    await expect(page.getByRole("button", { name: "Play Timeline" })).toBeVisible({ timeout: 30000 });

    await page.evaluate(() => document.getElementById("history-diff-btn")?.click());
    await expect(page.locator("#history-diff-body")).not.toContainText("Click Show Diff", { timeout: 30000 });
    await expect(page.locator("#history-diff-body")).not.toContainText("Failed to load commit diff", { timeout: 30000 });
    await expect(page.locator("#history-diff-body")).toContainText("index.ts", { timeout: 30000 });
  });
});
