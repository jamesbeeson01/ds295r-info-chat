// One-off e2e smoke test: drives the real UI against the local FastAPI
// backend (FAKE_MODEL=1). Not part of the test suite.
import { chromium } from "@playwright/test";

const url = process.env.E2E_URL ?? "http://localhost:3000";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
page.on("console", (m) => {
  if (m.type() === "error") console.log("[console.error]", m.text());
});
page.on("requestfailed", (r) =>
  console.log("[requestfailed]", r.url(), r.failure()?.errorText),
);

try {
  await page.goto(url, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForSelector("textarea", { timeout: 60000 });
  console.log("Page loaded; textarea present.");

  await page.fill("textarea", "When are office hours?");
  await page.keyboard.press("Enter");
  console.log("Message submitted.");

  await page.waitForSelector("text=fake model", { timeout: 60000 });
  console.log("AI response rendered.");

  // Follow-up message exercises the full-history submit path.
  await page.fill("textarea", "And where?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    () =>
      document.querySelectorAll("textarea") &&
      document.body.innerText.split("fake model").length >= 3,
    { timeout: 60000 },
  );
  console.log("Second AI response rendered. Thread ID in URL:", page.url());

  await page.screenshot({ path: "e2e-smoke.png", fullPage: true });
  console.log("E2E SMOKE PASSED");
} catch (err) {
  await page
    .screenshot({ path: "e2e-failure.png", fullPage: true })
    .catch(() => {});
  console.error("E2E SMOKE FAILED:", err);
  process.exitCode = 1;
} finally {
  await browser.close();
}
