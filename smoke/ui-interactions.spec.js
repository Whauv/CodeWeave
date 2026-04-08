const path = require("path");
const { test, expect } = require("@playwright/test");

const fixturePath = path.resolve(__dirname, "fixtures", "typescript_demo");

async function scanFixture(page) {
  await page.goto("/");
  await page.selectOption("#language-input", "typescript");
  await page.locator("#path-input").fill(fixturePath);
  await page.getByRole("button", { name: "Scan Project" }).click();
  await expect(page.locator("#metric-nodes")).not.toHaveText("0", { timeout: 30000 });
  await expect(page.locator("#graph-svg circle")).toHaveCount(4, { timeout: 30000 });
  await page.locator("#graph-svg circle").last().click({ force: true });
  await expect(page.locator("#detail-panel")).toBeVisible();
}

test.describe("CodeWeave interaction flows", () => {
  test("runs blast radius workflow", async ({ page }) => {
    await scanFixture(page);
    await page.evaluate(() => {
      const graph = window.__CODEWEAVE_TEST_API__?.getGraphData?.() || { nodes: [] };
      const node = graph.nodes[graph.nodes.length - 1];
      if (!node || !window.__CODEWEAVE_TEST_API__?.applyBlastData) {
        throw new Error("Blast test helpers unavailable");
      }
      window.__CODEWEAVE_TEST_API__.applyBlastData({
        epicenter: node.id,
        epicenter_name: node.name,
        affected_nodes: [node.id],
        depth_map: { [node.id]: 0 },
        risk_colors: { [node.id]: "#ff2222" },
        summary: `Changing ${node.name} affects 1 function across 1 modules`,
      });
    });
    await expect(page.locator("#metric-mode")).toHaveText("Blast", { timeout: 30000 });
    await expect(page.locator("#blast-info")).toContainText("affects 1 function across 1 modules", { timeout: 30000 });
  });

  test("exports graph json after a scan", async ({ page }) => {
    await scanFixture(page);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export JSON" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/typescript_demo.*\.json$/i);
  });

  test("submits a chat question and renders the assistant response", async ({ page }) => {
    await page.route("**/api/chat", async (route) => {
      const request = route.request();
      const payload = request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          provider: payload.provider || "groq",
          node_id: payload.node_id || null,
          answer: "Changing this node will affect its direct callers and one downstream formatter.",
        }),
      });
    });

    await scanFixture(page);
    await page.locator("#chat-input").fill("What breaks if I change this node?");
    await page.getByRole("button", { name: "Ask AI" }).click();
    await expect(page.locator("#chat-messages")).toContainText("What breaks if I change this node?", { timeout: 30000 });
    await expect(page.locator("#chat-messages")).toContainText(
      "Changing this node will affect its direct callers and one downstream formatter.",
      { timeout: 30000 }
    );
  });
});
