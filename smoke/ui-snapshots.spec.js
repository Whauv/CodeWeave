const path = require("path");
const { test, expect } = require("@playwright/test");

const fixturePath = path.resolve(__dirname, "fixtures", "typescript_demo");

async function scanFixture(page) {
  await page.goto("/");
  await page.selectOption("#language-input", "typescript");
  await page.locator("#path-input").fill(fixturePath);
  await page.getByRole("button", { name: "Scan Project" }).click();
  await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });
}

test.describe("CodeWeave UI snapshots", () => {
  test("dashboard shell snapshot", async ({ page }) => {
    await page.goto("/");
    const snapshot = await page.evaluate(() => ({
      theme: document.body.getAttribute("data-theme"),
      status: document.getElementById("status-label")?.textContent?.trim(),
      controls: [
        "tree-layout-btn",
        "force-layout-btn",
        "cluster-toggle-btn",
        "history-btn",
      ].map((id) => ({
        id,
        active: document.getElementById(id)?.classList.contains("active") || false,
      })),
      historyStatus: document.getElementById("history-status")?.textContent?.trim(),
    }));
    expect(JSON.stringify(snapshot, null, 2)).toMatchSnapshot("dashboard-shell-state.txt");
  });

  test("post-scan toolbar snapshot", async ({ page }) => {
    await scanFixture(page);
    const snapshot = await page.evaluate(() => ({
      mode: document.getElementById("metric-mode")?.textContent?.trim(),
      nodes: document.getElementById("metric-nodes")?.textContent?.trim(),
      edges: document.getElementById("metric-edges")?.textContent?.trim(),
      focusBadge: document.getElementById("focus-badge")?.textContent?.replace(/\s+/g, " ").trim(),
      edgeLabelsActive: document.getElementById("edge-label-toggle-btn")?.classList.contains("active") || false,
      neighborDepth: document.getElementById("neighbor-depth-value")?.textContent?.trim(),
      graphSpacing: document.getElementById("graph-spacing-value")?.textContent?.trim(),
    }));
    // Normalize numbers that can vary by environment.
    snapshot.nodes = String(snapshot.nodes || "").replace(/[^0-9]/g, "");
    snapshot.edges = String(snapshot.edges || "").replace(/[^0-9]/g, "");
    expect(JSON.stringify(snapshot, null, 2)).toMatchSnapshot("post-scan-toolbar-state.txt");
  });
});
