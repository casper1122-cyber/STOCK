let auditVersions = [],
  calendarVersions = [];
const auditNames = {
  passed_selected_checks: "日期／版本檢查通過",
  issues_found: "發現待處理問題",
  needs_calendar: "尚缺日曆比對",
  not_provided: "未附檢查報告",
};
window.auditStatusLabel = (value) => auditNames[value] || "未附檢查報告";

window.auditInputsFor = (ids, actions = {}) => {
  const audit_ids = {};
  for (const id of ids) {
    const match = auditVersions.find(
      (a) =>
        a.dataset_id === id && (a.action_id || null) === (actions[id] || null),
    );
    if (match) audit_ids[id] = match.id;
  }
  return { audit_ids, require_audit: $("#require-audit").checked };
};
function qualityActionOptions() {
  const id = Number($("#quality-dataset").value),
    previous = $("#quality-actions").value;
  $("#quality-actions").innerHTML =
    '<option value="">不套公司行動</option>' +
    actionBundles
      .filter((a) => a.dataset_id === id)
      .map(
        (a) =>
          `<option value="${a.id}">事件 #${a.id} · ${a.event_count} 筆</option>`,
      )
      .join("");
  if ([...$("#quality-actions").options].some((o) => o.value === previous))
    $("#quality-actions").value = previous;
}
window.qualityDataChanged = async () => {
  [calendarVersions, auditVersions] = await Promise.all([
    api("calendars"),
    api("audits"),
  ]);
  const d = $("#quality-dataset").value,
    c = $("#quality-calendar").value;
  $("#quality-dataset").innerHTML = datasets
    .map((d) => `<option value="${d.id}">#${d.id} ${esc(d.symbol)}</option>`)
    .join("");
  if ([...$("#quality-dataset").options].some((o) => o.value === d))
    $("#quality-dataset").value = d;
  $("#quality-calendar").innerHTML =
    '<option value="">不套日曆，僅基本旗標</option>' +
    calendarVersions
      .map(
        (c) =>
          `<option value="${c.id}">#${c.id} ${esc(c.market)} · ${esc(c.source)} · ${esc(c.first_date)}–${esc(c.last_date)}</option>`,
      )
      .join("");
  if ([...$("#quality-calendar").options].some((o) => o.value === c))
    $("#quality-calendar").value = c;
  qualityActionOptions();
};
window.dataAuditHTML = (a) => {
  if (!a)
    return '<p class="muted">此研究未附資料檢查報告；交易日及事件完整性尚未核對。</p>';
  const current = auditVersions.find(
    (r) =>
      r.dataset_id === a.dataset_id &&
      (r.action_id || null) === (a.action_id || null),
  );
  const alert =
    current && current.id !== a.id && current.status === "issues_found"
      ? `<p class="error-text">目前選定的檢查 #${current.id} 發現問題；下方保留原研究快照，不代表現在已通過。<button class="ghost" data-audit-open="${current.id}">查看目前問題</button></p>`
      : "";
  return (
    alert +
    `<div class="audit-badge ${a.status === "issues_found" ? "audit-issue" : ""}"><b>${auditNames[a.status] || esc(a.status)}</b><span>價格 #${a.dataset_id} · 檢查快照 #${a.id}${a.action_id ? ` · 事件 #${a.action_id}` : ""} · 仍非完整資料認證</span><button class="ghost" data-audit-open="${a.id}">查看檢查依據</button></div>`
  );
};
window.riskSummaryHTML = (m) => {
  if (!("profit_factor_status" in m))
    return '<p class="muted">此舊版報告未計算擴充風險統計，請以新版重新提交。</p>';
  const interval = m.win_rate_interval95;
  const factor =
    m.profit_factor_status === "no_losses_observed"
      ? "尚未觀察到虧損"
      : m.profit_factor_status === "no_nonzero_trades"
        ? "無非零損益"
        : num(m.profit_factor);
  return `<details class="risk-details" open><summary>交易風險與樣本範圍</summary><div class="metric-grid risk-grid">${[
    ["Profit factor", factor],
    [
      "平均每筆淨損益",
      m.expectancy_cash == null ? "—" : "NT$ " + num(m.expectancy_cash),
    ],
    ["平均每筆報酬", pct(m.expectancy_return)],
    ["平均獲利 / 虧損", num(m.average_win) + " / " + num(m.average_loss)],
    ["最長連續虧損", m.max_consecutive_losses + " 筆"],
    ["最長回撤期間", m.longest_drawdown_bars + " 個觀測日"],
    ["年化波動率", pct(m.annual_volatility)],
    ["Sortino", num(m.sortino)],
    ["Calmar", num(m.calmar)],
  ]
    .map(([k, v]) => `<div><small>${k}</small><b>${esc(v)}</b></div>`)
    .join(
      "",
    )}</div><p class="muted">${interval ? `95% 勝率描述區間 ${pct(interval.lower)}–${pct(interval.upper)}（Wilson）` : "沒有完成交易，不估計勝率區間"} · ${esc(m.sample_note)}${m.drawdown_unrecovered ? " · 期末尚未恢復先前權益高點" : ""}</p><p class="risk-assumption">區間假設交易獨立，未校正交易相依、選樣與多重搜尋；不代表未來勝率。${esc(m.annualization_note)}</p></details>`;
};

function renderAudit(r) {
  const lists = [
    ["缺少開市日價格", r.missing_open_dates],
    ["休市日出現價格", r.closed_day_prices],
    ["日曆未涵蓋日期", r.uncovered_calendar_dates],
    ["事件後仍有大幅跳動", r.unexplained_moves_over_20pct],
    ["零成交量日期", r.zero_volume_dates],
  ];
  $("#quality-report").innerHTML =
    window.dataAuditHTML(r) +
    `<p class="muted">${esc(r.first_date)} → ${esc(r.last_date)} · ${r.price_rows} 筆價格 · 開市日期覆蓋 ${pct(r.open_day_coverage)} · 已提供 ${r.provided_events} 筆事件</p><p class="muted">日曆：${esc(r.calendar_source || "未提供")}。商品市場為提供者聲明，來源未獨立驗證。</p><div class="quality-counts">${lists.map(([name, values]) => `<div><b>${values.length}</b><span>${name}</span></div>`).join("")}</div>${warnings(r.blocking_issues)}${warnings(r.review_items)}<details><summary>日期明細（各顯示前 20 筆）</summary>${lists.map(([name, values]) => `<p><strong>${name}</strong>：${values.slice(0, 20).map(esc).join("、") || "無"}${values.length > 20 ? "…" : ""}</p>`).join("")}</details><button class="ghost" id="audit-export">下載完整檢查 JSON</button>`;
  $("#audit-export").onclick = () => {
    const blob = new Blob([JSON.stringify(r, null, 2)], {
        type: "application/json",
      }),
      a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `data-audit-${r.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
}
async function showAudit(id) {
  try {
    renderAudit(await api(`audits/${id}`));
    $("#data-quality").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    notice(e.message, true);
  }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-audit-open]");
  if (b) showAudit(Number(b.dataset.auditOpen));
});
$("#quality-dataset").onchange = qualityActionOptions;
$("#calendar-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target),
    b = e.target.querySelector("button");
  b.disabled = true;
  try {
    const file = f.get("file");
    if (file.size > 3000000) throw Error("日曆 CSV 上限 3 MB");
    const c = await api("calendars", {
      market: f.get("market"),
      source: f.get("source"),
      csv_text: await file.text(),
    });
    await window.qualityDataChanged();
    $("#quality-calendar").value = String(c.id);
    notice("日曆版本已保存，來源仍需核對。");
  } catch (e) {
    notice(e.message, true);
  } finally {
    b.disabled = false;
  }
};
$("#audit-form").onsubmit = async (e) => {
  e.preventDefault();
  const b = e.target.querySelector("button");
  b.disabled = true;
  try {
    const r = await api("audits", {
      dataset_id: Number($("#quality-dataset").value),
      calendar_id: $("#quality-calendar").value
        ? Number($("#quality-calendar").value)
        : null,
      action_id: $("#quality-actions").value
        ? Number($("#quality-actions").value)
        : null,
    });
    await window.qualityDataChanged();
    renderAudit(r);
    notice(auditNames[r.status] + "；請閱讀檢查範圍與未查核項目。");
  } catch (e) {
    notice(e.message, true);
  } finally {
    b.disabled = false;
  }
};
