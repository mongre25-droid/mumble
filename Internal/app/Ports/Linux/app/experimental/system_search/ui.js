/* Mumble Find — keyboard-first Windows/Linux launcher UI. */
(function () {
  "use strict";

  const SS = {
    status: null,
    query: "",
    category: "all",
    results: [],
    selected: 0,
    request: 0,
    wired: false,
    preview: !window.pywebview,
  };

  const $s = (selector, root) => (root || document).querySelector(selector);
  const $$s = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const escapeHtml = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  function prettyHotkey(value) {
    return String(value || "Ctrl + Alt + S")
      .split("+").map((part) => part.trim()).filter(Boolean)
      .map((part) => ({ ctrl: "Ctrl", alt: "Alt", shift: "Shift", windows: "Win" }[part.toLowerCase()] || part))
      .join(" + ");
  }

  function mockStatus() {
    return { ok: true, supported: true, platform: "windows", platform_label: "Windows",
      refreshing: false, total: 1284, counts: { app: 96, file: 1098, folder: 90 },
      hotkey: "ctrl+alt+s", updated_at: new Date().toISOString(), roots: ["Documents", "Downloads"] };
  }

  function mockSearch(query, category) {
    const sample = [
      { id: "preview-vscode", kind: "app", name: "Visual Studio Code", subtitle: "Start menu", source: "start-menu", favorite: true, actions: ["open", "favorite", "reveal", "copy_path"] },
      { id: "preview-downloads", kind: "folder", name: "Downloads", subtitle: "C:\\Users\\You", source: "files", favorite: false, actions: ["open", "favorite", "copy_path"] },
      { id: "preview-notes", kind: "file", name: "Project notes.md", subtitle: "Documents\\Mumble", source: "files", favorite: false, actions: ["open", "favorite", "reveal", "copy_path"] },
    ];
    const q = String(query || "").toLowerCase();
    let results = sample.filter((item) => (category === "all" || item.kind === category)
      && (!q || (item.name + " " + item.subtitle).toLowerCase().includes(q)));
    if (q && category === "all") results.push({ id: "preview-web", kind: "web", name: `Search the web for “${query}”`, subtitle: "Open with Perplexity", source: "web", favorite: false, actions: ["open"] });
    return { ok: true, results, total_matches: results.length, refreshing: false };
  }

  async function api(name, ...args) {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === "function") {
      return window.pywebview.api[name](...args);
    }
    if (name === "system_search_status") return mockStatus();
    if (name === "system_search_query") return mockSearch(args[0], args[1]);
    if (name === "system_search_refresh") return { ok: true, refreshing: true };
    if (name === "system_search_execute") return { ok: true, action: args[1] || "open" };
    return { ok: false };
  }

  function ensureSurface() {
    if ($s('[data-view="system-search"]')) return;
    const nav = $s(".navbar .nav");
    const settings = $s('[data-nav="settings"]', nav);
    if (!nav) return;
    const button = document.createElement("button");
    button.className = "nav-btn";
    button.dataset.nav = "system-search";
    button.innerHTML = '<span data-icon="search"></span>Mumble Find';
    nav.insertBefore(button, settings || null);
    button.addEventListener("click", enterView);

    const view = document.createElement("div");
    view.className = "view";
    view.dataset.view = "system-search";
    view.hidden = true;
    view.innerHTML = `
      <div class="ss-shell">
        <section class="ss-hero">
          <div class="ss-kicker">
            <span class="badge">Experimental</span>
            <span class="badge">Windows + Linux exclusive</span>
            <span class="t-mute fs10">Local index · no cloud</span>
          </div>
          <h1 class="ss-title">Find apps &amp; files</h1>
          <p class="ss-lede">Search installed apps, documents, downloads and folders from one keyboard-first launcher. Mumble learns what you open most, while every query and path stays on this device.</p>
        </section>
        <section class="ss-launcher" aria-label="Mumble Find launcher">
          <div class="ss-search-row">
            <span class="ss-search-icon" data-icon="search"></span>
            <input id="ss-input" class="ss-input selectable" type="search" autocomplete="off" spellcheck="false" aria-label="Find apps &amp; files" placeholder="Find apps &amp; files" />
            <span class="kbd ss-shortcut" id="ss-hotkey">Ctrl + Alt + S</span>
          </div>
          <div class="ss-filters" role="tablist" aria-label="Search categories">
            <button class="ss-filter active" data-ss-filter="all" role="tab" aria-selected="true">Everything</button>
            <button class="ss-filter" data-ss-filter="app" role="tab" aria-selected="false">Apps</button>
            <button class="ss-filter" data-ss-filter="file" role="tab" aria-selected="false">Files</button>
            <button class="ss-filter" data-ss-filter="folder" role="tab" aria-selected="false">Folders</button>
            <span class="ss-index-state" id="ss-index-state">Preparing local index…</span>
          </div>
        </section>
        <section class="card ss-results-card" aria-live="polite">
          <div class="ss-results-head">
            <strong id="ss-results-title">Recent &amp; favourite</strong>
            <span id="ss-results-count"></span>
            <button class="btn btn-icon btn-ghost ss-refresh" id="ss-refresh" title="Refresh local search index" aria-label="Refresh local search index"><span data-icon="refresh"></span></button>
          </div>
          <div class="ss-results" id="ss-results" role="listbox" aria-label="Search results"></div>
          <div class="ss-foot"><span><span class="kbd">↑↓</span> navigate</span><span><span class="kbd">Enter</span> open</span><span><span class="kbd">Ctrl Enter</span> reveal</span><span><span class="kbd">Esc</span> clear</span></div>
        </section>
      </div>`;
    const main = $s("#main-content") || document.body;
    main.insertBefore(view, $s('[data-view="settings"]', main) || null);
    if (typeof window.paintIcons === "function") window.paintIcons(view);
    else if (typeof paintIcons === "function") paintIcons(view);
    wire();
  }

  function wire() {
    if (SS.wired) return;
    SS.wired = true;
    const input = $s("#ss-input");
    let timer = null;
    input.addEventListener("input", () => {
      SS.query = input.value || "";
      clearTimeout(timer);
      timer = setTimeout(runSearch, 75);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); moveSelection(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); moveSelection(-1); }
      else if (event.key === "Enter") {
        event.preventDefault();
        executeSelected(event.ctrlKey ? "reveal" : "open");
      } else if (event.key === "Escape") {
        event.preventDefault();
        if (input.value) { input.value = ""; SS.query = ""; runSearch(); }
        else if (typeof window.navTo === "function") window.navTo("home");
      }
    });
    $$s("[data-ss-filter]").forEach((button) => button.addEventListener("click", () => {
      SS.category = button.dataset.ssFilter || "all";
      $$s("[data-ss-filter]").forEach((other) => {
        const active = other === button;
        other.classList.toggle("active", active);
        other.setAttribute("aria-selected", active ? "true" : "false");
      });
      runSearch();
      input.focus();
    }));
    $s("#ss-refresh").addEventListener("click", refreshIndex);
  }

  function enterView() {
    ensureSurface();
    if (typeof window.navTo === "function") window.navTo("system-search");
    window.setTimeout(() => { const input = $s("#ss-input"); if (input) { input.focus(); input.select(); } }, 40);
    refreshStatus();
    runSearch();
  }

  async function refreshStatus() {
    try {
      SS.status = await api("system_search_status");
      if (!SS.status || !SS.status.supported) return;
      const counts = SS.status.counts || {};
      const count = Number(SS.status.total || 0).toLocaleString();
      const state = $s("#ss-index-state");
      if (state) state.textContent = SS.status.refreshing
        ? `Indexing ${SS.status.platform_label || "this computer"}…`
        : count ? `${count} local items` : "Local index is ready";
      const hotkey = $s("#ss-hotkey");
      if (hotkey) hotkey.textContent = prettyHotkey(SS.status.hotkey);
      const refresh = $s("#ss-refresh");
      if (refresh) refresh.classList.toggle("spinning", !!SS.status.refreshing);
      if (counts.app || counts.file || counts.folder) {
        const parts = [];
        if (counts.app) parts.push(`${counts.app} apps`);
        if (counts.file) parts.push(`${counts.file} files`);
        if (counts.folder) parts.push(`${counts.folder} folders`);
        if (state && !SS.status.refreshing) state.title = parts.join(" · ");
      }
    } catch (error) {
      const state = $s("#ss-index-state");
      if (state) state.textContent = "Local index unavailable";
    }
  }

  async function refreshIndex() {
    const button = $s("#ss-refresh");
    if (button) button.classList.add("spinning");
    const result = await api("system_search_refresh");
    if (result && result.ok === false && typeof window.toast === "function") window.toast(result.message || "The index could not refresh", "err");
    refreshStatus();
    window.setTimeout(() => { refreshStatus(); runSearch(); }, 900);
  }

  async function runSearch() {
    const request = ++SS.request;
    const result = await api("system_search_query", SS.query, SS.category, 50);
    if (request !== SS.request) return;
    SS.results = (result && result.results) || [];
    SS.selected = Math.min(SS.selected, Math.max(0, SS.results.length - 1));
    renderResults(result || {});
    if (result && result.refreshing) window.setTimeout(refreshStatus, 500);
  }

  function iconFor(kind) {
    return ({ app: "monitor", file: "file", folder: "folder", web: "search" }[kind] || "search");
  }

  function renderResults(result) {
    const host = $s("#ss-results");
    if (!host) return;
    $s("#ss-results-title").textContent = SS.query ? "Best matches" : "Recent & favourite";
    $s("#ss-results-count").textContent = SS.results.length ? `${result.total_matches || SS.results.length} found` : "";
    if (!SS.results.length) {
      host.innerHTML = `<div class="ss-empty"><div><span class="ss-empty-icon" data-icon="search"></span><strong>${SS.query ? "No local matches" : "Your index is warming up"}</strong><p>${SS.query ? "Try fewer words, switch category, or refresh the local index." : "Mumble is finding apps and files in the background. Start typing while it works."}</p></div></div>`;
      if (typeof paintIcons === "function") paintIcons(host);
      return;
    }
    host.innerHTML = SS.results.map((item, index) => {
      const actions = item.actions || ["open"];
      const reveal = actions.includes("reveal") ? `<button class="btn-icon btn-ghost" data-ss-action="reveal" title="Show in folder" aria-label="Show ${escapeHtml(item.name)} in folder"><span data-icon="folder"></span></button>` : "";
      const copy = actions.includes("copy_path") ? `<button class="btn-icon btn-ghost" data-ss-action="copy_path" title="Copy path" aria-label="Copy path for ${escapeHtml(item.name)}"><span data-icon="copy"></span></button>` : "";
      const favorite = actions.includes("favorite") ? `<button class="btn-icon btn-ghost ${item.favorite ? "on" : ""}" data-ss-action="favorite" title="${item.favorite ? "Remove from favourites" : "Add to favourites"}" aria-label="${item.favorite ? "Remove" : "Add"} ${escapeHtml(item.name)} ${item.favorite ? "from" : "to"} favourites"><span data-icon="star"></span></button>` : "";
      return `<div class="ss-result ${index === SS.selected ? "selected" : ""}" data-index="${index}" data-kind="${escapeHtml(item.kind)}" role="option" aria-selected="${index === SS.selected ? "true" : "false"}">
        <button class="ss-result-main" data-ss-action="open" tabindex="${index === SS.selected ? "0" : "-1"}">
          <span class="ss-kind-icon" data-icon="${iconFor(item.kind)}"></span>
          <span class="ss-copy"><span class="ss-name"><span>${escapeHtml(item.name)}</span>${item.favorite ? '<span class="ss-fav-mark" data-icon="star"></span>' : ""}<span class="ss-type">${escapeHtml(item.kind)}</span></span><span class="ss-meta">${escapeHtml(item.subtitle || item.source || "Local")}</span></span>
        </button><span class="ss-result-actions">${favorite}${reveal}${copy}</span></div>`;
    }).join("");
    $$s(".ss-result", host).forEach((row) => {
      row.addEventListener("mouseenter", () => selectIndex(Number(row.dataset.index)));
      $$s("[data-ss-action]", row).forEach((button) => button.addEventListener("click", (event) => {
        event.stopPropagation();
        selectIndex(Number(row.dataset.index));
        executeSelected(button.dataset.ssAction || "open");
      }));
    });
    if (typeof paintIcons === "function") paintIcons(host);
  }

  function selectIndex(index) {
    if (!SS.results.length) return;
    SS.selected = Math.max(0, Math.min(index, SS.results.length - 1));
    $$s(".ss-result").forEach((row, rowIndex) => {
      const selected = rowIndex === SS.selected;
      row.classList.toggle("selected", selected);
      row.setAttribute("aria-selected", selected ? "true" : "false");
      const primary = $s(".ss-result-main", row);
      if (primary) primary.tabIndex = selected ? 0 : -1;
    });
  }

  function moveSelection(delta) {
    if (!SS.results.length) return;
    selectIndex((SS.selected + delta + SS.results.length) % SS.results.length);
    $s(`.ss-result[data-index="${SS.selected}"]`)?.scrollIntoView({ block: "nearest" });
  }

  async function executeSelected(action) {
    const item = SS.results[SS.selected];
    if (!item) return;
    if (action === "reveal" && !(item.actions || []).includes("reveal")) action = "open";
    const result = await api("system_search_execute", item.id, action);
    if (!result || result.ok === false) {
      if (typeof window.toast === "function") window.toast((result && result.message) || "That item could not be opened", "err", 3000);
      return;
    }
    if (action === "favorite") {
      item.favorite = result.favorite !== false;
      renderResults({ total_matches: SS.results.length });
    } else if (action === "copy_path" && typeof window.toast === "function") {
      window.toast("Path copied", "ok", 1200);
    }
  }

  window.bootSystemSearch = function (status) {
    SS.status = status || mockStatus();
    if (!SS.status.supported) return false;
    ensureSurface();
    refreshStatus();
    runSearch();
    return true;
  };
  window.openSystemSearch = enterView;
  window.__mumbleSystemSearchTest = { mockSearch, renderResults, iconFor };
})();
