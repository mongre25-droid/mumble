/* Mumble Find — calm, centred, keyboard-first local launcher UI. */
(function () {
  "use strict";

  const SS = {
    status: null,
    previewStatus: null,
    query: "",
    category: "all",
    results: [],
    selected: 0,
    request: 0,
    iconRequest: 0,
    iconVersion: "",
    wired: false,
    open: false,
    previousFocus: null,
    statusTimer: null,
    actionBusy: false,
  };
  const standalone = document.body.classList.contains("search-popup-page");

  const $s = (selector, root) => (root || document).querySelector(selector);
  const $$s = (selector, root) =>
    Array.from((root || document).querySelectorAll(selector));
  const escapeHtml = (value) =>
    String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function prettyHotkey(value) {
    return String(value || "Ctrl + Alt + F")
      .split("+")
      .map((part) => part.trim())
      .filter(Boolean)
      .map(
        (part) =>
          ({
            ctrl: "Ctrl",
            control: "Ctrl",
            alt: "Alt",
            shift: "Shift",
            windows: "Win",
            win: "Win",
          })[part.toLowerCase()] || part,
      )
      .join(" + ");
  }

  function mockStatus() {
    return (
      SS.previewStatus || {
        ok: true,
        supported: true,
        platform: "windows",
        platform_label: "Windows",
        refreshing: false,
        total: 1284,
        counts: { app: 96, file: 1098, folder: 90 },
        hotkey: "ctrl+alt+f",
        updated_at: new Date().toISOString(),
        roots: ["Documents", "Downloads"],
        file_provider: { available: true, state: "ready", name: "Windows Search", message: "" },
        icon_version: "1:preview",
      }
    );
  }

  function mockSearch(query, category, generation) {
    const sample = [
      {
        id: "preview-vscode",
        kind: "app",
        name: "Visual Studio Code",
        subtitle: "Application",
        source: "start-menu",
        favorite: true,
        actions: ["open", "favorite", "reveal", "copy_path"],
      },
      {
        id: "preview-downloads",
        kind: "folder",
        name: "Downloads",
        subtitle: "Folder",
        source: "files",
        favorite: false,
        actions: ["open", "favorite", "copy_path"],
      },
      {
        id: "preview-notes",
        kind: "file",
        name: "Project notes.md",
        subtitle: "Markdown document",
        source: "files",
        favorite: false,
        actions: ["open", "favorite", "reveal", "copy_path"],
      },
    ];
    const q = String(query || "").toLowerCase();
    const results = sample.filter(
      (item) =>
        (category === "all" || item.kind === category) &&
        (!q || (item.name + " " + item.subtitle).toLowerCase().includes(q)),
    );
    return {
      ok: true,
      results: results.slice(0, 12),
      total_matches: results.length,
      refreshing: false,
      generation: Number(generation || 1),
      provider_state: "complete",
      message: "",
      icon_version: "1:preview",
    };
  }

  async function api(name, ...args) {
    if (
      window.pywebview &&
      window.pywebview.api &&
      typeof window.pywebview.api[name] === "function"
    )
      return window.pywebview.api[name](...args);
    if (name === "system_search_status") return mockStatus();
    if (name === "system_search_query") return mockSearch(args[0], args[1], args[3]);
    if (name === "system_search_cancel") return true;
    if (name === "system_search_refresh")
      return { ok: true, refreshing: true };
    if (name === "system_search_execute")
      return { ok: true, action: args[1] || "open", favorite: true };
    if (name === "system_search_icons") return { ok: true, icons: {} };
    if (name === "system_search_hide" || name === "system_search_show" ||
        name === "system_search_toggle")
      return { ok: true, state: name.endsWith("hide") ? "hidden" : "visible" };
    return { ok: false, message: "Mumble Find is unavailable." };
  }

  function paint(root) {
    const fn = window.paintIcons ||
      (typeof paintIcons === "function" ? paintIcons : null);
    if (fn) {
      fn(root);
      return;
    }
    const glyphs = {
      search: "⌕", shield: "◆", x: "×", refresh: "↻", monitor: "◫",
      file: "▤", folder: "▰", star: "★", copy: "⧉", alert: "!",
    };
    $$s("[data-icon]", root || document).forEach((node) => {
      if (node.dataset.fallbackPainted) return;
      node.dataset.fallbackPainted = "true";
      node.textContent = glyphs[node.dataset.icon] || "•";
    });
  }

  function ensureSurface() {
    if ($s("#ss-overlay")) return;

    const overlay = document.createElement("div");
    overlay.id = "ss-overlay";
    overlay.className = "ss-overlay";
    overlay.hidden = true;
    overlay.innerHTML = `
      <div class="ss-backdrop" data-ss-dismiss></div>
      <section class="ss-dialog" id="ss-dialog" role="dialog" aria-modal="true" aria-labelledby="ss-title" aria-describedby="ss-privacy">
        <header class="ss-header pywebview-drag-region" aria-label="Move Mumble Find window" title="Drag to move Mumble Find">
          <div class="ss-heading">
            <span class="ss-mark" aria-hidden="true"><img src="mumble.png" alt="" /></span>
            <div><span class="ss-kicker">MUMBLE FIND <i></i> LOCAL</span><h2 id="ss-title">Find apps & files</h2><p id="ss-privacy"><span data-icon="shield" aria-hidden="true"></span>Private on this device</p></div>
          </div>
          <button type="button" class="btn btn-icon btn-ghost ss-close" id="ss-close" aria-label="Close Mumble Find"><span data-icon="x" aria-hidden="true"></span></button>
        </header>
        <div class="ss-search-row">
          <span class="ss-search-icon" data-icon="search" aria-hidden="true"></span>
          <label class="sr-only" for="ss-input">Find apps & files</label>
          <input id="ss-input" class="ss-input selectable" type="search" autocomplete="off" spellcheck="false" aria-controls="ss-results" aria-describedby="ss-announcer" placeholder="Find apps & files" />
          <span class="kbd ss-shortcut" id="ss-hotkey">Ctrl + Alt + F</span>
        </div>
        <div class="ss-toolbar">
          <div class="ss-filters" role="group" aria-label="Search category">
            <button type="button" class="ss-filter active" data-ss-filter="all" aria-pressed="true">Everything</button>
            <button type="button" class="ss-filter" data-ss-filter="app" aria-pressed="false">Apps</button>
            <button type="button" class="ss-filter" data-ss-filter="file" aria-pressed="false">Files</button>
            <button type="button" class="ss-filter" data-ss-filter="folder" aria-pressed="false">Folders</button>
          </div>
          <span class="ss-index-state" id="ss-index-state">Preparing Mumble Find…</span>
          <button type="button" class="btn btn-icon btn-ghost ss-refresh" id="ss-refresh" title="Refresh local search index" aria-label="Refresh local search index"><span data-icon="refresh" aria-hidden="true"></span></button>
        </div>
        <div class="ss-results-head">
          <strong id="ss-results-title">Recent and favourites</strong>
          <span id="ss-results-count"></span>
        </div>
        <div class="ss-results" id="ss-results" role="list" aria-labelledby="ss-results-title" aria-busy="false"></div>
        <p class="sr-only" id="ss-announcer" aria-live="polite" aria-atomic="true"></p>
        <footer class="ss-foot" aria-hidden="true"><span><span class="kbd">↑↓</span> move</span><span><span class="kbd">Enter</span> open</span><span><span class="kbd">Ctrl Enter</span> show in folder</span><span><span class="kbd">Esc</span> clear / close</span></footer>
      </section>`;
    document.body.appendChild(overlay);
    paint(overlay);
    wire();
  }

  function wire() {
    if (SS.wired) return;
    SS.wired = true;
    const overlay = $s("#ss-overlay");
    const input = $s("#ss-input");
    let timer = null;
    input.addEventListener("input", () => {
      SS.query = input.value || "";
      SS.selected = 0;
      clearTimeout(timer);
      timer = setTimeout(runSearch, 75);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveSelection(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSelection(-1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        executeSelected(event.ctrlKey ? "reveal" : "open");
      }
    });
    $$s("[data-ss-filter]", overlay).forEach((button) =>
      button.addEventListener("click", () => {
        SS.category = button.dataset.ssFilter || "all";
        SS.selected = 0;
        $$s("[data-ss-filter]", overlay).forEach((other) => {
          const active = other === button;
          other.classList.toggle("active", active);
          other.setAttribute("aria-pressed", active ? "true" : "false");
        });
        runSearch();
        input.focus();
      }),
    );
    $s("#ss-refresh").addEventListener("click", async () => {
      await refreshIndex();
      input.focus();
    });
    $s("#ss-close").addEventListener("click", () => closeSearch(true));
    $s("[data-ss-dismiss]", overlay).addEventListener("click", () =>
      closeSearch(true),
    );
    overlay.addEventListener("keydown", handleDialogKeydown);
  }

  function handleDialogKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      const input = $s("#ss-input");
      if (input && input.value) {
        input.value = "";
        SS.query = "";
        SS.selected = 0;
        runSearch();
        input.focus();
      } else closeSearch(true);
      return;
    }
    if (event.key !== "Tab") return;
    const dialog = $s("#ss-dialog");
    const focusable = $$s(
      'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      dialog,
    ).filter((element) => !element.hidden && element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function showSearch() {
    ensureSurface();
    const overlay = $s("#ss-overlay");
    const input = $s("#ss-input");
    if (!SS.open) {
      SS.previousFocus = document.activeElement;
      SS.open = true;
      const app = $s("#app");
      if (app) {
        app.setAttribute("aria-hidden", "true");
        app.inert = true;
      }
      overlay.hidden = false;
      requestAnimationFrame(() => overlay.classList.add("open"));
    }
    // Run on every show, including the second boot event when pywebview's
    // bridge becomes ready after DOMContentLoaded. This replaces preview data
    // with the real local index instead of leaving the launcher in mock state.
    refreshStatus().then(runSearch);
    window.setTimeout(() => {
      input.focus();
      input.select();
    }, 35);
    return true;
  }

  async function closeSearch(restoreFocus) {
    if (!SS.open) return true;
    if (standalone) {
      let result = null;
      try {
        result = await api("system_search_hide");
      } catch (error) {
        result = { ok: false, state: "visible", message: "Mumble Find could not close its window." };
      }
      if (!result || (result.ok === false && result.state !== "hidden")) {
        const message = (result && result.message) ||
          "Mumble Find could not close its window.";
        announce(message);
        if (typeof window.toast === "function")
          window.toast(message, "err", 3000);
        return false;
      }
    }
    SS.open = false;
    clearTimeout(SS.statusTimer);
    const overlay = $s("#ss-overlay");
    overlay.classList.remove("open");
    overlay.hidden = true;
    const app = $s("#app");
    if (app) {
      app.inert = false;
      app.removeAttribute("aria-hidden");
    }
    if (typeof window.syncDeckWindow === "function") window.syncDeckWindow();
    if (
      restoreFocus &&
      SS.previousFocus &&
      document.documentElement.contains(SS.previousFocus)
    )
      SS.previousFocus.focus();
    SS.previousFocus = null;
    return true;
  }

  function announce(message) {
    const host = $s("#ss-announcer");
    if (host) host.textContent = String(message || "");
  }

  async function refreshStatus() {
    clearTimeout(SS.statusTimer);
    try {
      SS.status = await api("system_search_status");
      if (!SS.status || SS.status.supported === false) {
        setIndexState(
          (SS.status && SS.status.message) || "Mumble Find is unavailable.",
          true,
        );
        return SS.status;
      }
      const total = Number(SS.status.total || 0);
      const stateMessage = SS.status.refreshing
        ? "Updating local index…"
        : SS.status.last_error
          ? "Index needs attention"
          : total
            ? `${total.toLocaleString()} local items`
            : "Local index is ready";
      setIndexState(stateMessage, !!SS.status.last_error);
      const state = $s("#ss-index-state");
      if (state) state.title = SS.status.last_error || "";
      const hotkey = $s("#ss-hotkey");
      if (hotkey) hotkey.textContent = prettyHotkey(SS.status.hotkey);
      const refresh = $s("#ss-refresh");
      if (refresh) {
        refresh.classList.toggle("spinning", !!SS.status.refreshing);
        refresh.disabled = !!SS.status.refreshing;
        refresh.setAttribute("aria-busy", SS.status.refreshing ? "true" : "false");
      }
      if (SS.status.refreshing && SS.open) {
        SS.statusTimer = window.setTimeout(async () => {
          const before = SS.status && SS.status.refreshing;
          await refreshStatus();
          if (before && SS.status && !SS.status.refreshing) runSearch();
        }, 650);
      }
      return SS.status;
    } catch (error) {
      setIndexState("Local index unavailable", true);
      return null;
    }
  }

  function setIndexState(message, error) {
    const state = $s("#ss-index-state");
    if (!state) return;
    state.textContent = message;
    state.classList.toggle("error", !!error);
  }

  async function refreshIndex() {
    const button = $s("#ss-refresh");
    if (button) {
      button.classList.add("spinning");
      button.disabled = true;
    }
    try {
      const result = await api("system_search_refresh");
      if (!result || result.ok === false) {
        const message =
          (result && result.message) || "The local index could not refresh.";
        if (typeof window.toast === "function")
          window.toast(message, "err", 3000);
        announce(message);
      } else {
        setIndexState("Updating local index…", false);
        announce("Updating the local search index.");
      }
    } catch (error) {
      setIndexState("Local index unavailable", true);
      announce("The local search index could not refresh.");
    }
    await refreshStatus();
  }

  async function runSearch() {
    const request = ++SS.request;
    const host = $s("#ss-results");
    if (!host) return;
    SS.iconRequest += 1;
    if (request > 1) api("system_search_cancel", request - 1);
    host.setAttribute("aria-busy", "true");
    setIndexState("Searching local indexes…", false);
    try {
      const result = await api(
        "system_search_query",
        SS.query,
        SS.category,
        12,
        request,
        75,
      );
      if (request !== SS.request || !result || result.stale ||
          Number(result.generation || request) !== request) return;
      if (!result || result.ok === false) {
        SS.results = [];
        SS.selected = 0;
        renderResults({
          ok: false,
          message:
            (result && result.message) || "The local search index could not be read.",
        });
        return;
      }
      SS.results = (result.results || []).slice(0, 12);
      SS.iconVersion = String(result.icon_version || "");
      SS.selected = Math.min(
        SS.selected,
        Math.max(0, SS.results.length - 1),
      );
      renderResults(result);
      announce(
        `${result.total_matches || SS.results.length} ${
          SS.results.length === 1 ? "result" : "results"
        }`,
      );
      if (result.refreshing) refreshStatus();
    } catch (error) {
      if (request !== SS.request) return;
      SS.results = [];
      renderResults({
        ok: false,
        message: "Mumble Find could not read the local index.",
      });
    } finally {
      if (request === SS.request) host.setAttribute("aria-busy", "false");
    }
  }

  function iconFor(kind) {
    return (
      { app: "monitor", file: "file", folder: "folder" }[
        kind
      ] || "search"
    );
  }

  function renderResults(result) {
    const host = $s("#ss-results");
    if (!host) return;
    $s("#ss-results-title").textContent = SS.query
      ? "Best matches"
      : "Your apps";
    $s("#ss-results-count").textContent = SS.results.length
      ? SS.query
        ? `${Number(result.total_matches || SS.results.length).toLocaleString()} found`
        : `${SS.results.length} shown`
      : "";
    if (result.ok === false) {
      host.innerHTML = `<div class="ss-empty ss-error" role="status"><div><span class="ss-empty-icon" data-icon="alert"></span><strong>Search needs a refresh</strong><p>${escapeHtml(result.message)}</p><button type="button" class="btn btn-sm" id="ss-empty-retry"><span data-icon="refresh"></span>Try again</button></div></div>`;
      $s("#ss-empty-retry", host)?.addEventListener("click", async () => {
        await refreshIndex();
        $s("#ss-input")?.focus();
      });
      paint(host);
      announce(result.message);
      return;
    }
    const providerState = String(result.provider_state || "complete");
    if (providerState === "partial" || providerState === "error") {
      setIndexState(
        result.message || "Some indexed locations are unavailable; installed applications remain searchable.",
        true,
      );
    } else {
      setIndexState("Local results are ready", false);
    }
    if (!SS.results.length) {
      host.innerHTML = `<div class="ss-empty" role="status"><div><span class="ss-empty-icon" data-icon="search"></span><strong>${
        SS.query ? "No local matches" : "Your index is warming up"
      }</strong><p>${
        SS.query
          ? "Try fewer words or another local category."
          : "Installed applications remain searchable while Windows Search becomes ready. You can start typing now."
      }</p></div></div>`;
      paint(host);
      return;
    }
    host.innerHTML = SS.results
      .map((item, index) => {
        const actions = item.actions || ["open"];
        const reveal = actions.includes("reveal")
          ? `<button type="button" class="btn-icon btn-ghost" data-ss-action="reveal" title="Show in folder" aria-label="Show ${escapeHtml(item.name)} in folder"><span data-icon="folder"></span></button>`
          : "";
        const favorite = actions.includes("favorite")
          ? `<button type="button" class="btn-icon btn-ghost ${item.favorite ? "on" : ""}" data-ss-action="favorite" title="${item.favorite ? "Remove from favourites" : "Add to favourites"}" aria-label="${item.favorite ? "Remove" : "Add"} ${escapeHtml(item.name)} ${item.favorite ? "from" : "to"} favourites"><span data-icon="star"></span></button>`
          : "";
        const icon = item.kind === "app"
          ? `<span class="ss-kind-icon" data-ss-app-icon="${escapeHtml(item.id)}"><span class="ss-app-icon-pending" aria-hidden="true"></span></span>`
          : `<span class="ss-kind-icon" data-icon="${iconFor(item.kind)}"></span>`;
        return `<div class="ss-result ${index === SS.selected ? "selected" : ""}" data-index="${index}" data-kind="${escapeHtml(item.kind)}" role="listitem" ${index === SS.selected ? 'aria-current="true"' : ""}>
          <button type="button" class="ss-result-main" data-ss-action="open">
            ${icon}
            <span class="ss-copy"><span class="ss-name"><span>${escapeHtml(item.name)}</span>${item.favorite ? '<span class="ss-fav-mark" data-icon="star"></span>' : ""}</span><span class="ss-meta">${escapeHtml(item.meta || item.subtitle || "Local")}</span></span>
          </button><span class="ss-result-actions">${favorite}${reveal}</span></div>`;
      })
      .join("");
    $$s(".ss-result", host).forEach((row) => {
      row.addEventListener("mouseenter", () =>
        selectIndex(Number(row.dataset.index)),
      );
      $$s("[data-ss-action]", row).forEach((button) =>
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          selectIndex(Number(row.dataset.index));
          executeSelected(button.dataset.ssAction || "open");
        }),
      );
    });
    paint(host);
    hydrateAppIcons(host);
  }

  async function hydrateAppIcons(host) {
    const request = SS.request;
    const iconRequest = SS.iconRequest;
    const iconVersion = SS.iconVersion;
    const slots = $$s("[data-ss-app-icon]", host)
      .filter((slot) => {
        const bounds = slot.getBoundingClientRect();
        return bounds.bottom >= 0 && bounds.top <= window.innerHeight;
      })
      .slice(0, 12);
    if (!slots.length) return;
    try {
      const result = await api(
        "system_search_icons",
        slots.map((slot) => slot.dataset.ssAppIcon),
        request,
        iconVersion,
      );
      if (request !== SS.request || iconRequest !== SS.iconRequest ||
          !result || result.stale || String(result.icon_version || "") !== iconVersion) return;
      const icons = result && result.icons;
      if (!icons || typeof icons !== "object") return;
      slots.forEach((slot) => {
        const source = icons[slot.dataset.ssAppIcon];
        if (typeof source !== "string" ||
            !/^data:image\/(?:png|svg\+xml|webp|jpeg);base64,/.test(source)) return;
        const image = document.createElement("img");
        image.className = "ss-app-icon-img";
        image.alt = "";
        image.src = source;
        image.addEventListener("error", () => image.remove(), { once: true });
        slot.replaceChildren(image);
        slot.classList.add("native-icon");
      });
    } catch (error) {
      // Keep the quiet loading surface; never invent or rotate an app mark.
    }
  }

  function selectIndex(index, announceSelection) {
    if (!SS.results.length) return;
    SS.selected = Math.max(0, Math.min(index, SS.results.length - 1));
    $$s(".ss-result", $s("#ss-results")).forEach((row, rowIndex) => {
      const selected = rowIndex === SS.selected;
      row.classList.toggle("selected", selected);
      if (selected) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
    if (announceSelection) {
      const item = SS.results[SS.selected];
      announce(`${item.name}, ${item.kind}, result ${SS.selected + 1} of ${SS.results.length}`);
    }
  }

  function moveSelection(delta) {
    if (!SS.results.length) return;
    selectIndex(
      (SS.selected + delta + SS.results.length) % SS.results.length,
      true,
    );
    $s(`.ss-result[data-index="${SS.selected}"]`)?.scrollIntoView({
      block: "nearest",
    });
  }

  async function executeSelected(action) {
    const item = SS.results[SS.selected];
    if (!item || SS.actionBusy) return;
    if (action === "reveal" && !(item.actions || []).includes("reveal"))
      action = "open";
    SS.actionBusy = true;
    const host = $s("#ss-results");
    host?.setAttribute("aria-busy", "true");
    const hidesForLaunch = standalone && (action === "open" || action === "reveal");
    try {
      if (hidesForLaunch && !(await closeSearch(false))) return;
      const result = await api("system_search_execute", item.id, action);
      if (!result || result.ok === false) {
        const message =
          (result && result.message) || "That item could not be opened.";
        if (typeof window.toast === "function")
          window.toast(message, "err", 3000);
        announce(message);
        if (hidesForLaunch) {
          await api("system_search_show");
          showSearch();
        }
        return;
      }
      if (action === "favorite") {
        item.favorite = result.favorite !== false;
        renderResults({ total_matches: SS.results.length });
        $s("#ss-input")?.focus();
        announce(item.favorite ? "Added to favourites" : "Removed from favourites");
      } else if (action === "copy_path") {
        if (typeof window.toast === "function")
          window.toast("Path copied", "ok", 1200);
        announce("Path copied");
      } else {
        if (!hidesForLaunch) await closeSearch(false);
      }
    } catch (error) {
      const message = "That item could not be opened.";
      if (typeof window.toast === "function")
        window.toast(message, "err", 3000);
      announce(message);
      if (hidesForLaunch) {
        await api("system_search_show");
        showSearch();
      }
    } finally {
      SS.actionBusy = false;
      host?.setAttribute("aria-busy", "false");
    }
  }

  window.bootSystemSearch = function (previewStatus) {
    SS.previewStatus = previewStatus || null;
    ensureSurface();
    return true;
  };
  window.showSystemSearch = showSearch;
  window.closeSystemSearch = () => closeSearch(true);
  window.__mumbleSystemSearchTest = {
    mockSearch,
    renderResults,
    iconFor,
    prettyHotkey,
    state: SS,
  };
})();
