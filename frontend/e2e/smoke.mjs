import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.BASE ?? "https://localhost";
const PW = process.env.ADMIN_PASSWORD;
const out = "/work/shots";
fs.mkdirSync(out, { recursive: true });
const report = { consoleErrors: [], pageErrors: [], badResponses: [], steps: [] };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: true });
page.on("console", (m) => m.type() === "error" && report.consoleErrors.push(`${page.url()} :: ${m.text()}`));
page.on("pageerror", (e) => report.pageErrors.push(`${page.url()} :: ${e.message}`));
page.on("response", (r) => r.status() >= 500 && report.badResponses.push(`${r.status()} ${r.url()}`));

async function step(name, fn) {
  try {
    await fn();
    report.steps.push(`OK   ${name}`);
  } catch (e) {
    report.steps.push(`FAIL ${name}: ${e.message.split("\n")[0]}`);
    await page.screenshot({ path: `${out}/FAIL-${name.replace(/\W+/g, "_")}.png`, fullPage: true }).catch(() => {});
  }
}
const shot = (name) => page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
const settle = () => page.waitForLoadState("networkidle").catch(() => {});

await step("login page", async () => {
  await page.goto(`${BASE}/map`);
  await page.getByLabel("Username").waitFor();
  await shot("00-login");
});
await step("login", async () => {
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Map concepts" }).waitFor({ timeout: 10000 });
  if (!page.url().endsWith("/map")) throw new Error(`redirected to ${page.url()}`);
});
for (const [path, heading, file] of [
  ["/import", "Import data", "01-import-vocabulary"],
  ["/import?tab=source", "Import data", "02-import-source"],
  ["/import?tab=embeddings", "Import data", "04-import-embeddings"],
]) {
  await step(`page ${path}`, async () => {
    await page.goto(`${BASE}${path}`);
    await page.getByRole("heading", { name: heading }).waitFor();
    await settle();
    await shot(file);
  });
}
await step("search", async () => {
  await page.goto(`${BASE}/search`);
  await page.getByPlaceholder(/concept name/i).fill("metformin 500 mg");
  await page.getByText(/Rank 1:/).waitFor({ timeout: 10000 });
  await page.getByText(/Rank 1:/).click();
  await page.waitForTimeout(600);
  await shot("05-search");
});
await step("map: select concept and search", async () => {
  await page.goto(`${BASE}/map`);
  await page.getByRole("heading", { name: "Map concepts" }).waitFor();
  await settle();
  await shot("06-map");
  const row = page.locator("table").first().locator("tbody tr").first();
  await row.click();
  await page
    .getByText(/Mapping:/)
    .first()
    .waitFor();
  await settle();
  await page.waitForTimeout(1200);
  await shot("07-map-detail");
});
await step("map: AI suggest", async () => {
  await page.getByRole("button", { name: /AI suggest/i }).click();
  await page
    .getByText(/accepted|rejected|no_match|no match/i)
    .first()
    .waitFor({ timeout: 15000 });
  await settle();
  await shot("08-map-ai-suggest");
});
await step("map: auto-map settings", async () => {
  await page.getByRole("button", { name: /Auto-map with AI/ }).click();
  await page.waitForTimeout(500);
  await shot("10-map-automap-settings");
  await page.keyboard.press("Escape");
});
await step("map: select result and confirm", async () => {
  const results = page.locator("table").nth(1);
  await results.locator("tbody tr").first().click();
  await page.getByRole("button", { name: /Confirm mapping/i }).click();
  await page
    .getByText(/Mapped to/i)
    .first()
    .waitFor({ timeout: 10000 });
  await settle();
  await shot("09-map-confirmed");
});
for (const [path, heading, file] of [
  ["/review", "Review AI suggestions", "11-review"],
  ["/commit", "Check & commit", "12-commit"],
  ["/settings", "Configuration", "13-settings"],
  ["/users", "Users", "14-users"],
]) {
  await step(`page ${path}`, async () => {
    await page.goto(`${BASE}${path}`);
    await page.getByRole("heading", { name: heading }).waitFor();
    await settle();
    await page.waitForTimeout(800);
    await shot(file);
  });
}
await step("task tray", async () => {
  await page.getByRole("button", { name: /Tasks/ }).click();
  await page.waitForTimeout(300);
  await shot("15-task-tray");
  await page.keyboard.press("Escape");
});
await step("mobile layout", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/map`);
  await settle();
  await shot("16-mobile-map");
});
await step("logout", async () => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${BASE}/import`);
  await page.getByRole("button", { name: "admin" }).click();
  await page.getByRole("menuitem", { name: /Log out/ }).click();
  await page.getByLabel("Username").waitFor();
});
await browser.close();
console.log(JSON.stringify(report, null, 2));

// Fail the run (and CI) on any failed step, uncaught page error, console error or 5xx response
const failed = report.steps.filter((s) => s.startsWith("FAIL"));
const problems = failed.length + report.pageErrors.length + report.consoleErrors.length + report.badResponses.length;
if (problems > 0) {
  console.error(
    `Smoke test failed: ${failed.length} step(s), ${report.pageErrors.length} page error(s), ` +
      `${report.consoleErrors.length} console error(s), ${report.badResponses.length} 5xx response(s)`,
  );
  process.exit(1);
}
