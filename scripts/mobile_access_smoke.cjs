// Runs against the isolated authenticated production-entrypoint test host.
const { chromium } = require("playwright");
const fs = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(process.env.STOCKLAB_TEST_ORIGIN, {
      waitUntil: "networkidle",
    });
    await page.locator("#access-dialog").waitFor();
    await page.screenshot({ path: ".stocklab/verification/mobile-login.png" });
    await page.locator("#access-key").fill("wrong-demo-key");
    await page.locator("#access-form button[type=submit]").click();
    await page.waitForFunction(() =>
      document.querySelector("#access-error").textContent.includes("不正確"),
    );
    await page
      .locator("#access-key")
      .fill(process.env.STOCKLAB_TEST_ACCESS_KEY);
    await page.locator("#access-form button[type=submit]").click();
    await page.waitForFunction(() =>
      document
        .querySelector("#connection-state")
        .textContent.includes("已連線"),
    );
    if (await page.locator("#access-dialog").isVisible())
      throw Error("Login dialog did not close");
    if (!(await page.locator(".banner").textContent()).includes("免費體驗"))
      throw Error("Preview durability warning missing");
    const result = await page.evaluate(async () => ({
      status: await api("status"),
      datasets: await api("datasets"),
    }));
    if (
      result.datasets.length < 2 ||
      result.datasets.some((d) => d.source !== "SYNTHETIC_DEMO")
    )
      throw Error("Expected explicit synthetic preview data");
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    if (width.document > width.viewport) throw Error("Mobile layout overflow");
    await page.screenshot({
      path: ".stocklab/verification/mobile-host-preview.png",
    });
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = {
      checkedAt: new Date().toISOString(),
      wrongKeyRejected: true,
      authenticatedMobileLogin: true,
      publicHostMode: "isolated local test only",
      demoOnly: true,
      datasets: result.datasets.length,
      mobileWidth: width,
      pageErrors: errors,
    };
    fs.writeFileSync(
      ".stocklab/verification/mobile-access.json",
      JSON.stringify(evidence, null, 2),
    );
    console.log(JSON.stringify(evidence));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  const key = process.env.STOCKLAB_TEST_ACCESS_KEY;
  console.error(key ? e.message.replaceAll(key, "[REDACTED]") : e.message);
  process.exit(1);
});
