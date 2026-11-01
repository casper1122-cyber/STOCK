// Runs against the isolated authenticated production-entrypoint test host.
const { chromium } = require("playwright");
const fs = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const ephemeral = process.env.STOCKLAB_TEST_EPHEMERAL === "1";
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
    if (!(await page.locator(".banner").textContent()).includes(ephemeral ? "免費暫存" : "免費體驗"))
      throw Error("Storage durability warning missing");
    if (ephemeral) {
      const csv = ["date,open,high,low,close,volume"];
      const date = new Date("2024-01-01T00:00:00Z");
      while (csv.length <= 160) {
        if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) {
          const price = 100 + csv.length;
          csv.push(`${date.toISOString().slice(0,10)},${price},${price},${price},${price},10000`);
        }
        date.setUTCDate(date.getUTCDate() + 1);
      }
      await page.evaluate(async (csv_text) => {
        if ((await api("datasets")).length) throw Error("Expected empty free host, no seeded demo data");
        await api("import", {symbol:"FREE_TEST",name:"合成軟體測試，非市場資料",csv_text});
        await refreshData();
      }, csv.join("\n"));
      await page.locator("#datasets tr").filter({hasText:"FREE_TEST"}).locator(".data-check").check();
      await page.locator("#run-backtest").click();
      await page.locator("#report .chart").first().waitFor();
      if (!(await page.locator("#schedule-enabled").isDisabled())) throw Error("Ephemeral schedule should be disabled");
      const downloadReady = page.waitForEvent("download");
      await page.locator("#backup-download").click();
      const download = await downloadReady;
      await download.saveAs(".stocklab/verification/free-host-backup.tar.gz");
      if (await download.failure()) throw Error("Backup download failed");
    }
    const result = await page.evaluate(async () => ({
      status: await api("status"),
      datasets: await api("datasets"),
    }));
    if (!ephemeral && (
      result.datasets.length < 2 ||
      result.datasets.some((d) => d.source !== "SYNTHETIC_DEMO")
    ))
      throw Error("Expected explicit synthetic preview data");
    const cachedRequests = await page.evaluate(async () => {
      const keys = await caches.keys();
      return (await Promise.all(keys.map(async k => (await (await caches.open(k)).keys()).map(r => new URL(r.url).pathname)))).flat();
    });
    if (cachedRequests.some(path => path.startsWith("/api/"))) throw Error("Private API entered PWA cache");
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    if (width.document > width.viewport) throw Error("Mobile layout overflow");
    await page.screenshot({
      path: ephemeral ? ".stocklab/verification/mobile-free-host.png" : ".stocklab/verification/mobile-host-preview.png",
    });
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = {
      checkedAt: new Date().toISOString(),
      wrongKeyRejected: true,
      authenticatedMobileLogin: true,
      publicHostMode: "isolated local test only",
      demoOnly: !ephemeral,
      ephemeralResearch: ephemeral,
      backupDownloaded: ephemeral,
      apiCacheEntries: 0,
      datasets: result.datasets.length,
      mobileWidth: width,
      pageErrors: errors,
    };
    fs.writeFileSync(
      ephemeral ? ".stocklab/verification/mobile-free-host.json" : ".stocklab/verification/mobile-access.json",
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
