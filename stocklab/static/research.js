const feePct = (x) =>
  (Number(x) * 100).toLocaleString("zh-TW", { maximumFractionDigits: 6 }) + "%";
// Chat proposals and evidence summaries stay on the research host. No LLM calls.
const studyState = {
  messages: [],
  jobs: new Map(),
  summaries: new Map(),
  loading: new Set(),
  completedSignature: "",
};
let installPrompt = null;
const kindNames = {
  backtest: "自訂條件回測",
  research: "均線候選研究",
  walkforward: "滾動樣本外驗證",
  correlation: "跨商品關聯",
};
const stateNames = {
  queued: "等待運算",
  running: "研究中",
  done: "已完成",
  failed: "未完成",
};

function resultSnippet(summary) {
  if (summary.error) return `<p class="error-text">${esc(summary.error)}</p>`;
  const outputs = summary.outputs || [];
  return (
    outputs
      .slice(0, 3)
      .map((o) => {
        const demo =
          o.source === "SYNTHETIC_DEMO"
            ? '<span class="sample-label">合成示範</span>'
            : "";
        if (o.walkforward_summary) {
          const s = o.walkforward_summary;
          return `<div class="chat-result"><strong>${esc(o.symbol)} ${demo}</strong><p>滾動測試 ${s.folds} 段 · 正報酬 ${s.positive_folds} 段 · 段報酬中位數 ${pct(s.median_test_return)}</p><small>各段獨立資金，不是連續複利績效。</small></div>`;
        }
        const m = o.metrics;
        if (!m) return "";
        return `<div class="chat-result"><strong>${esc(o.symbol)} ${demo}</strong><small>${o.scope === "held_out_test" ? "保留測試段" : "指定條件回測"} · ${esc(m.start)} → ${esc(m.end)} · ${window.auditStatusLabel ? window.auditStatusLabel(o.data_audit_status) : "未附檢查報告"}</small><div class="chat-result-numbers"><span>勝率 <b>${pct(m.win_rate)}</b></span><span>交易 <b>${m.trades} 筆</b></span><span>淨報酬 <b>${pct(m.total_return)}</b></span><span>最大回撤 <b>${pct(m.max_drawdown)}</b></span></div>${m.win_rate_interval95 ? `<small>95%勝率描述區間 ${pct(m.win_rate_interval95.lower)}–${pct(m.win_rate_interval95.upper)}；交易可能相依，非未來保證。</small>` : ""}</div>`;
      })
      .join("") +
    (summary.correlation_pairs
      ? `<p>已完成 ${summary.correlation_pairs} 組商品配對，請開啟完整報告查看樣本數與限制。</p>`
      : "") +
    (summary.failed
      ? `<p class="error-text">另有 ${summary.failed} 組未完成，請查看完整錯誤。</p>`
      : "") +
    (outputs.length > 3 || summary.omitted_outputs
      ? "<p class=muted>此處顯示前 3 組摘要，其餘請開啟完整報告。</p>"
      : "")
  );
}

function renderChat(scroll = true) {
  const log = $("#research-chat");
  if (!studyState.messages.length) {
    log.innerHTML =
      '<div class="chat-welcome"><h3>先從一個小問題開始。</h3><p>選一份資料，再輸入進出場條件。也可以直接寫已匯入的商品代號。</p><p class="muted">例如「RSI低於30進場，RSI高於70出場」。助理會先列出規則，不會猜測或自動下單。</p></div>';
    return;
  }
  log.innerHTML = studyState.messages
    .map((m) => {
      let content = `<p class="message-text">${esc(m.content)}</p>`;
      const p = m.proposal;
      if (p?.ready) {
        const req = p.request,
          c = req.costs;
        content += `<div class="proposal-card"><strong>${kindNames[req.kind]}</strong><ul class="proposal-rules">${p.description.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="proposal-targets">${p.targets.map((t) => `<div><b>${esc(t.symbol)}</b> <small>版本 #${t.id} · ${esc(t.first_date)} → ${esc(t.last_date)} · ${esc(t.price_basis)}${req.action_ids[t.id] ? ` · 事件 #${req.action_ids[t.id]}` : ""}${req.audit_ids?.[t.id] ? ` · 檢查 #${req.audit_ids[t.id]}` : ""}</small></div>`).join("")}</div><p class="muted">本金 NT$ ${Number(c.initial_cash).toLocaleString()} · 單邊費 ${feePct(c.commission)} · 最低費 NT$ ${c.min_commission} · 滑價 ${feePct(c.slippage)} · 賣出稅 ${c.sell_tax == null ? "依商品預設" : feePct(c.sell_tax)}</p><details><summary>資料與執行假設</summary>${warnings(p.notes)}</details>`;
        if (m.job_id) {
          const job = studyState.jobs.get(m.job_id),
            summary = studyState.summaries.get(m.job_id);
          content += `<div class="chat-job-state">工作 #${m.job_id} · ${stateNames[job?.status || summary?.status] || "已保存"}</div>`;
          if (summary) content += resultSnippet(summary);
          content += `<button class="secondary" data-open-job="${m.job_id}">查看完整報告與圖表 →</button>`;
        } else {
          content += `<div class="proposal-actions"><button data-run-proposal="${m.id}">確認條件並開始</button><button class="ghost" data-edit-proposal="${m.id}">修改文字</button></div>`;
        }
        content += "</div>";
      }
      return `<article class="chat-message ${m.role}"><span class="message-role">${m.role === "user" ? "你" : "研究助理"}</span><div>${content}</div></article>`;
    })
    .join("");
  log.querySelectorAll("[data-run-proposal]").forEach(
    (button) =>
      (button.onclick = async () => {
        button.disabled = true;
        try {
          const j = await api(`chat/${button.dataset.runProposal}/run`, {});
          studyState.jobs.set(j.id, j);
          studyState.messages = await api("chat");
          renderChat();
          await refreshJobs();
          await fetchChatSummaries();
        } catch (e) {
          notice(e.message, true);
          button.disabled = false;
        }
      }),
  );
  log.querySelectorAll("[data-edit-proposal]").forEach(
    (button) =>
      (button.onclick = () => {
        const index = studyState.messages.findIndex(
          (m) => m.id === Number(button.dataset.editProposal),
        );
        const user = studyState.messages
          .slice(0, index)
          .reverse()
          .find((m) => m.role === "user");
        if (user) {
          $("#chat-input").value = user.content;
          $("#chat-input").focus();
        }
      }),
  );
  log
    .querySelectorAll("[data-open-job]")
    .forEach(
      (button) =>
        (button.onclick = () =>
          openStudyReport(Number(button.dataset.openJob))),
    );
  if (scroll) log.scrollTop = log.scrollHeight;
}

async function openStudyReport(id) {
  try {
    await showJob(id);
    $("#results").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    notice(e.message, true);
  }
}

async function fetchChatSummaries() {
  const pending = [];
  for (const m of studyState.messages) {
    if (
      !m.job_id ||
      studyState.summaries.has(m.job_id) ||
      studyState.loading.has(m.job_id)
    )
      continue;
    const job = studyState.jobs.get(m.job_id);
    if (job && !["done", "failed"].includes(job.status)) continue;
    studyState.loading.add(m.job_id);
    pending.push(
      api(`jobs/${m.job_id}/summary`)
        .then((s) => {
          if (["done", "failed"].includes(s.status))
            studyState.summaries.set(m.job_id, s);
        })
        .finally(() => studyState.loading.delete(m.job_id)),
    );
  }
  if (pending.length) {
    await Promise.all(pending);
    renderChat(false);
  }
}

async function refreshEvidence() {
  const min = $("#rank-min-trades").value,
    demo = $("#rank-show-demo").checked;
  const d = await api(`dashboard?min_trades=${min}&include_demo=${demo}`);
  $("#candidate-cards").innerHTML = d.candidates.length
    ? d.candidates
        .map((c, i) => {
          const m = c.validation;
          return `<article class="card candidate"><div class="candidate-heading"><span class="rank-number">${String(i + 1).padStart(2, "0")}</span><div><h3>${esc(c.symbol)}</h3><small>${c.source === "SYNTHETIC_DEMO" ? "合成示範 · 非市場證據" : "驗證段觀察 · 完整性待稽核"}</small></div><b class="candidate-win">${pct(m.win_rate)}<small>驗證勝率</small></b></div><p class="confidence-note">95%勝率描述區間 ${pct(c.win_rate_interval95?.lower)}–${pct(c.win_rate_interval95?.upper)}（獨立交易假設，非未來保證） · ${window.auditStatusLabel ? window.auditStatusLabel(c.data_audit_status) : c.data_audit_status}</p><div class="candidate-rules">${c.description.map(esc).join("<br>")}</div><div class="candidate-metrics"><span>交易 <b>${m.trades} 筆</b></span><span>淨報酬 <b>${pct(m.total_return)}</b></span><span>最大回撤 <b>${pct(m.max_drawdown)}</b></span></div><p class="muted">驗證期間 ${esc(c.periods.validation.join(" → "))}<br>${c.heldout_test ? `原研究選定策略 · 保留測試段報酬 ${pct(c.heldout_test.total_return)}（未參與本頁排名）` : "原研究未選此條件，沒有額外測試段結果。"}</p><button class="ghost" data-evidence-job="${c.job_id}">查看來源、成本與證據 →</button></article>`;
        })
        .join("")
    : `<div class="card empty"><h3>還沒有符合門檻的研究條件。</h3><p>先匯入資料並完成候選研究。樣本不足或只有合成示範時，不會顯示成高勝率市場策略。</p><a class="subtle-link" href="#conversation">用對話開始研究 →</a></div>`;
  $("#candidate-cards")
    .querySelectorAll("[data-evidence-job]")
    .forEach(
      (b) => (b.onclick = () => openStudyReport(Number(b.dataset.evidenceJob))),
    );
  $("#evidence-scope").textContent =
    `目前引擎最近 ${d.studies_examined} 組研究 · ${d.insufficient_samples} 組條件未達交易筆數門檻 · ${d.studies_with_failed_audits || 0} 組研究因資料檢查問題排除 · 隱藏 ${d.hidden_synthetic_studies} 組合成研究。勝率不等於獲利能力；反覆依結果挑策略會造成選擇偏誤。`;
  $("#research-overview").innerHTML =
    `<div><b>${d.inventory.non_demo_datasets}</b><span>非示範資料版本（來源待稽核）</span></div><div><b>${d.inventory.rows.toLocaleString()}</b><span>本地日線筆數 · 含 ${d.inventory.synthetic_datasets} 組合成資料</span></div><div><b>${(d.counts.queued || 0) + (d.counts.running || 0)}</b><span>等待／執行中的工作</span></div><div><b>${d.counts.done || 0}</b><span>已保存的完成工作</span></div>`;
}

window.studyDataChanged = () => {
  const current = $("#chat-dataset").value;
  $("#chat-dataset").innerHTML =
    '<option value="">使用資料工作台的勾選項目</option>' +
    datasets
      .map(
        (d) =>
          `<option value="${d.id}">${esc(d.symbol)} · #${d.id}${d.source === "SYNTHETIC_DEMO" ? " · 合成示範" : ""}</option>`,
      )
      .join("");
  if ([...$("#chat-dataset").options].some((o) => o.value === current))
    $("#chat-dataset").value = current;
};
window.studyJobsChanged = async (jobs) => {
  const before = [...studyState.jobs.values()]
    .map((j) => `${j.id}:${j.status}`)
    .join(",");
  studyState.jobs = new Map(jobs.map((j) => [j.id, j]));
  const signature = jobs.map((j) => `${j.id}:${j.status}`).join(",");
  if (before !== signature) renderChat(false);
  await fetchChatSummaries();
  if (studyState.completedSignature !== signature) {
    studyState.completedSignature = signature;
    await refreshEvidence();
  }
};

$("#chat-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#chat-input"),
    text = input.value.trim(),
    button = $("#chat-send");
  if (!text) return;
  button.disabled = true;
  try {
    const ids = $("#chat-dataset").value
      ? [Number($("#chat-dataset").value)]
      : [...selected];
    const action_ids = {};
    if ($("#use-actions").checked)
      for (const a of actionBundles)
        if (!action_ids[a.dataset_id]) action_ids[a.dataset_id] = a.id;
    const r = await api("chat", {
      message: text,
      dataset_ids: ids,
      costs: costs(),
      action_ids,
      ...(window.auditInputsFor
        ? window.auditInputsFor(
            datasets.map((d) => d.id),
            action_ids,
          )
        : {}),
      walk_forward: {
        train_bars: Number($("#wf-train").value),
        validation_bars: Number($("#wf-validation").value),
        test_bars: Number($("#wf-test").value),
      },
    });
    studyState.messages.push(r.user, r.assistant);
    if (input.value.trim() === text) {
      input.value = "";
      localStorage.removeItem("stocklab_chat_draft");
    }
    renderChat();
  } catch (e) {
    notice(e.message, true);
  } finally {
    button.disabled = false;
  }
};
$("#chat-input").value = localStorage.getItem("stocklab_chat_draft") || "";
$("#chat-input").addEventListener("input", (e) =>
  localStorage.setItem("stocklab_chat_draft", e.target.value),
);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && innerWidth > 700) {
    e.preventDefault();
    $("#chat-form").requestSubmit();
  }
});
document.querySelectorAll("[data-prompt]").forEach(
  (b) =>
    (b.onclick = () => {
      $("#chat-input").value = b.dataset.prompt;
      $("#chat-input").focus();
    }),
);
$("#rank-min-trades").onchange = () =>
  refreshEvidence().catch((e) => notice(e.message, true));
$("#rank-show-demo").onchange = () =>
  refreshEvidence().catch((e) => notice(e.message, true));

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("#install-app").textContent = "安裝研究室";
});
$("#install-app").onclick = async () => {
  if (installPrompt) {
    await installPrompt.prompt();
    installPrompt = null;
  } else $("#install-help").showModal();
};
$("#close-install-help").onclick = () => $("#install-help").close();
function connection(online) {
  $("#connection-state").textContent = online
    ? "研究主機已連線"
    : "尚未連上研究主機";
  $("#connection-state").classList.toggle("offline", !online);
}
window.addEventListener("offline", () => connection(false));
window.addEventListener("online", () => initResearch());
async function initResearch() {
  try {
    studyState.messages = await api("chat");
    renderChat();
    await refreshEvidence();
    window.studyDataChanged();
    await fetchChatSummaries();
    connection(true);
  } catch (e) {
    connection(false);
    notice(e.message, true);
  }
}
if ("serviceWorker" in navigator)
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
initResearch();
