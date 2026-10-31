// Run the APP and explicit CLI demo fixtures first. Exercises real browser/API/worker.
const { chromium } = require("playwright");
const fs = require("node:fs");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1100 },
    });
    if (process.env.STOCKLAB_API_TOKEN)
      await context.addInitScript(
        (token) => sessionStorage.setItem("stocklab_token", token),
        process.env.STOCKLAB_API_TOKEN,
      );
    const errors = [];
    context.on("page", (p) => p.on("pageerror", (e) => errors.push(e.message)));
    let page = await context.newPage();
    const origin = "http://127.0.0.1:" + (process.env.STOCKLAB_PORT || "8000");
    await page.goto(origin, { waitUntil: "networkidle" });
    await page.waitForFunction(
      () => document.querySelector("#chat-dataset").options.length > 1,
    );
    const options = await page
      .locator("#chat-dataset option")
      .evaluateAll((list) =>
        list.map((o) => ({ value: o.value, text: o.textContent })),
      );
    const target = options.find((o) => o.text.includes("DEMO_STOCK"));
    if (!target) throw Error("Explicit DEMO_STOCK fixture required");
    await page.locator("#chat-dataset").selectOption(target.value);
    const capital = 1000000 + (Date.now() % 100000); // distinct request: verify queued work survives page close
    await page
      .locator("#chat-input")
      .fill(
        `20日均線高於60日均線進場，20日均線低於60日均線出場，本金${capital}`,
      );
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
    );
    await page.locator("#chat-send").click();
    const proposal = (await (await response).json()).assistant;
    if (!proposal.proposal.ready) throw Error(proposal.content);
    await page.locator(`[data-run-proposal="${proposal.id}"]`).waitFor();
    const previewText = await page
      .locator(`[data-run-proposal="${proposal.id}"]`)
      .evaluate((b) => b.closest(".proposal-card").textContent);
    if (!previewText.includes("0.1425%"))
      throw Error("Commission preview lost precision");
    const submitted = page.waitForResponse((r) =>
      r.url().endsWith(`/api/chat/${proposal.id}/run`),
    );
    await page.locator(`[data-run-proposal="${proposal.id}"]`).click();
    const job = await (await submitted).json();
    if (!["queued", "running"].includes(job.status))
      throw Error("Expected a new unfinished job before closing the page");
    await page.close();
    // A new page recovers server-side conversation and background results.
    page = await context.newPage();
    await page.goto(origin, { waitUntil: "networkidle" });
    await page.waitForFunction(
      (id) =>
        document
          .querySelector(`[data-open-job="${id}"]`)
          ?.closest(".proposal-card")
          ?.querySelector(".chat-result"),
      job.id,
      { timeout: 45000 },
    );
    const finished = await page.evaluate(
      (id) => api(`jobs/${id}/summary`),
      job.id,
    );
    if (finished.status !== "done" || !finished.outputs.length)
      throw Error("Background job did not complete");
    await page.locator("#chat-input").fill("停損改成3%");
    const changed = page.waitForResponse(
      (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
    );
    await page.locator("#chat-send").click();
    const follow = (await (await changed).json()).assistant.proposal;
    if (
      !follow.ready ||
      follow.request.strategy.stop_loss !== 0.03 ||
      follow.request.costs.initial_cash !== capital
    )
      throw Error("Follow-up lost previous conditions");
    const ranking = await page.evaluate(() => api("dashboard"));
    if (ranking.candidates.some((c) => c.source === "SYNTHETIC_DEMO"))
      throw Error("Synthetic data leaked into default market ranking");
    await page.locator("#chat-dataset").selectOption(target.value);
    await page.locator("#chat-input").fill("找進場條件");
    const search = page.waitForResponse(
      (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
    );
    await page.locator("#chat-send").click();
    const searchMessage = (await (await search).json()).assistant;
    if (!searchMessage.proposal.ready) throw Error(searchMessage.content);
    const searchRun = page.waitForResponse((r) =>
      r.url().endsWith(`/api/chat/${searchMessage.id}/run`),
    );
    await page.locator(`[data-run-proposal="${searchMessage.id}"]`).click();
    const researchJob = await (await searchRun).json();
    await page.waitForFunction(
      (id) =>
        document
          .querySelector(`[data-open-job="${id}"]`)
          ?.closest(".proposal-card")
          ?.textContent.includes("保留測試段"),
      researchJob.id,
      { timeout: 45000 },
    );
    await page.locator("#rank-show-demo").check();
    await page.locator("#rank-min-trades").selectOption("5");
    await page.waitForTimeout(800);
    await page.evaluate(() => scrollTo(0, 0));
    fs.mkdirSync(".stocklab/verification", { recursive: true });
    await page.screenshot({ path: ".stocklab/verification/app-home.png" });
    await page.locator("#conversation").scrollIntoViewIfNeeded();
    await page.screenshot({ path: ".stocklab/verification/app-chat.png" });
    await page.evaluate(() => navigator.serviceWorker.ready);
    // Ensure the next navigation is controlled before testing the offline shell.
    await page.reload({ waitUntil: "networkidle" });
    const cached = await page.evaluate(async () => {
      const names = await caches.keys();
      return (
        await Promise.all(
          names.map(async (n) =>
            (await (await caches.open(n)).keys()).map((r) => r.url),
          ),
        )
      ).flat();
    });
    if (cached.some((u) => u.includes("/api/")))
      throw Error("Private API cached by service worker");
    await context.setOffline(true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#chat-input").waitFor();
    await page.waitForFunction(() =>
      document.querySelector("#connection-state").textContent.includes("尚未"),
    );
    await context.setOffline(false);
    await page.reload({ waitUntil: "networkidle" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => scrollTo(0, 0));
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    await page.screenshot({ path: ".stocklab/verification/app-mobile.png" });
    if (width.document > width.viewport)
      throw Error("Mobile overflow: " + JSON.stringify(width));
    if (errors.length) throw Error(JSON.stringify(errors));
    const evidence = {
      checkedAt: new Date().toISOString(),
      engine: finished.engine_version,
      chatJob: job.id,
      statusBeforePageClose: job.status,
      statusAfterReopen: finished.status,
      followupPreservedParameters: true,
      defaultRankingExcludedSynthetic: true,
      offlineShell: true,
      privateApiCacheEntries: 0,
      mobileWidth: width,
      pageErrors: errors,
      data: "SYNTHETIC_DEMO only",
    };
    fs.writeFileSync(
      ".stocklab/verification/chat-browser.json",
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
