const accessDialog = document.querySelector("#access-dialog");
window.openResearchLogin = () => {
  if (!accessDialog.open) accessDialog.showModal();
};
document.querySelector("#login-open").onclick = window.openResearchLogin;
document.querySelector("#access-cancel").onclick = () => accessDialog.close();
document.querySelector("#access-logout").onclick = () => {
  sessionStorage.removeItem("stocklab_token");
  location.reload();
};
document.querySelector("#backup-download").onclick = async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  const notice = document.querySelector("#notice");
  notice.textContent = "正在建立一致性備份，請等下載完成。";
  try {
    const key = sessionStorage.getItem("stocklab_token");
    const response = await fetch("/api/backup", {
      headers: key ? { Authorization: "Bearer " + key } : {},
      cache: "no-store",
    });
    if (response.status === 401) {
      window.openResearchLogin();
      throw Error("請先登入，再下載研究備份。");
    }
    if (!response.ok) throw Error("備份未完成，請稍後再試；目前資料尚未備份。");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "stocklab-backup.tar.gz";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    notice.textContent = "備份檔已準備下載，請確認已保存到你的裝置。內含行情與研究紀錄，請妥善保管。";
  } catch (error) {
    notice.textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
document.querySelector("#access-form").onsubmit = async (e) => {
  e.preventDefault();
  const key = document.querySelector("#access-key").value;
  const button = e.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const response = await fetch("/api/status", {
      headers: { Authorization: "Bearer " + key },
      cache: "no-store",
    });
    if (!response.ok)
      throw Error(
        response.status === 401
          ? "通行碼不正確，請確認你的研究室通行碼。"
          : "目前無法登入，請稍後再試。",
      );
    sessionStorage.setItem("stocklab_token", key);
    document.querySelector("#access-key").value = "";
    location.reload();
  } catch (e) {
    document.querySelector("#access-error").textContent = e.message;
  } finally {
    button.disabled = false;
  }
};
fetch("/app-info", { cache: "no-store" })
  .then((r) => r.json())
  .then((info) => {
    if (info.demo_mode) {
      const banner = document.querySelector(".banner");
      banner.innerHTML =
        "<strong>免費體驗模式</strong><span>只使用合成資料。服務可能休眠、資料可能重設；不能匯入私人行情或當成正式研究主機。</span>";
    } else if (info.ephemeral_mode) {
      document.querySelector(".banner").innerHTML =
        "<strong>免費暫存模式</strong><span>可匯入行情、驗證股票／ETF 現股日線條件。主機可能休眠，資料可能重設；無人排程停用。離開前請按「下載完整備份」，並保存原始 CSV。完整市場與期貨交易模型尚未完成。</span>";
    }
    if (info.demo_mode || info.ephemeral_mode) {
      document.querySelector(".aside-note p").textContent =
        "目前資料暫存於免費主機，請自行下載保存，不保證永久保留。";
      document.querySelector("#schedule-enabled").disabled = true;
    }
    if (info.requires_key && !sessionStorage.getItem("stocklab_token"))
      window.openResearchLogin();
    if (!info.requires_key) {
      document.querySelector("#login-open").textContent = "本機研究室";
      document.querySelector("#login-open").disabled = true;
    }
  })
  .catch(() => {});
