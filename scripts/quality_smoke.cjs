// Explicit synthetic fixture only. Real calendars/source licensing are not verified here.
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
    const action = await page.evaluate(
      async (f) =>
        api("actions", {
          dataset_id: f.id,
          source: "SYNTHETIC_AUDIT_BROWSER_TEST",
          csv_text: f.csv,
        }),
      fixture,
    );
    await page.evaluate(() => refreshData());
    await page.locator("#quality-dataset").selectOption(String(fixture.id));
    await page.locator("#quality-actions").selectOption(String(action.id));
    function calendarCsv(bad) {
      let text = "date,is_open,reason\n";
      for (let day = 1; day <= 9; day++) {
        const date = `2020-01-${String(day).padStart(2, "0")}`;
        const dow = new Date(date + "T00:00:00Z").getUTCDay();
        const open = dow !== 0 && dow !== 6 && !(bad && day === 1) ? 1 : 0;
        text += `${date},${open},synthetic not official\n`;
      }
      return text;
    }
    async function uploadCalendar(bad) {
      await page
        .locator("#calendar-form input[name=source]")
        .fill(
          bad
            ? "SYNTHETIC bad calendar fixture"
            : "SYNTHETIC matching calendar fixture",
        );
      await page.locator("#calendar-form input[type=file]").setInputFiles({
        name: "synthetic-calendar.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(calendarCsv(bad)),
      });
      const sent = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/calendars") && r.request().method() === "POST",
      );
      await page.locator("#calendar-form button").click();
      const response = await sent;
      if (response.status() !== 200) throw Error("Calendar import failed");
      const c = await response.json();
      await page.waitForFunction(
        (id) =>
          document.querySelector("#quality-calendar").value === String(id),
        c.id,
      );
      return c;
    }
    async function inspect() {
      const done = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/audits") && r.request().method() === "POST",
      );
      await page.locator("#audit-form button[type=submit]").click();
      const response = await done;
      if (response.status() !== 200) throw Error("Audit failed unexpectedly");
      const r = await response.json();
      await page.waitForFunction(
        (id) =>
          document.querySelector(`#quality-report [data-audit-open="${id}"]`),
        r.id,
      );
      return r;
    }
    await uploadCalendar(true);
    const failed = await inspect();
    if (
      failed.status !== "issues_found" ||
      failed.closed_day_prices[0] !== "2020-01-01"
    )
      throw Error("Closed-date error was missed");
    await page.locator("#require-audit").check();
    await page.locator("#chat-dataset").selectOption(String(fixture.id));
    for (const [id, value] of Object.entries({
      capital: 1000,
      commission: 0,
      "min-fee": 0,
      slippage: 0,
      "sell-tax": 0,
    }))
      await page.locator("#" + id).fill(String(value));
    const message = "收盤價高於0進場，不設出場，本金1000";
    async function send() {
      await page.locator("#chat-input").fill(message);
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
      );
      await page.locator("#chat-send").click();
      return (await (await response).json()).assistant;
    }
    const blocked = await send();
    if (blocked.proposal.ready || !blocked.content.includes("尚未通過"))
      throw Error("Failed audit did not block strict chat");
    await uploadCalendar(false);
    const passed = await inspect();
    if (
      passed.status !== "passed_selected_checks" ||
      passed.source_independently_verified !== false
    )
      throw Error("Audit scope overstated");
    const ready = await send();
    if (!ready.proposal.ready) throw Error(ready.content);
    if (
      ready.proposal.request.audit_ids[fixture.id] !== passed.id ||
      ready.proposal.request.action_ids[fixture.id] !== action.id
    )
      throw Error("Version binding missing");
    const queued = page.waitForResponse((r) =>
      r.url().endsWith(`/api/chat/${ready.id}/run`),
    );
    await page.locator(`[data-run-proposal="${ready.id}"]`).click();
    const job = await (await queued).json();
    await page.waitForFunction(
      (id) =>
        document
          .querySelector(`[data-open-job="${id}"]`)
          ?.closest(".proposal-card")
          ?.querySelector(".chat-result"),
      job.id,
      { timeout: 45000 },
    );
    await page.locator(`[data-open-job="${job.id}"]`).last().click();
    await page.locator("#report .risk-details").first().waitFor();
    const output = await page.evaluate(
      async (id) => Object.values((await api(`jobs/${id}`)).result.outputs)[0],
      job.id,
    );
    if (
      output.data_audit.fingerprint !== passed.fingerprint ||
      output.metrics.final_equity !== 1000
    )
      throw Error("Result lost audit or accounting consistency");
    if (
      output.metrics.profit_factor !== null ||
      output.metrics.breakeven_trades !== 1
    )
      throw Error("Flat sample risk overstated");
    await page
      .locator("#report .result-block")
      .first()
      .screenshot({ path: ".stocklab/verification/risk-report.png" });
    await page.locator("#quality-report").scrollIntoViewIfNeeded();
    await page.screenshot({ path: ".stocklab/verification/data-quality.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    await page.screenshot({
      path: ".stocklab/verification/data-quality-mobile.png",
    });
    if (width.document > width.viewport)
      throw Error("Mobile overflow: " + JSON.stringify(width));
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = {
      checkedAt: new Date().toISOString(),
      engine: output.engine_version,
      blockedAudit: failed.id,
      acceptedAudit: passed.id,
      job: job.id,
      strictGate: true,
      exactActionVersion: action.id,
      finalEquity: output.metrics.final_equity,
      sourceVerified: false,
      riskInterval: output.metrics.win_rate_interval95,
      mobileWidth: width,
      pageErrors: errors,
      data: "SYNTHETIC_DEMO only",
    };
    fs.writeFileSync(
      ".stocklab/verification/quality-browser.json",
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
