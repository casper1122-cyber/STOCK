// Optional Google Sheets entry point. Never store a key in source code.
// Script properties: STOCKLAB_URL (reachable HTTPS URL), STOCKLAB_TOKEN,
// STOCKLAB_DATASET_IDS (JSON array, e.g. [1,2]). This does not host Python.
function onOpen() {
  SpreadsheetApp.getUi().createMenu('台股研究室')
    .addItem('提交候選研究', 'submitResearch')
    .addItem('提交資料更新與研究', 'submitDaily')
    .addItem('更新工作摘要', 'refreshSummary').addToUi();
}
function labRequest_(path, body) {
  const p = PropertiesService.getScriptProperties();
  const base = (p.getProperty('STOCKLAB_URL') || '').replace(/\/$/, '');
  const token = p.getProperty('STOCKLAB_TOKEN');
  if (!/^https:\/\//.test(base) || !token) throw new Error('請先在指令碼屬性設定 HTTPS 主機及存取金鑰');
  const options = {method: body ? 'post' : 'get', headers: {Authorization: 'Bearer ' + token}, muteHttpExceptions: true};
  if (body) { options.contentType = 'application/json'; options.payload = JSON.stringify(body); }
  const r = UrlFetchApp.fetch(base + '/api/' + path, options);
  if (r.getResponseCode() !== 200) throw new Error('研究主機回應 ' + r.getResponseCode() + '；請檢查主機、金鑰與請求');
  return JSON.parse(r.getContentText());
}
function submitResearch() { submit_('research'); }
function submitDaily() { submit_('daily'); }
function submit_(kind) {
  const p = PropertiesService.getScriptProperties();
  const ids = JSON.parse(p.getProperty('STOCKLAB_DATASET_IDS') || '[]');
  if (!Array.isArray(ids) || !ids.length) throw new Error('請先設定資料版本 ID');
  const job = labRequest_('jobs', {kind: kind, dataset_ids: ids});
  p.setProperty('STOCKLAB_LAST_JOB', String(job.id));
  SpreadsheetApp.getActive().toast('工作 #' + job.id + ' 已提交；運算在研究主機執行');
}
function refreshSummary() {
  const id = PropertiesService.getScriptProperties().getProperty('STOCKLAB_LAST_JOB');
  if (!id) throw new Error('尚未提交工作');
  const summary = labRequest_('jobs/' + id + '/summary');
  const spreadsheet = SpreadsheetApp.getActive();
  const sheet = spreadsheet.getSheetByName('研究摘要') || spreadsheet.insertSheet('研究摘要');
  // Only replace this integration's sheet. Do not send full price histories to Sheets or an LLM.
  sheet.clearContents();
  const rows = [['工作', summary.id, '狀態', summary.status],
    ['注意', summary.note, '', ''], ['商品', '範圍', '淨報酬', '最大回撤']];
  (summary.outputs || []).forEach(x => rows.push([x.symbol, x.scope,
    x.metrics ? x.metrics.total_return : '', x.metrics ? x.metrics.max_drawdown : '']));
  sheet.getRange(1, 1, rows.length, 4).setValues(rows);
}
