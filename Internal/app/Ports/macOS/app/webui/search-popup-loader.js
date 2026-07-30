/* Fixed bridge loader for the standalone Mumble Find window. */
(function () {
  "use strict";

  let loading = false;
  let loaded = false;

  function install(assets) {
    if (loaded || !assets || assets.ok === false || !assets.css || !assets.js)
      return false;
    const style = document.createElement("style");
    style.id = "system-search-styles";
    style.textContent = String(assets.css);
    document.head.appendChild(style);
    const script = document.createElement("script");
    script.id = "system-search-script";
    script.textContent = String(assets.js) +
      "\n//# sourceURL=mumble-system-search-ui.js";
    document.body.appendChild(script);
    loaded = true;
    if (typeof window.bootSystemSearch === "function") {
      window.bootSystemSearch();
      window.showSystemSearch();
    }
    return true;
  }

  async function load() {
    if (loaded || loading) return loaded;
    if (!window.pywebview || !window.pywebview.api ||
        typeof window.pywebview.api.system_search_assets !== "function")
      return false;
    loading = true;
    try {
      return install(await window.pywebview.api.system_search_assets());
    } catch (error) {
      console.error("Mumble Find assets failed", error);
      return false;
    } finally {
      loading = false;
    }
  }

  window.addEventListener("pywebviewready", load);
  load();
})();
