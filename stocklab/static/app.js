const $ = (s) => document.querySelector(s);
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let datasets = [],
  actionBundles = [],
  selected = new Set(),
  noticeTimer,
  activeJob = null,
  activeStatus = null;
const fields = {
  close: "收盤價",
  open: "開盤價",
  volume: "成交量",
  sma5: "5 日均線",
  sma10: "10 日均線",
  sma20: "20 日均線",
  sma60: "60 日均線",
  sma120: "120 日均線",
  rsi14: "RSI 14",
  volume_ratio20: "成交量 / 20 日均量",
  return1: "1 日報酬（小數）",
  return5: "5 日報酬（小數）",
  volatility20: "20 日年化波動率",
};
function notice(text, error = false) {
  clearTimeout(noticeTimer);
  $("#notice").textContent = text;
  $("#notice").className = error ? "error" : "";
  noticeTimer = setTimeout(
    () => ($("#notice").textContent = ""),
    error ? 15000 : 6000,
  );
}
async function api(path, body, method) {
  const headers = {};
  const token = sessionStorage.getItem("stocklab_token");
  if (token) headers.Authorization = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch("/api/" + path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw Error(
      "目前無法連上研究主機，請確認連線後重試；已保存的工作仍可在重新連線後查看。",
    );
  }
  if (!r.ok) {
    if (r.status === 401 && window.openResearchLogin)
      window.openResearchLogin();
    let d;
    try {
      d = await r.json();
    } catch {
      d = { detail: "伺服器回應失敗" };
    }
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail),
    );
  }
  return r.json();
}
function opts(value, constant = false) {
  return (
    (constant ? '<option value="number">自訂數值</option>' : "") +
    Object.entries(fields)
      .map(
        ([k, v]) =>
          `<option value="${k}" ${k === value ? "selected" : ""}>${v}</option>`,
      )
      .join("")
  );
}
function addRule(target, field = "sma20", op = "gt", value = "sma60") {
  const row = document.createElement("div");
  row.className = "rule";
  row.innerHTML = `<select class="lhs" aria-label="指標">${opts(field)}</select><select class="op" aria-label="比較"><option value="gt">&gt;</option><option value="ge">≥</option><option value="lt">&lt;</option><option value="le">≤</option><option value="cross_above">突破</option><option value="cross_below">跌破</option></select><div class="rhs"><select aria-label="比較對象">${opts(value, true)}</select><input type="number" step="any" aria-label="比較數值" hidden></div><button type="button" aria-label="刪除條件">×</button>`;
  row.querySelector(".op").value = op;
  const choice = row.querySelector(".rhs select"),
    input = row.querySelector(".rhs input");
  if (typeof value === "number") {
    choice.value = "number";
    input.hidden = false;
    input.value = value;
  }
  choice.onchange = () => (input.hidden = choice.value !== "number");
  row.querySelector("button").onclick = () => row.remove();
  $(target).append(row);
}
function rules(target) {
  return [...$(target).children].map((r) => {
    const v = r.querySelector(".rhs select").value;
    const raw = r.querySelector(".rhs input").value;
    if (v === "number" && raw === "") throw Error("請填入條件的比較數值");
    return {
      field: r.querySelector(".lhs").value,
      op: r.querySelector(".op").value,
      value: v === "number" ? Number(raw) : v,
    };
  });
}
const optionalNumber = (id) =>
  $(id).value === "" ? null : Number($(id).value);
function costs() {
  return {
    initial_cash: Number($("#capital").value),
    commission: Number($("#commission").value) / 100,
    min_commission: Number($("#min-fee").value),
    slippage: Number($("#slippage").value) / 100,
    sell_tax:
      optionalNumber("#sell-tax") === null
        ? null
        : optionalNumber("#sell-tax") / 100,
    lot_size: Number($("#lot-size").value),
  };
}
function strategy() {
  return {
    entry: rules("#entry-rules"),
    exit: rules("#exit-rules"),
    entry_mode: $("#entry-mode").value,
    exit_mode: $("#exit-mode").value,
    max_hold: optionalNumber("#max-hold"),
    stop_loss:
      optionalNumber("#stop-loss") === null
        ? null
        : optionalNumber("#stop-loss") / 100,
  };
}
async function refreshData() {
  datasets = await api("datasets");
  actionBundles = await api("actions");
  const oldActionDataset = $("#action-dataset").value;
  $("#action-dataset").innerHTML = datasets
    .filter(
      (d) => d.price_basis === "raw" && ["stock", "etf"].includes(d.asset_type),
    )
    .map((d) => `<option value="${d.id}">#${d.id} ${esc(d.symbol)}</option>`)
    .join("");
  if (
    [...$("#action-dataset").options].some((o) => o.value === oldActionDataset)
  )
    $("#action-dataset").value = oldActionDataset;
  $("#actions-summary").textContent = actionBundles.length
    ? `已保存 ${actionBundles.length} 個事件版本；回測會記錄使用的事件指紋。`
    : "尚未匯入公司行動；原始價格回測仍不含股利與拆股。";
  $("#dataset-count").textContent = datasets.length;
  $("#datasets").innerHTML = datasets.length
    ? datasets
        .map(
          (d) =>
            `<tr><td><input class="data-check" type="checkbox" value="${d.id}" ${selected.has(d.id) ? "checked" : ""} aria-label="選取 ${esc(d.symbol)}"></td><td><b>${esc(d.symbol)}</b> <span class="badge">${esc(d.asset_type)}</span><small>${esc(d.name || d.source)}</small><small>版本 #${d.id} · ${esc(d.source)}</small></td><td>${esc(d.first_date)} → ${esc(d.last_date)}</td><td>${d.rows.toLocaleString()}</td><td>${d.price_basis === "raw" ? "未還原 / 未含息" : "提供者已還原"}</td><td><details><summary>${d.source === "SYNTHETIC_DEMO" ? "合成示範" : "結構檢查通過，未完成稽核"}</summary><pre>${esc(JSON.stringify(d.quality, null, 2))}</pre></details></td></tr>`,
        )
        .join("")
    : '<tr><td colspan="6" class="empty">尚無資料。請下載或匯入歷史 CSV。</td></tr>';
  document
    .querySelectorAll(".data-check")
    .forEach(
      (c) =>
        (c.onchange = () =>
          c.checked
            ? selected.add(Number(c.value))
            : selected.delete(Number(c.value))),
    );
  if (window.studyDataChanged) window.studyDataChanged();
  if (window.qualityDataChanged) await window.qualityDataChanged();
}
async function refreshJobs() {
  const jobs = await api("jobs");
  if (window.studyJobsChanged) await window.studyJobsChanged(jobs);
  $("#done-count").textContent =
    jobs.filter((j) => j.status === "done").length + " / " + jobs.length;
  const names = {
      backtest: "自訂條件回測",
      research: "均線候選搜尋",
      correlation: "跨商品關聯",
      daily: "每日更新與研究",
      walkforward: "滾動樣本外驗證",
    },
    states = {
      queued: "等待中",
      running: "執行中",
      done: "已完成",
      failed: "失敗",
    };
  $("#jobs").innerHTML = jobs.length
    ? jobs
        .map(
          (j) =>
            `<div class="job" data-id="${j.id}" role="button" tabindex="0"><span class="status ${j.status}">${states[j.status]}</span><b>#${j.id} ${names[j.request.kind]}</b><small>${new Date(j.created).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })} · ${j.request.dataset_ids.length} 組資料</small>${j.error ? `<span class="error-text">${esc(j.error)}</span>` : ""}</div>`,
        )
        .join("")
    : "尚無工作";
  document.querySelectorAll(".job").forEach((el) => {
    el.onclick = () =>
      showJob(el.dataset.id).catch((e) => notice(e.message, true));
    el.onkeydown = (e) => {
      if (e.key === "Enter") el.click();
    };
  });
  const active = jobs.find((j) => j.id === activeJob);
  if (active && active.status !== activeStatus) await showJob(activeJob);
}
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(2) + "%");
const num = (x) => (x == null ? "—" : Number(x).toFixed(2));
function metricHTML(m) {
  return `<div class="metric-grid">${[
    ["淨報酬", pct(m.total_return)],
    ["年化報酬", pct(m.cagr)],
    ["最大回撤", pct(m.max_drawdown)],
    ["Sharpe", num(m.sharpe)],
    ["完成交易", m.trades],
    ["勝率", pct(m.win_rate)],
  ]
    .map(([k, v]) => `<div><small>${k}</small><b>${esc(v)}</b></div>`)
    .join(
      "",
    )}</div><p class="muted">${esc(m.start)} → ${esc(m.end)} · ${m.bars} 根日線 · 總成本 NT$ ${num(m.total_costs)}</p>`;
}
function chart(result) {
  const curves = [result.curve, result.benchmark.curve],
    all = curves.flatMap((c) => c.map((p) => p.equity)),
    min = all.reduce((a, b) => Math.min(a, b), Infinity),
    max = all.reduce((a, b) => Math.max(a, b), -Infinity),
    span = max - min || 1;
  const w = 640,
    h = 210,
    pad = 20;
  const paths = curves.map((c) =>
    c
      .map(
        (p, i) =>
          `${i ? "L" : "M"}${(pad + (i * (w - 2 * pad)) / Math.max(c.length - 1, 1)).toFixed(1)},${(h - pad - ((p.equity - min) * (h - 2 * pad)) / span).toFixed(1)}`,
      )
      .join(" "),
  );
  return `<div class="legend"><span>策略權益</span><span>同期間買入持有（含成本）</span></div><svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="策略與買入持有權益比較"><line x1="20" y1="190" x2="620" y2="190" stroke="#dce4df"/><path d="${paths[1]}" fill="none" stroke="#aaafa4" stroke-width="1.5"/><path d="${paths[0]}" fill="none" stroke="#176a52" stroke-width="2"/></svg>`;
}
const warnings = (w) =>
  `<ul class="warning-list">${(w || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
function tradesHTML(r) {
  return `<details><summary>成交對帳（共 ${r.trades.length} 筆，顯示前 100 筆）</summary><div class="table-wrap"><table><tr><th>進場 → 出場</th><th>進場股數 → 出場股數</th><th>原始成交價（買／賣）</th><th>股利權利</th><th>淨損益</th><th>總成本</th></tr>${r.trades
    .slice(0, 100)
    .map(
      (t) =>
        `<tr><td>${esc(t.entry_date)} → ${esc(t.exit_date)}</td><td>${t.initial_quantity ?? t.quantity} → ${t.quantity}</td><td>${num(t.entry_price)} / ${num(t.exit_price)}</td><td>${num(t.dividend_amount || 0)}</td><td>${num(t.pnl)}</td><td>${num(t.costs)}</td></tr>`,
    )
    .join(
      "",
    )}</table></div><p class="muted">股利權利包含尚未付款的應收。完整紀錄可下載 JSON；券商逐筆費稅取整尚未建模。</p></details>`;
}
function backtestHTML(r) {
  return (
    metricHTML(r.metrics) +
    chart(r) +
    (window.riskSummaryHTML ? window.riskSummaryHTML(r.metrics) : "") +
    tradesHTML(r) +
    `<p class="muted">買入持有淨報酬 ${pct(r.benchmark.metrics.total_return)} · 價格口徑 ${esc(r.dataset.price_basis)}${r.corporate_actions ? ` · 事件 #${r.corporate_actions.id}` : " · 無公司行動版本"}</p><details><summary>參數、成本與完整成交紀錄（${r.trades.length} 筆）</summary><pre>${esc(JSON.stringify({ strategy: r.strategy, costs: r.costs, trades: r.trades, corporate_actions: r.corporate_actions, ledger: r.corporate_action_ledger, pending_dividends: r.pending_dividends }, null, 2))}</pre></details><details><summary>模型假設與資料限制</summary>${warnings(r.warnings)}</details>`
  );
}
async function showJob(id) {
  const j = await api("jobs/" + id);
  activeJob = Number(id);
  activeStatus = j.status;
  let html = `<div class="row"><h3>工作 #${j.id} · 引擎 ${esc(j.engine_version)}</h3><button id="export-job" class="ghost">下載完整 JSON</button></div>`;
  if (j.error)
    html += `<p class="error-text">${esc(j.error)}</p><button id="retry-job" class="ghost">修正問題後重試</button>`;
  const r = j.result;
  if (!r)
    html += `<p class="muted">狀態：${esc(j.status)}。工作由背景程式執行，離開此頁不會取消；重新點選工作查看結果。</p>`;
  else if (r.pairs) {
    html += warnings(r.warnings);
    for (const p of r.pairs)
      html += `<h3>${esc(p.x.symbol)} → ${esc(p.y.symbol)}</h3><div class="table-wrap"><table><tr><th>領先共同觀測日</th><th>相關係數</th><th>樣本數</th></tr>${p.lags.map((x) => `<tr><td>${x.lag_common_sessions}</td><td>${num(x.correlation)}</td><td>${x.observations}</td></tr>`).join("")}</table></div>`;
  } else {
    html += `<p class="muted">成功 ${r.succeeded} 組 / 失敗 ${r.failed} 組；各商品獨立研究。</p>`;
    for (const [key, error] of Object.entries(r.errors || {}))
      html += `<p class="error-text">資料 #${esc(key)}：${esc(error)}</p>`;
    for (const output of Object.values(r.outputs || {})) {
      html += `<article class="result-block"><hr><h3>${esc(output.dataset.symbol)} ${esc(output.dataset.name)}</h3><p class="muted">資料 #${output.dataset.id} · ${esc(output.dataset.source)} · 指紋 ${esc(output.dataset.fingerprint.slice(0, 12))}</p>`;
      if (window.dataAuditHTML) html += window.dataAuditHTML(output.data_audit);
      if (output.folds) {
        const s = output.summary;
        html += `<span class="tag">滾動樣本外結果 · 各段獨立資金</span><div class="metric-grid"><div><small>完整測試段</small><b>${s.folds}</b></div><div><small>正報酬段</small><b>${s.positive_folds} / ${s.folds}</b></div><div><small>段報酬中位數</small><b>${pct(s.median_test_return)}</b></div></div><p class="muted">有合格條件 ${s.selected_folds} 段；未測尾端 ${output.untested_tail_bars} 筆。</p>${warnings(output.warnings)}<div class="table-wrap"><table><tr><th>段</th><th>測試期間</th><th>選擇</th><th>淨報酬</th><th>買入持有</th><th>最大回撤</th></tr>${output.folds.map((f) => `<tr><td>${f.index}</td><td>${esc(f.test_period.join(" → "))}</td><td>${f.selected_strategy ? "訓練／驗證選擇" : "持有現金"}</td><td>${pct(f.test.metrics.total_return)}</td><td>${pct(f.test.benchmark.metrics.total_return)}</td><td>${pct(f.test.metrics.max_drawdown)}</td></tr>`).join("")}</table></div><details><summary>各段選擇與成交明細</summary>${output.folds.map((f) => `<details><summary>第 ${f.index} 段 ${esc(f.selection_error || "已選擇條件")}</summary>${backtestHTML(f.test)}</details>`).join("")}</details>`;
      } else if (output.test) {
        html +=
          `<span class="tag">以下為保留測試段結果</span><details><summary>時間切分、候選與選擇過程</summary><pre>${esc(JSON.stringify({ split: output.split, shortlist: output.shortlist }, null, 2))}</pre></details>` +
          warnings(output.warnings) +
          backtestHTML(output.test);
      } else html += backtestHTML(output);
      html += "</article>";
    }
  }
  $("#report").innerHTML = html;
  $("#export-job").onclick = async () => {
    const blob = new Blob([JSON.stringify(j, null, 2)], {
        type: "application/json",
      }),
      a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `research-${id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  if ($("#retry-job"))
    $("#retry-job").onclick = async () => {
      try {
        await api(`jobs/${id}/retry`, {});
        notice("工作已重新排隊");
        await refreshJobs();
      } catch (e) {
        notice(e.message, true);
      }
    };
}
async function queue(kind) {
  if (!selected.size) return notice("請先在資料工作台選取資料", true);
  try {
    const req = { kind, dataset_ids: [...selected], costs: costs() };
    if (
      ["backtest", "research", "walkforward"].includes(kind) &&
      $("#use-actions").checked
    ) {
      req.action_ids = {};
      for (const id of selected) {
        const bundle = actionBundles.find((x) => x.dataset_id === id);
        if (bundle) req.action_ids[id] = bundle.id;
      }
    }
    if (kind === "walkforward")
      req.walk_forward = {
        train_bars: Number($("#wf-train").value),
        validation_bars: Number($("#wf-validation").value),
        test_bars: Number($("#wf-test").value),
      };
    if (window.auditInputsFor) {
      const quality = window.auditInputsFor(
        req.dataset_ids,
        req.action_ids || {},
      );
      if (kind === "correlation") quality.audit_ids = {};
      Object.assign(req, quality);
    }
    if (kind === "backtest") req.strategy = strategy();
    const job = await api("jobs", req);
    notice(`工作 #${job.id} 已保存；相同資料與參數會沿用已有工作`);
    await refreshJobs();
    await showJob(job.id);
    $("#results").scrollIntoView();
  } catch (e) {
    notice(e.message, true);
  }
}
$("#download-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target),
    btn = e.target.querySelector("button");
  btn.disabled = true;
  notice("正在下載與驗證資料…");
  try {
    const d = await api("download", {
      symbol: f.get("symbol"),
      asset_type: f.get("asset_type"),
      start: f.get("start"),
      end: f.get("end") || null,
    });
    selected.add(d.id);
    await refreshData();
    notice(`已保存 ${d.symbol}，${d.rows} 筆資料`);
  } catch (e) {
    notice(e.message, true);
  } finally {
    btn.disabled = false;
  }
};
$("#import-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target),
    btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const file = f.get("file");
    if (file.size > 8000000) throw Error("CSV 上限 8 MB");
    const d = await api("import", {
      symbol: f.get("symbol"),
      asset_type: f.get("asset_type"),
      price_basis: f.get("price_basis"),
      csv_text: await file.text(),
    });
    selected.add(d.id);
    await refreshData();
    notice("資料已檢查並建立不可變版本");
  } catch (e) {
    notice(e.message, true);
  } finally {
    btn.disabled = false;
  }
};
$("#add-entry").onclick = () => addRule("#entry-rules", "rsi14", "gt", 50);
$("#add-exit").onclick = () => addRule("#exit-rules", "rsi14", "lt", 40);
addRule("#entry-rules");
addRule("#exit-rules", "sma20", "lt", "sma60");
$("#run-backtest").onclick = () => queue("backtest");
$("#run-research").onclick = () => queue("research");
$("#run-walkforward").onclick = () => queue("walkforward");
$("#run-correlation").onclick = () => queue("correlation");
$("#actions-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target),
    button = e.target.querySelector("button");
  button.disabled = true;
  try {
    const file = f.get("file");
    if (file.size > 2000000) throw Error("公司行動 CSV 上限 2 MB");
    const a = await api("actions", {
      dataset_id: Number(f.get("dataset_id")),
      source: f.get("source"),
      csv_text: await file.text(),
    });
    await refreshData();
    notice(
      `公司行動版本 #${a.id} 已保存，包含 ${a.events.length} 筆事件；尚未獨立驗證完整性`,
    );
  } catch (e) {
    notice(e.message, true);
  } finally {
    button.disabled = false;
  }
};
$("#refresh").onclick = () =>
  Promise.all([refreshData(), refreshJobs()]).catch((e) =>
    notice(e.message, true),
  );
$("#token-btn").onclick = () => {
  const token = prompt(
    "若主機設定 STOCKLAB_API_TOKEN，請輸入相同金鑰。只保存在此分頁 sessionStorage；本機未啟用驗證可留空。",
  );
  if (token !== null) {
    token
      ? sessionStorage.setItem("stocklab_token", token)
      : sessionStorage.removeItem("stocklab_token");
    location.reload();
  }
};
$("#schedule-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const [hour, minute] = $("#schedule-time").value.split(":").map(Number);
    const s = await api(
      "schedule",
      {
        enabled: $("#schedule-enabled").checked,
        hour,
        minute,
        dataset_ids: [...selected],
        costs: costs(),
        backup: $("#schedule-backup").checked,
      },
      "PUT",
    );
    $("#schedule-summary").textContent =
      `${s.enabled ? "已啟用" : "已停用"} · ${s.dataset_ids.length} 組資料 · 台北 ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`;
    notice("排程已保存；需主機及 APP 持續執行");
  } catch (e) {
    notice(e.message, true);
  }
};
async function init() {
  try {
    const s = await api("status");
    $("#version").textContent = "v" + s.engine_version + " · 研究開發版";
    await refreshData();
    await refreshJobs();
    const schedule = await api("schedule");
    $("#schedule-enabled").checked = schedule.enabled;
    $("#schedule-backup").checked = schedule.backup;
    $("#schedule-time").value =
      String(schedule.hour).padStart(2, "0") +
      ":" +
      String(schedule.minute).padStart(2, "0");
    $("#schedule-summary").textContent =
      schedule.available === false
        ? schedule.unavailable_reason
        : `已儲存 ${schedule.dataset_ids.length} 組資料；${schedule.enabled ? "啟用中" : "未啟用"}${schedule.backup_status?.error ? "；" + schedule.backup_status.error : ""}`;
    if (schedule.available === false) {
      $("#schedule-enabled").disabled = true;
      $("#schedule-form button[type=submit]").disabled = true;
    }
  } catch (e) {
    notice(e.message, true);
  }
}
init();
setInterval(() => {
  if (!document.hidden) refreshJobs().catch(() => {});
}, 4000);
