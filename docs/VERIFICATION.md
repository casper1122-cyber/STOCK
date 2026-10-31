# 本輪驗證證據

以下是當前工作區的實測，不表示已部署正式主機或已驗證市場獲利能力。

- v0.1 Python 3.12：35 項測試通過，涵蓋成交時點、費稅／滑價／最低費用、停損跳空、整股預算、
  未來資料不改變歷史指標、測試段不參與選擇、資料品質、指紋、API、排程去重、重啟、快取與備份還原。
- 測試用戶端有 1 項 Starlette/httpx 棄用提示，測試仍全部通過；未隱藏警告。
- Python 套件相依檢查通過，安裝腳本重跑成功。
- 真實 HTTP：兩組合成資料的自訂回測、候選搜尋、關聯工作完成，成功／失敗狀態已核對。
- 停止本輪啟動的服務，先加入排隊工作，再啟動：該工作由 queued 轉為 done，兩組資料均完成。
- 線上 SQLite 備份與新目錄還原成功；備份、快取及工作資料不進 Git。
- 真實 Chromium：選取資料、提交回測、工作完成自動顯示兩張曲線；自訂數值欄位切換正確，無 page error。
- 手機視窗 390 px，文件寬度 390 px；桌面與手機畫面已截圖。
- JS／Apps Script 語法檢查及 Git 格式檢查通過。Google Apps Script 尚未在 Google 服務實際執行。

本地驗證紀錄在 `.stocklab/verification/`：http_smoke.json、restart-job.json、browser.json、desktop.png、mobile.png。
這些包含合成資料研究記錄，**不是 2330、0050 或任何市場商品的研究結果**。
可選的瀏覽器重跑命令為 `node scripts/browser_smoke.cjs`，需要既有 Playwright 與系統 Chromium；
不影響純 Python 的安裝／啟動。預設腳本使用明確的 DEMO_STOCK／DEMO_ETF 資料。

FinMind 真實請求收到代理 `403 Forbidden`，當前允許清單尚未套用新增網域。
網路設定、install_script、start_skill 已確認存入環境草稿；尚未發布新環境版本或驗證新任務還原。
沒有新資料源授權、正式主機或 Google 帳號連接，不能宣稱全市場完整性、跨電腦正式服務或全天候排程已完成。

## v0.2 實測

- 目前共 59 項測試通過，仍有 1 項測試用戶端棄用提示；未隱藏警告。
- 新增股利、拆併股、付款日、權利歸屬、不可提前花應收、含費稅拆股逐筆對帳、零碎股拒絕、價格精度保存及事件指紋測試。
- 新增不重疊滾動測試、當前／未來測試資料不影響選樣、無合格策略保留現金、設定快取與舊版 checkpoint 拒絕測試。
- 實際 Chromium 經由網頁匯入兩筆合成事件，1000 元權益對帳正確，原始進場價 100、拆股後出場股數 20。
- 兩組合成資料的滾動工作完成，無 page error；390 px 手機畫面文件寬度 390 px。
- 新版備份／還原包括公司行動資料，還原後再次核對事件指紋。
- 證據：`.stocklab/verification/advanced-browser.json`、advanced-desktop.png、advanced-mobile.png。
- 可重複流程：明確執行 CLI demo 建立合成資料，啟動 APP，再執行 `node scripts/advanced_smoke.cjs`。
- Google Apps Script 仍只有語法驗證；真實資料、完整日曆／母體與正式部署仍未驗證。

## v0.3 實測

- 共 77 項測試通過，含中文條件／成本解析、未支援限制不被忽略、明確確認才入列、追加修改與歷史恢復。
- 突破／跌破的 crossing 判斷及次日開盤成交有手算案例，已在引擎升版中隔離舊快取。
- 候選排序驗證不使用測試段績效，訓練及驗證筆數門檻、同證據去重、合成及舊版結果排除有測試。
- 真實 Chromium：工作 queued 時關閉頁面，新頁回到同一主機後工作 done，對話與摘要可恢復。
- 後續「停損改成3%」保留前一組商品、本金與條件，仍需確認才執行。
- PWA 應用殼可離線載入並顯示未連線狀態；檢查 service worker Cache Storage 沒有研究／聊天 API。
- 390px 手機文件寬度 390px，無 page error；桌面及手機截圖在 `.stocklab/verification/app-*.png`。
- 機器可重複驗證：`node scripts/chat_smoke.cjs`，需已啟動 APP、明確的 CLI demo 與既有 Chromium／Playwright。
- `.stocklab/verification/chat-browser.json` 保存本輪證據，僅含合成資料，非真實股票研究。
- 關閉 APP 頁面不中斷 worker 的結果只在研究服務持續運行時成立，不是 Codex 任務永久執行保證。
- 手機正式 HTTPS 網址、加入主畫面實機操作、真實行情及完整資料稽核仍待部署與存取設定。

## v0.4 實測

- 最終完整套件 101 項通過，仍有 1 項已知測試用戶端棄用提示，未隱藏警告。
- 新增日曆逐日完整性、開市缺價、休市多價、範圍不足、缺日曆不算通過、價格／事件／報告綁定及指紋測試。
- 測試重新檢查舊問題時不會默默改用另一份成功報告；嚴格模式執行前再核對目前已知問題。
- 目前發現問題的資料不會進入首頁條件排行，歷史快照仍保留。
- 手算 Wilson 勝率區間、平均盈虧、Profit factor、期望值、連虧、未恢復回撤及無交易／無虧損邊界。
- 資料庫連線關閉與例外回滾、舊 schema 遷移、205 組資料檢查清單不截斷皆有測試。
- 真實 Chromium 透過網頁匯入錯誤日曆，確認嚴格聊天被擋；修正日曆後綁定事件／檢查版本，1000 元合成帳務仍對得上。
- 390px 手機文件寬度 390px，無 page error。證據為 `.stocklab/verification/quality-browser.json` 及 data-quality.png、risk-report.png。
- 可選重跑：`node scripts/quality_smoke.cjs`，先用明確 CLI demo 建立合成 fixture 並啟動 APP。
- 所有資料來源仍未獨立認證；日曆格式匹配及軟體測試不是完整金融資料驗收。
- 實際線上備份／新目錄還原後，重新讀取選定報告、日曆指紋與聊天；選定狀態及資料均保留。

## 手機部署入口實測

- 112 項測試通過，含強通行碼、HTTPS 來源、平台埠號、持久資料路徑及免費預覽的資料限制。
- 隔離本機 production 入口：以臨時生成且未印出的通行碼、獨立資料路徑啟動，再以 390px Chromium 驗證登入。
- 錯誤通行碼拒絕、正確登入後取得 3 組合成資料、體驗警示可見、文件寬度 390px，無 page error。
- 測試服務已停止，沒有建立公開主機；證據在 `.stocklab/verification/mobile-access.json`、mobile-login.png、mobile-host-preview.png。
- Render 原生 Python 範例設定已由官方 render-examples/fastapi 的 render.yaml 核對；本專案尚未實際在 Render 建置或產生網址。
- GitHub 原生 Git 實際推送 `codex/mobile-access` 成功；API GraphQL 存取遭 Forbidden，不以此推論缺少 Git token。
- 只保存程式與文件；資料／結果目錄及機密不在提交中。原始碼在 GitHub 不等於已取得 APP 網址。
