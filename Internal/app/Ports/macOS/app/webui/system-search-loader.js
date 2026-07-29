/* Main-window entry point for the dedicated Mumble Find launcher. */
(function () {
  "use strict";

  let previewLoaded = false;

  window.openSystemSearch = async function () {
    if (
      window.pywebview &&
      window.pywebview.api &&
      typeof window.pywebview.api.system_search_show === "function"
    ) {
      try {
        const result = await window.pywebview.api.system_search_show();
        if (!result || typeof result !== "object" || result.ok !== true) {
          const message = result && typeof result.message === "string"
            ? result.message
            : "Mumble Find could not open.";
          if (typeof window.toast === "function") window.toast(message, "err", 3000);
          else console.error(message);
          return false;
        }
        return true;
      } catch (error) {
        const message = error && error.message
          ? `Mumble Find could not open: ${error.message}`
          : "Mumble Find could not open.";
        if (typeof window.toast === "function") window.toast(message, "err", 3000);
        else console.error(message);
        return false;
      }
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
      console.error("Mumble Find preview assets failed", error);
      return false;
    }
  }

  // Mumble Find is a momentary command, not a seventh destination. Existing
  // Home/Settings actions and the global shortcut call openSystemSearch.
  document.querySelectorAll("[data-open-system-search]").forEach((button) => {
    button.addEventListener("click", window.openSystemSearch);
  });
})();
