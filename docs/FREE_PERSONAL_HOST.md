# 用自己的電腦執行，不租主機

這是持久研究的免費主機路線，不呼叫模型 API 或購買雲端資源。
電腦需保持開機、連網、程式運行並避免休眠，仍有自家電力與網路成本。
目前單主機、單 worker；完整行情與期貨模型仍在開發。

## 準備電腦

Linux 使用 Python 3.12+。Windows 使用 WSL Ubuntu，官方安裝方式為以系統管理員執行
`wsl --install -d Ubuntu`，可能需重啟。程式不直接支援原生 Windows Python。
在 Ubuntu 安裝 Git、Python 3.12 與 venv 後，於自己的目錄取得程式：

```bash
git clone --branch codex/mobile-access https://github.com/casper1122-cyber/STOCK.git
cd STOCK
bash scripts/setup.sh
bash scripts/start-personal.sh
```

已經有 STOCK 時使用原目錄，不覆寫自己的改動；私有 GitHub 儲存庫使用安全登入，不把 token 填入網址。
這是使用者電腦的目錄，不是 Codex 的 `/workspace/STOCK`。
啟動腳本只監聽本機 loopback，自動建立本地通行碼（權限 0600），重啟沿用同一通行碼。
在自己的電腦查看 `.stocklab/access-token`，手機登入時使用，勿貼到聊天。
自訂 STOCKLAB_HOME 時，金鑰與資料均在該目錄；金鑰不進備份或 Git。
保留程式視窗，瀏覽器可以關閉。先確認 Windows／Linux 自己的瀏覽器可使用 APP。

## 手機外出時連線

可使用 Tailscale 個人免費方案作私人連線。在自己的電腦與手機安裝官方 Tailscale，
使用同一私人帳號，依當時的免費條件操作，不購買升級。這不是在 Codex 雲端安裝 VPN。
在安裝 Tailscale 的電腦終端執行：

```text
tailscale serve --bg http://127.0.0.1:8000
```

依提示啟用私人 HTTPS，記下顯示的 `https://...ts.net` 網址，不使用公開 Funnel。
若 APP 在 WSL、Tailscale 在 Windows，先確認 Windows 瀏覽器可連到 WSL 的 8000 埠；
這依賴 WSL 的 localhost forwarding，尚未在使用者設備驗證。
停止 APP（Ctrl+C），在 Ubuntu 終端指定實際 HTTPS 網址再啟動：

```bash
export STOCKLAB_PUBLIC_ORIGIN='https://你的電腦名稱.你的私人網域.ts.net'
bash scripts/start-personal.sh
```

網址必須換成 Tailscale 顯示的真實值；換終端後需重新設定或保存在自己私人的啟動設定。
手機開啟 Tailscale，用瀏覽器打開私人網址、輸入研究室通行碼，再加入主畫面。
手機必須保持 Tailscale 連線，電腦與 APP 必須執行。

## 保存與接續

資料與工作預設在 `.stocklab/`。電腦關機或休眠後不會繼續運算。
定期下載完整備份，另存到自己的其他裝置；同磁碟備份不能防電腦損壞。
換電腦先備份、停止服務，再還原至全新目錄；不要同步正在使用的 SQLite 檔案。
Linux 本機流程與 Windows／WSL、私人 HTTPS、手機實機連線是不同驗證，後三者仍需在使用者設備完成。
