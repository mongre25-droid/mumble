/* Main-window entry point for the dedicated Mumble Search launcher. */
(function () {
  "use strict";

  let previewLoaded = false;

  function ensureNav() {
    if (document.querySelector("#ss-nav-button")) return;
    const nav = document.querySelector(".navbar .nav");
    if (!nav) return;
    const settings = nav.querySelector('[data-nav="settings"]');
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-btn";
    button.id = "ss-nav-button";
    button.setAttribute("aria-label", "Open Mumble Search launcher");
    button.innerHTML = '<span data-icon="search" aria-hidden="true"></span>Search';
    nav.insertBefore(button, settings || null);
    button.addEventListener("click", window.openSystemSearch);
    if (typeof window.paintIcons === "function") window.paintIcons(button);
  }

  window.openSystemSearch = async function () {
    if (
      window.pywebview &&
      window.pywebview.api &&
      typeof window.pywebview.api.system_search_show === "function"
    ) {
      return !!(await window.pywebview.api.system_search_show());
    }
    // Browser-only preview keeps the embedded palette available to designers;
    // the installed app always uses the separate native window above.
    if (!previewLoaded) await loadBrowserPreview();
    return typeof window.showSystemSearch === "function"
      ? window.showSystemSearch()
      : false;
  };

  function installPreview(css, javascript) {
    if (previewLoaded) return true;
    const style = document.createElement("style");
    style.id = "system-search-styles";
    style.textContent = String(css || "");
    document.head.appendChild(style);
    const script = document.createElement("script");
    script.id = "system-search-script";
    script.textContent = String(javascript || "") +
      "\n//# sourceURL=mumble-system-search-ui.js";
    document.body.appendChild(script);
    previewLoaded = true;
    if (typeof window.bootSystemSearch === "function") window.bootSystemSearch();
    return true;
  }

  async function loadBrowserPreview() {
    if (previewLoaded || window.pywebview) return false;
    try {
      const responses = await Promise.all([
        fetch("../experimental/system_search/ui.css"),
        fetch("../experimental/system_search/ui.js"),
      ]);
      if (!responses.every((response) => response.ok)) return false;
      return installPreview(
        await responses[0].text(),
        await responses[1].text(),
      );
    } catch (error) {
      console.error("Mumble Search preview assets failed", error);
      return false;
    }
  }

  ensureNav();
  window.addEventListener("DOMContentLoaded", ensureNav);
  window.addEventListener("pywebviewready", ensureNav);
})();
