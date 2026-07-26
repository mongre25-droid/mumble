/* Fixed local bridge loader for Mumble Find. */
(function () {
  "use strict";

  let loading = false;
  let loaded = false;
  let pendingOpen = false;

  window.openSystemSearch = function () {
    pendingOpen = true;
    if (!loaded) { loadFromBridge(); return false; }
    if (typeof window.bootSystemSearch === "function") window.bootSystemSearch();
    if (typeof window.navTo === "function" && document.querySelector('[data-view="system-search"]')) {
      window.navTo("system-search");
      const input = document.querySelector("#ss-input");
      window.setTimeout(() => { if (input) { input.focus(); input.select(); } }, 35);
      pendingOpen = false;
      return true;
    }
    return false;
  };

  function install(assets, status) {
    if (loaded || !assets || assets.ok === false || !assets.css || !assets.js) return false;
    if (status && status.supported === false) return false;
    const style = document.createElement("style");
    style.id = "system-search-styles";
    style.textContent = String(assets.css);
    document.head.appendChild(style);
    const script = document.createElement("script");
    script.id = "system-search-script";
    script.textContent = String(assets.js) + "\n//# sourceURL=mumble-system-search-ui.js";
    document.body.appendChild(script);
    loaded = true;
    if (typeof window.bootSystemSearch === "function") window.bootSystemSearch(status);
    if (pendingOpen) window.setTimeout(window.openSystemSearch, 0);
    return true;
  }

  async function loadFromBridge() {
    if (loaded || loading || !window.pywebview || !window.pywebview.api
        || typeof window.pywebview.api.system_search_assets !== "function") return false;
    loading = true;
    try {
      const status = typeof window.pywebview.api.system_search_status === "function"
        ? await window.pywebview.api.system_search_status() : null;
      if (status && status.supported === false) return false;
      return install(await window.pywebview.api.system_search_assets(), status);
    } catch (error) {
      console.error("Mumble Find bridge failed", error);
      return false;
    } finally { loading = false; }
  }

  async function loadBrowserPreview() {
    if (loaded || window.pywebview) return;
    try {
      const responses = await Promise.all([
        fetch("../experimental/system_search/ui.css"),
        fetch("../experimental/system_search/ui.js"),
      ]);
      if (!responses.every((response) => response.ok)) return;
      install({ ok: true, css: await responses[0].text(), js: await responses[1].text() },
        { ok: true, supported: true, platform: "windows", platform_label: "Windows" });
    } catch (error) { console.error("Mumble Find preview assets failed", error); }
  }

  window.addEventListener("pywebviewready", loadFromBridge);
  loadFromBridge();
  window.setTimeout(loadBrowserPreview, 250);
})();
