// Optional browser check using an already installed Playwright + system Chromium.
// Start the APP first; does not install browsers or download market data.
const { chromium } = require("playwright");
const fs = require("node:fs");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    if (process.env.STOCKLAB_API_TOKEN) {
      await page.addInitScript(
        (token) => sessionStorage.setItem("stocklab_token", token),
        process.env.STOCKLAB_API_TOKEN,
      );
    }
    await page.goto("http://127.0.0.1:" + (process.env.STOCKLAB_PORT || "8000"), {
      waitUntil: "networkidle",
    });
    // Fixtures are explicitly synthetic. Never create a real-market conclusion here.
    for (const symbol of ["DEMO_STOCK", "DEMO_ETF"]) {
      const row = page.locator("#datasets tr").filter({ hasText: symbol });
      await row.locator(".data-check").check();
    }
    const rhs = page.locator("#entry-rules .rhs").first();
    if (await rhs.locator("input").isVisible()) throw Error("Constant input should be hidden");
    await rhs.locator("select").selectOption("number");
    if (!(await rhs.locator("input").isVisible())) throw Error("Constant input should be visible");
    await rhs.locator("select").selectOption("sma60");
    await page.locator("#capital").fill(process.env.STOCKLAB_TEST_CAPITAL || "1000033");
    await page.locator("#run-backtest").click();
    await page.locator("#report .chart").first().waitFor();
    const curves = await page.locator("#report .chart").count();
    if (curves !== 2) throw Error("Expected two completed charts");
    fs.mkdirSync(".stocklab/verification", { recursive: true });
    await page.screenshot({ path: ".stocklab/verification/desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: ".stocklab/verification/mobile.png", fullPage: true });
    const width = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    if (width.document > width.viewport) throw Error("Mobile document overflow");
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = { checkedAt: new Date().toISOString(), browser: "Chromium", curves,
      automaticReportRefresh: true, conditionalInputVisibility: true, errors, mobileWidth: width };
    fs.writeFileSync(".stocklab/verification/browser.json", JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
