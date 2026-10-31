// Requires a running APP, existing DEMO_STOCK/DEMO_ETF and the explicit
// synthetic phase2-fixture.json generated during development. No market download.
const { chromium } = require("playwright");
const fs = require("node:fs");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    if (process.env.STOCKLAB_API_TOKEN)
      await page.addInitScript(
        (token) => sessionStorage.setItem("stocklab_token", token),
        process.env.STOCKLAB_API_TOKEN,
      );
    await page.goto(
      "http://127.0.0.1:" + (process.env.STOCKLAB_PORT || "8000"),
      { waitUntil: "networkidle" },
    );
    const fixture = JSON.parse(
      fs.readFileSync(".stocklab/verification/phase2-fixture.json", "utf8"),
    );
    await page.locator("#action-dataset").selectOption(String(fixture.id));
    await page
      .locator('#actions-form input[name="source"]')
      .fill("SYNTHETIC_BROWSER_TEST: not market evidence");
    await page
      .locator('#actions-form input[type="file"]')
      .setInputFiles({
        name: "synthetic-actions.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(fixture.csv),
      });
    const imported = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/actions") && r.request().method() === "POST",
    );
    await page.locator("#actions-form button[type=submit]").click();
    const response = await imported;
    if (response.status() !== 200)
      throw Error("Corporate action import failed");
    const actions = await response.json();
    await page.waitForFunction(() =>
      document.querySelector("#notice").textContent.includes("公司行動版本"),
    );
    await page
      .locator("#datasets tr")
      .filter({ hasText: "DEMO_ACTIONS" })
      .locator(".data-check")
      .check();
    await page.locator("#entry-rules .lhs").first().selectOption("close");
    await page
      .locator("#entry-rules .rhs select")
      .first()
      .selectOption("number");
    await page.locator("#entry-rules .rhs input").first().fill("0");
    await page.locator("#exit-rules .rule button").click();
    for (const [id, value] of Object.entries({
      capital: 1000,
      commission: 0,
      "min-fee": 0,
      slippage: 0,
      "sell-tax": 0,
    }))
      await page.locator("#" + id).fill(String(value));
    await page.locator("#run-backtest").click();
    await page.locator("#report .chart").first().waitFor();
    const jobId = await page.evaluate(() => activeJob);
    const result = await page.evaluate(
      async (id) => (await api("jobs/" + id)).result,
      jobId,
    );
    const output = Object.values(result.outputs)[0];
    if (
      output.metrics.final_equity !== 1000 ||
      output.corporate_actions.id !== actions.id
    )
      throw Error("Dividend/split ledger failed reconciliation");
    if (
      output.trades[0].entry_price !== 100 ||
      output.trades[0].quantity !== 20
    )
      throw Error("Fill audit is incorrect");
    await page
      .locator("#datasets tr")
      .filter({ hasText: "DEMO_ACTIONS" })
      .locator(".data-check")
      .uncheck();
    for (const symbol of ["DEMO_STOCK", "DEMO_ETF"])
      await page
        .locator("#datasets tr")
        .filter({ hasText: symbol })
        .locator(".data-check")
        .check();
    await page.locator("#run-walkforward").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector("#report")
          .textContent.includes("滾動樣本外結果"),
      null,
      { timeout: 60000 },
    );
    const rollingId = await page.evaluate(() => activeJob);
    const rolling = await page.evaluate(
      async (id) => (await api("jobs/" + id)).result,
      rollingId,
    );
    if (
      rolling.succeeded !== 2 ||
      Object.values(rolling.outputs).some((x) => x.folds.length < 2)
    )
      throw Error("Rolling research failed");
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({
      path: ".stocklab/verification/advanced-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    await page.screenshot({
      path: ".stocklab/verification/advanced-mobile.png",
      fullPage: true,
    });
    if (width.document > width.viewport)
      throw Error("Mobile overflow: " + JSON.stringify(width));
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = {
      checkedAt: new Date().toISOString(),
      engine: output.engine_version,
      backtestJob: jobId,
      actionVersion: actions.id,
      finalEquity: output.metrics.final_equity,
      walkforwardJob: rollingId,
      successfulRollingOutputs: rolling.succeeded,
      pageErrors: errors,
      mobileWidth: width,
      data: "SYNTHETIC_DEMO only",
    };
    fs.writeFileSync(
      ".stocklab/verification/advanced-browser.json",
      JSON.stringify(evidence, null, 2),
    );
    console.log(JSON.stringify(evidence));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
