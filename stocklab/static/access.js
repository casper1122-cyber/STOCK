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
          ? "通行碼不正確，請確認部署平台的設定。"
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
    }
    if (info.requires_key && !sessionStorage.getItem("stocklab_token"))
      window.openResearchLogin();
    if (!info.requires_key) {
      document.querySelector("#login-open").textContent = "本機研究室";
      document.querySelector("#login-open").disabled = true;
    }
  })
  .catch(() => {});
