# 如何真的在手機打開

目前雲端工作區的服務不是公開手機網址。截圖、環境發布及 GitHub 程式碼也都不是 APP 網址。
必須把後端部署到持續運作的主機，取得該主機提供的 HTTPS 網址，手機才能直接開啟。
本輪已準備 Render 的原生 Python 設定；尚未開通任何主機或購買服務。

## 常駐研究版（建議）

使用根目錄 `render.yaml`，設定單一 Web Service、背景 worker、自動產生通行碼與 1GB 持久磁碟。
這是 Render starter 付費方案；**以你開通時平台顯示的服務、磁碟與稅費為準，尚未核對報價或扣款**。
磁碟不等於異地備份，仍應安排 off-host 備份。autoDeploy 關閉，推送程式不會自動發布新版本。

1. 用自己的 GitHub 帳號登入 https://dashboard.render.com/，授權讀取 STOCK 儲存庫。
2. 建立 Blueprint，選擇 STOCK 及手機部署分支 `codex/mobile-access`，使用根目錄 render.yaml。
3. 檢查平台列出的費用、方案、磁碟與服務內容，確認後才建立；這一步由帳號擁有者操作。
4. 部署成功後，平台會提供 HTTPS Web Service URL。這個網址才是手機要開啟的 APP。
5. 在平台的服務環境設定查看自動生成的 STOCKLAB_API_TOKEN，存入你的密碼管理器，勿貼在聊天或 GitHub。
6. 手機開啟服務網址，按「研究室通行碼」輸入它；之後可用瀏覽器「加入主畫面」。

若平台介面未提供 Blueprint 分支選擇，請先確認部署分支／檔案位置，勿改部署成沒有 APP 程式的 main。
這裡提供的是部署準備，不代表已驗證 Render 的實際建置、計費或手機正式部署。

## 免費體驗版

設定檔為 `deploy/render-preview.yaml`，需在建立服務時明確選此檔案／設定。
也可建原生 Python Web Service：build 為 `python -m pip install -r requirements.txt`，start 為 `python -m stocklab.production`。
設定 STOCKLAB_DEMO_MODE=1、STOCKLAB_HOME=/tmp/stocklab-preview、至少32字元隨機 STOCKLAB_API_TOKEN，以及 Python 3.12。
RENDER_EXTERNAL_URL 由平台提供；正式來源會由啟動程式讀取，不需自行買網域。

免費服務可能休眠、儲存可能重設。此模式只用合成資料，禁止匯入私人行情及啟用排程，不能當作正式研究主機。
不要把體驗網址、免費帳務與持久研究版混為一談；先核對平台當下的免費條件。

## 目前仍需要的外部動作

尚沒有已連接的 Render 帳號／主機授權。Codex 不能代替你完成帳號登入、付款或費用確認，
也沒有可直接公開此開發環境連接埠的工具。取得平台帳號授權並成功部署前，沒有可以交付的手機 APP 網址。
切勿使用別台電腦上的 localhost，或把環境設定的「發布」當成網站部署。

## 實際啟動行為

`python -m stocklab.production` 會要求強通行碼、HTTPS 來源與明確資料路徑，綁定主機 PORT。
`/healthz` 只提供健康狀態，不含私人研究資料；API 需要通行碼，手機登入入口始終可見。
通行碼只保存於目前分頁的 sessionStorage；它是個人研究室共用金鑰，不是多人帳號系統。
本輪用隔離本機服務實測登入與錯誤通行碼拒絕，尚未冒稱已有公開可用網址。
