# 關機、換電腦與持續運算

建議：**一台固定 Linux 主機持有資料與工作佇列，各電腦只用瀏覽器登入**。
你關掉筆電後，運算在固定主機繼續；不用在每台電腦安裝套件或搬資料。
目前只是雲端開發工作區，尚未取得可部署主機、網域或其存取權，沒有正式線上服務。

## 個人正式部署的組成

- GitHub 私有程式庫保存來源、版本與測試；私人資料與金鑰不進 Git。
- 固定 Linux 主機＋持久磁碟，Python 3.12+，執行 scripts/setup.sh。
- STOCKLAB_HOME 指向持久磁碟。以 systemd 等服務管理程式執行 scripts/start.sh，設定重啟。
- API 維持 loopback，由 HTTPS 反向代理加上個人登入／存取控制；不要直接公開無認證的服務。
- 程式支援 STOCKLAB_API_TOKEN 的單一 Bearer 金鑰驗證，瀏覽器可按「研究室通行碼」輸入。
  這是個人用服務金鑰，不是完整使用者帳號／權限系統。Apps Script 可使用同一受限服務端點。
- 反向代理外部 HTTPS 來源需以 STOCKLAB_PUBLIC_ORIGIN 明確設定，例如 https://你的研究主機網域，
  APP 只額外信任這個設定的 Origin，不信任任意 forwarded headers。正式部署仍需端到端驗證。
- 排程只在主機開機且服務運行時有效；硬碟、主機或雲端任務結束會中止運算。

使用者已選擇常駐雲端版、先確認月費再開通；目前的 Render 設定與費用核對見 [手機部署說明](PHONE_ACCESS.md)。未經明確授權不購買雲端資源。
正式部署的登入、HTTPS、備份復原與主機重啟驗證尚未完成，不能稱為正式可用。

## 換主機或多機運算

關閉服務後搬移整份 `.stocklab/` 可帶走資料、排程、結果與佇列；先保留備份，再於新主機啟動。
執行中的工作會在新的 worker 取得鎖後重新排隊，已完成的商品會沿用 checkpoint。
不要用雲端硬碟、Dropbox、網路共享磁碟直接同步運行中的 SQLite／WAL 檔案。
APP 的每日排程可自動建立本地備份並保留最近 7 份；使用 SQLite online backup API 配合不可變資料檔案。
本地備份不能防主機或磁碟整體遺失；異地備份仍未設定。還原前先停止 APP，並還原到全新目錄：

```bash
.venv/bin/python -m stocklab backup --output /private-backups/stocklab.tar.gz
.venv/bin/python -m stocklab restore --archive /private-backups/stocklab.tar.gz --destination /new-empty-data
```

之後將 STOCKLAB_HOME 指向新目錄，重新啟動。請只使用自己可信的備份。

若要多台電腦同時分攤工作，下一階段應改成中央 PostgreSQL／工作佇列與物件儲存，
各 worker 領取不同工作並回傳結果；**目前只支援單主機單 worker**，不能把目前 SQLite 架構當成分散式系統。

## 和我接續製作

目前檔案在雲端工作區，不在你的筆電。另一台電腦登入同一 ChatGPT 帳號回到原對話可接續討論；
工作區的執行與保留仍受平台生命週期限制。請發布環境保存快照，並將開發成果提交到 GitHub 後再依其版本接續。
自動產生的短 context 和 docs/STATUS.md 能降低跨對話接續成本，但不會替你自動開新聊天。
