const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const appRoot = path.join(repoRoot, "Internal", "app");
const uiSource = fs.readFileSync(
  path.join(appRoot, "experimental", "system_search", "ui.js"),
  "utf8",
);
const cssSource = fs.readFileSync(
  path.join(appRoot, "experimental", "system_search", "ui.css"),
  "utf8",
);
const mainHtml = fs.readFileSync(path.join(appRoot, "webui", "index.html"), "utf8");
const loaderSource = fs.readFileSync(
  path.join(appRoot, "webui", "system-search-loader.js"),
  "utf8",
);
const verifyCase = String(process.env.MUMBLE_FIND_VERIFY_CASE || "all");

async function verifyLoader(browser) {
  const cases = [
    { name: "success", value: { ok: true, state: "visible" }, expected: true },
    { name: "false", value: false, expected: false },
    { name: "backend failure", value: { ok: false, message: "backend failed" }, expected: false, message: "backend failed" },
    { name: "missing", value: undefined, expected: false },
    { name: "malformed", value: { state: "visible" }, expected: false },
    { name: "boolean true", value: true, expected: false },
  ];
  for (const entry of cases) {
    const page = await browser.newPage();
    await page.setContent("<!doctype html><html><body><button data-open-system-search></button></body></html>");
    await page.evaluate(({ value }) => {
      window.__loaderValue = value;
      window.__loaderMessages = [];
      window.toast = message => window.__loaderMessages.push(String(message));
      window.pywebview = { api: {
        system_search_show: async () => window.__loaderValue,
      } };
    }, { value: entry.value });
    await page.addScriptTag({ content: loaderSource });
    const result = await page.evaluate(() => window.openSystemSearch());
    assert.equal(result, entry.expected, `${entry.name} must preserve backend truth`);
    if (entry.message) {
      const messages = await page.evaluate(() => window.__loaderMessages);
      assert.ok(messages.some(message => message.includes(entry.message)), "backend failure message must be surfaced");
    }
    await page.close();
  }

  const thrownPage = await browser.newPage();
  await thrownPage.setContent("<!doctype html><html><body></body></html>");
  await thrownPage.evaluate(() => {
    window.__loaderMessages = [];
    window.toast = message => window.__loaderMessages.push(String(message));
    window.pywebview = { api: {
      system_search_show: async () => { throw new Error("transport failed"); },
    } };
  });
  await thrownPage.addScriptTag({ content: loaderSource });
  assert.equal(await thrownPage.evaluate(() => window.openSystemSearch()), false);
  await thrownPage.close();
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  if (verifyCase === "all" || verifyCase === "loader") {
    await verifyLoader(browser);
  }
  if (verifyCase === "loader") {
    console.log(JSON.stringify({ ok: true, case: "loader" }));
    await browser.close();
    return;
  }
  const page = await browser.newPage({ viewport: { width: 700, height: 520 } });
  const browserErrors = [];
  page.on("pageerror", error => browserErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") browserErrors.push(message.text());
  });

  await page.setContent(`<!doctype html><html><head></head><body class="search-popup-page enhanced"></body></html>`);
  await page.addStyleTag({ content: cssSource });
  await page.evaluate(() => {
    window.__findTest = {
      queryCalls: [],
      cancelCalls: [],
      iconCalls: [],
      iconGateOpen: false,
      resolveFirstIcons: null,
    };
    const rows = Array.from({ length: 20 }, (_, index) => ({
      id: `app-${index}`,
      kind: "app",
      name: `Application ${String(index).padStart(2, "0")}`,
      subtitle: "Application",
      meta: "Application",
      source: "start-menu",
      actions: ["open", "reveal"],
    }));
    window.pywebview = { api: {
      system_search_status: async () => ({
        ok: true,
        supported: true,
        refreshing: false,
        total: 20,
        counts: { app: 20, file: 0, folder: 0 },
        hotkey: "ctrl+alt+f",
        icon_version: "1:test",
        file_provider: { available: true, state: "ready", name: "Windows Search", message: "" },
      }),
      system_search_cancel: async generation => {
        window.__findTest.cancelCalls.push(generation);
        return true;
      },
      system_search_query: async (query, category, limit, generation, deadline) => {
        window.__findTest.queryCalls.push({ query, category, limit, generation, deadline });
        if (query === "missing") return {
          ok: true, results: [], total_matches: 0, generation,
          provider_state: "complete", icon_version: "1:test",
        };
        if (query === "partial") return {
          ok: true, results: rows.slice(0, 1), total_matches: 1, generation,
          provider_state: "partial",
          message: "Some indexed locations are unavailable; installed applications remain searchable.",
          icon_version: "1:test",
        };
        if (query === "failure") return {
          ok: false, results: [], generation, provider_state: "error",
          message: "Windows Search is unavailable.", icon_version: "1:test",
        };
        return {
          ok: true, results: rows.slice(0, limit), total_matches: rows.length,
          generation, provider_state: "complete", icon_version: "1:test",
        };
      },
      system_search_icons: async (ids, generation, iconVersion) => {
        window.__findTest.iconCalls.push({ ids, generation, iconVersion });
        if (!window.__findTest.iconGateOpen) {
          await new Promise(resolve => { window.__findTest.resolveFirstIcons = resolve; });
        }
        return { ok: true, icons: {}, stale: false, generation, icon_version: iconVersion };
      },
      system_search_execute: async () => ({ ok: true }),
      system_search_refresh: async () => ({ ok: true, refreshing: false }),
      system_search_hide: async () => ({ ok: true, state: "hidden" }),
      system_search_show: async () => ({ ok: true, state: "visible" }),
    } };
  });
  await page.addScriptTag({ content: uiSource });

  const coldStarted = Date.now();
  await page.evaluate(() => {
    window.bootSystemSearch();
    window.showSystemSearch();
  });
  await page.waitForSelector(".ss-result");
  const coldVisibilityMs = Date.now() - coldStarted;

  const firstPaint = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".ss-result")];
    const slots = [...document.querySelectorAll("[data-ss-app-icon]")];
    const visibleIds = slots.filter(slot => {
      const bounds = slot.getBoundingClientRect();
      return bounds.bottom >= 0 && bounds.top <= window.innerHeight;
    }).map(slot => slot.dataset.ssAppIcon);
    return {
      rowCount: rows.length,
      imageCount: document.querySelectorAll(".ss-app-icon-img").length,
      visibleIds,
      iconCall: window.__findTest.iconCalls[0],
      queryCall: window.__findTest.queryCalls[0],
      dragHeader: document.querySelector(".ss-header")?.classList.contains("pywebview-drag-region"),
      closeInHeader: Boolean(document.querySelector(".ss-header .ss-close")),
    };
  });
  assert.equal(firstPaint.rowCount, 12, "text-first page must contain at most 12 rows");
  assert.equal(firstPaint.imageCount, 0, "rows must paint before asynchronous icons finish");
  assert.deepEqual(firstPaint.iconCall.ids, firstPaint.visibleIds, "only visible icons may hydrate");
  assert.ok(firstPaint.iconCall.ids.length <= 12, "icon batch must remain bounded");
  assert.deepEqual(
    { limit: firstPaint.queryCall.limit, deadline: firstPaint.queryCall.deadline },
    { limit: 12, deadline: 75 },
  );
  assert.equal(firstPaint.dragHeader, true);
  assert.equal(firstPaint.closeInHeader, true);

  await page.evaluate(() => {
    window.__findTest.iconGateOpen = true;
    window.__findTest.resolveFirstIcons();
  });
  await page.waitForFunction(() => window.__findTest.iconCalls.length === 1);
  await page.locator(".ss-result").nth(11).scrollIntoViewIfNeeded();
  await page.waitForFunction(
    () => window.__findTest.iconCalls.length >= 2,
    undefined,
    { timeout: 3000 },
  );
  const scrollHydration = await page.evaluate(() => ({
    first: window.__findTest.iconCalls[0].ids,
    second: window.__findTest.iconCalls[1].ids,
  }));
  assert.ok(scrollHydration.second.length > 0, "newly visible rows must request icons");
  assert.ok(scrollHydration.second.length <= 12, "later icon request must remain bounded");
  assert.equal(
    scrollHydration.second.some(id => scrollHydration.first.includes(id)),
    false,
    "settled or pending icons must not be requested again",
  );

  async function query(value, expectedText) {
    await page.locator("#ss-input").fill(value);
    await page.locator("#ss-input").dispatchEvent("input");
    await page.waitForFunction(text => document.body.innerText.includes(text), expectedText);
  }
  await query("missing", "No local matches");
  await query("partial", "installed applications remain searchable");
  assert.equal(await page.locator(".ss-result").count(), 1, "partial state must preserve usable rows");
  await query("failure", "Try again");

  await page.evaluate(() => {
    window.__findTest.originalHide = window.pywebview.api.system_search_hide;
    window.pywebview.api.system_search_hide = async () => ({
      ok: false, state: "visible", message: "The window stayed visible.",
    });
  });
  await page.locator("#ss-close").click();
  await page.waitForFunction(() => document.querySelector("#ss-announcer")?.textContent === "The window stayed visible.");
  assert.equal(await page.locator("#ss-overlay").isVisible(), true, "a failed hide must keep usable content visible");
  await page.evaluate(() => {
    window.pywebview.api.system_search_hide = window.__findTest.originalHide;
  });

  const warmVisibilityMs = [];
  for (let index = 0; index < 30; index += 1) {
    const elapsed = await page.evaluate(async () => {
      await window.closeSystemSearch();
      const started = performance.now();
      window.showSystemSearch();
      const visible = !document.querySelector("#ss-overlay").hidden;
      return { elapsed: performance.now() - started, visible };
    });
    assert.equal(elapsed.visible, true);
    warmVisibilityMs.push(elapsed.elapsed);
  }

  const destinations = [...mainHtml.matchAll(/class="nav-btn[^"]*"[^>]*data-nav="([^"]+)"/g)]
    .map(match => match[1]);
  assert.deepEqual(destinations, ["home", "history", "stats", "meetings", "reader", "settings"]);
  assert.equal(await page.locator("#ss-nav-button").count(), 0, "Mumble Find must not become a seventh destination");
  assert.equal(browserErrors.length, 0, browserErrors.join(" | "));

  warmVisibilityMs.sort((left, right) => left - right);
  console.log(JSON.stringify({
    ok: true,
    coldVisibilityMs,
    headlessWarmRuns: warmVisibilityMs.length,
    headlessWarmVisibilityP95Ms: Number(warmVisibilityMs[28].toFixed(3)),
    firstPageRows: firstPaint.rowCount,
    visibleIconRequests: firstPaint.iconCall.ids.length,
    newlyVisibleIconRequests: scrollHydration.second.length,
    destinations,
  }));
  await browser.close();
}

main().catch(error => {
  console.error(error.stack || error);
  process.exit(1);
});
