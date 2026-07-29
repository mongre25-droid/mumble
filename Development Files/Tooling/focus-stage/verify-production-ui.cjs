const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const webuiRoot = path.join(repoRoot, "Internal", "app", "webui");
const entry = pathToFileURL(path.join(webuiRoot, "index.html")).href;
const requestedCase = (process.argv.find(arg => arg.startsWith("--case=")) || "").split("=")[1] || "all";
const capture = process.argv.includes("--capture");
const outputRoot = path.join(repoRoot, "Development Files", "Research", "focus-stage-production-screenshots");
const zoomExtensionRoot = path.join(__dirname, "browser-zoom-extension");
const evidenceManifestPath = path.join(outputRoot, "evidence-manifest.json");
const evidenceSourceFiles = [
  "Development Files/Tooling/focus-stage/browser-zoom-extension/manifest.json",
  "Development Files/Tooling/focus-stage/browser-zoom-extension/service-worker.js",
  "Development Files/Tooling/focus-stage/verify-production-ui.cjs",
  "Development Files/Tooling/focus-stage/verify-production-ui.ps1",
  "Internal/app/webui/app.css",
  "Internal/app/webui/app.js",
  "Internal/app/webui/enhanced.css",
  "Internal/app/webui/focus-stage-contract.json",
  "Internal/app/webui/focus-stage.css",
  "Internal/app/webui/focus-stage.js",
  "Internal/app/webui/index.html",
  "Internal/app/webui/mumble.png",
  "Internal/app/webui/remaster.css",
  "Internal/app/webui/system-search-loader.js",
];
const evidenceScreenshotNames = [
  "01-focus-stage-home-desktop.png",
  "02-focus-stage-navigation-narrow.png",
  "03-shared-state-surfaces.png",
  "04-forced-colours-focus.png",
  "05-deck-command-surface.png",
  "06-deck-selection-narrow.png",
  "05-meetings-before.png",
  "06-meetings-during.png",
  "07-meetings-after.png",
];
const results = [];
const screenshotHashes = {};
let expectedEvidence = null;

function record(name, details = "passed") {
  results.push({ name, details });
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function sourceHashes() {
  return Object.fromEntries(evidenceSourceFiles.map(relativePath => [
    relativePath,
    sha256((() => {
      const bytes = fs.readFileSync(path.join(repoRoot, ...relativePath.split("/")));
      if (path.extname(relativePath).toLowerCase() === ".png") return bytes;
      return Buffer.from(bytes.toString("utf8").replace(/\r\n?/g, "\n"), "utf8");
    })()),
  ]));
}

function loadAndValidateEvidenceManifest(browserVersion) {
  assert.ok(fs.existsSync(evidenceManifestPath), "screenshot evidence manifest is missing; regenerate with -Capture");
  const manifest = JSON.parse(fs.readFileSync(evidenceManifestPath, "utf8"));
  assert.equal(manifest.version, 1, "screenshot evidence manifest version is unsupported");
  assert.deepEqual(
    manifest.renderer,
    { browserVersion, platform: `${process.platform}-${process.arch}` },
    "screenshot evidence renderer changed; regenerate with -Capture",
  );
  assert.deepEqual(
    manifest.sources,
    sourceHashes(),
    "screenshot evidence is stale relative to the production UI or capture harness; regenerate with -Capture",
  );
  assert.deepEqual(
    Object.keys(manifest.screenshots).sort(),
    [...evidenceScreenshotNames].sort(),
    "screenshot evidence manifest must name exactly the nine current production images",
  );
  return manifest;
}

function writeEvidenceManifest(browserVersion) {
  const manifest = {
    version: 1,
    command: "verify-production-ui.ps1 -Case visual -Capture",
    renderer: { browserVersion, platform: `${process.platform}-${process.arch}` },
    sources: sourceHashes(),
    screenshots: Object.fromEntries(Object.entries(screenshotHashes).sort(([left], [right]) => left.localeCompare(right))),
  };
  fs.writeFileSync(evidenceManifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}

async function openProductionPage(browser, options = {}) {
  const page = await browser.newPage({
    viewport: options.viewport || { width: 1440, height: 1000 },
    colorScheme: "dark",
    reducedMotion: options.reducedMotion || "no-preference",
    forcedColors: options.forcedColors || "none",
  });
  const browserErrors = [];
  page.on("pageerror", error => browserErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.goto(`${entry}?ui-test=1`);
  await page.waitForFunction(() => window.__mumbleBooted === true);
  await page.waitForTimeout(600);
  return { page, browserErrors };
}

async function testShell(browser) {
  const { page, browserErrors } = await openProductionPage(browser);
  assert.equal(browserErrors.length, 0, browserErrors.join(" | "));
  assert.ok(await page.evaluate(() => Boolean(window.MumbleUIFoundation)), "packaged Focus Stage contract did not load");
  const packagedContract = JSON.parse(fs.readFileSync(path.join(webuiRoot, "focus-stage-contract.json"), "utf8"));
  const browserContract = await page.evaluate(() => ({
    version: window.MumbleUIFoundation.contract.version,
    destinations: window.MumbleUIFoundation.contract.destinations,
    primitives: window.MumbleUIFoundation.contract.primitives,
    effects: window.MumbleUIFoundation.contract.effects,
    statusSemantics: window.MumbleUIFoundation.contract.statusSemantics,
    maxPrimaryActions: window.MumbleUIFoundation.contract.maxPrimaryActions,
    states: window.MumbleUIFoundation.contract.states,
    controlRail: window.MumbleUIFoundation.contract.controlRail,
  }));
  assert.deepEqual(browserContract, {
    version: packagedContract.version,
    destinations: packagedContract.destinations,
    primitives: packagedContract.primitives,
    effects: packagedContract.effects,
    statusSemantics: packagedContract.statusSemantics,
    maxPrimaryActions: packagedContract.maxPrimaryActions,
    states: packagedContract.states,
    controlRail: packagedContract.controlRail,
  }, "browser and packaged contracts drifted");
  assert.equal(
    await page.locator('link[href^="focus-stage.css"]').count(),
    1,
    "the production page did not load the Focus Stage stylesheet",
  );
  const labels = await page.locator(".navbar .nav > .nav-btn").allTextContents();
  assert.deepEqual(labels.map(label => label.trim()), ["Home", "Deck", "Stats", "Meetings", "Reader", "Settings"]);
  assert.equal(await page.locator('.nav-btn[aria-current="page"]').count(), 1, "one current destination must be announced");
  assert.equal(await page.locator('.nav-btn[aria-current="page"]').innerText(), "Home");
  assert.equal(await page.locator(".focus-stage").count(), 1, "the existing Home hero must use the Focus Stage primitive");
  assert.equal(await page.locator('[data-view="home"] .context-ledger').count(), 1, "the Home shortcut panel must use the optional context ledger primitive");
  assert.equal(await page.locator(".numbered-spine").count(), 1, "the genuine Home sequence must use the numbered spine primitive");
  assert.equal(await page.locator(".command-surface").count(), 1, "the existing Deck toolbar must use the command surface primitive");
  assert.equal(await page.locator(".focus-stage .control-rail").count(), 1, "the production dictation actions must use the Control Rail primitive");
  assert.equal(await page.locator(".focus-stage .btn-gold").count(), 1, "the Focus Stage must expose one primary action");
  assert.equal(await page.locator(".home-focus-stage__primary").count(), 1, "Home must expose one centred voice-first primary stage");
  assert.equal(await page.locator(".home-latest-result").count(), 1, "Home must retain one restrained latest-result area");
  assert.equal(await page.locator(".home-capabilities-grid, .home-capabilities-archive").count(), 0, "Home must remove the permanent marketing capability grid rather than hide or rename it");
  const homeText = (await page.locator('[data-view="home"]').innerText()).replace(/\s+/g, " ");
  assert.equal(/10\s*(minutes?|:00)/i.test(homeText), false, "Home must not sell dictation as a ten-minute feature");
  assert.equal(/More than dictation|Everything in one place/i.test(homeText), false, "Home must not retain the old capability-tour marketing copy");
  assert.match(homeText, /bounded, recovery-safe segments/i, "Home must describe segmented dictation truthfully");
  const shortcutLabels = await page.locator(".home-shortcut-row b").allTextContents();
  assert.deepEqual(shortcutLabels, ["Dictate", "Paste latest", "Open Deck", "Mumble Find", "Web Search"]);
  assert.match(await page.locator(".home-shortcut-row").nth(3).innerText(), /stays on this device/i);
  assert.match(await page.locator(".home-shortcut-row").nth(4).innerText(), /confirm.*online/i);
  const homeFallbackTruth = await page.evaluate(async () => {
    MOCK.overview.transcription_mode = "cloud";
    MOCK.overview.cloud_transcription_provider = "groq";
    MOCK.overview.transcription_route = {
      requested: "cloud", effective: "local", reason: "no_key",
      provider: "groq", sends_audio: false,
    };
    await bootHome();
    return {
      hero: document.querySelector("#home-hero-copy")?.textContent || "",
      badge: document.querySelector("#home-audio-badge")?.textContent || "",
      detail: document.querySelector("#home-audio-detail")?.textContent || "",
    };
  });
  assert.match(homeFallbackTruth.hero, /on this device/i, "Home must describe the effective local transcription route");
  assert.match(homeFallbackTruth.badge, /locally/i, "Home's audio badge must use effective route truth");
  assert.match(homeFallbackTruth.detail, /Cloud.*saved|saved.*Cloud/i, "Home must explain the preserved saved Cloud choice separately");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  assert.deepEqual(
    await page.locator("[data-settings-tab]").allTextContents().then(labels => labels.map(label => label.trim())),
    ["Overview", "Speech to text", "Text shaping", "Deck & data", "System"],
    "Settings must use the five plain-language sections",
  );
  await page.waitForFunction(() => document.querySelector('[data-view="settings"]')?.dataset.settingsState === "ready");
  const requiredRouteFacts = [
    "Saved choice", "Effective route", "Why this route", "Input and engine", "Location",
    "What leaves this device", "Speed", "Privacy", "Quality boundary", "Cost",
  ];
  assert.deepEqual(
    await page.locator("#transcription-route-facts dt").allTextContents(),
    requiredRouteFacts,
    "Speech to text must explain every route fact in normal-user language",
  );
  assert.deepEqual(
    await page.locator("#processing-route-facts dt").allTextContents(),
    requiredRouteFacts,
    "Text shaping must explain every route fact in normal-user language",
  );
  assert.equal(await page.locator("[data-route-feature]").count(), 7, "the feature route ledger must cover all seven policy features");
  assert.deepEqual(
    await page.locator("[data-route-feature] h4").allTextContents(),
    ["Plain dictation", "Prompt", "Email", "Reply", "Deck actions", "Meetings analysis", "Reader actions"],
  );
  assert.deepEqual(
    await page.locator("[data-route-feature]").evaluateAll(rows => rows.map(row => row.dataset.routeFeature)),
    ["plain-dictation", "prompt", "email", "reply", "deck-actions", "meetings-analysis", "reader-actions"],
    "each route row must retain a distinct feature-policy identity",
  );
  const featureFacts = await page.locator("[data-route-feature]").evaluateAll(rows => rows.map(row => ({
    saved: row.querySelector('[data-route-value="saved"]')?.textContent.trim(),
    effective: row.querySelector('[data-route-value="effective"]')?.textContent.trim(),
    reason: row.querySelector('[data-route-value="reason"]')?.textContent.trim(),
    engine: row.querySelector('[data-route-value="engine"]')?.textContent.trim(),
    location: row.querySelector('[data-route-value="location"]')?.textContent.trim(),
    egress: row.querySelector('[data-route-value="egress"]')?.textContent.trim(),
    speed: row.querySelector('[data-route-value="speed"]')?.textContent.trim(),
    privacy: row.querySelector('[data-route-value="privacy"]')?.textContent.trim(),
    quality: row.querySelector('[data-route-value="quality"]')?.textContent.trim(),
    cost: row.querySelector('[data-route-value="cost"]')?.textContent.trim(),
  })));
  for (const [index, facts] of featureFacts.entries()) {
    assert.equal(Object.values(facts).every(Boolean), true, `feature route row ${index + 1} has an unexplained fact: ${JSON.stringify(facts)}`);
  }
  const savedLocalProvider = await page.evaluate(() => buildRouteFacts({
    kind: "text", route: {},
    decision: { requested_route: "hosted", effective_route: "local", reason: "unsupported_provider", provider: "local", model: "" },
    providerLabel: "local",
    reasonText: { unsupported_provider: "This build does not support the saved provider." },
    localEngine: "local text-shaping pipeline",
  }).saved);
  assert.equal(savedLocalProvider, "On-device local model · saved, unavailable", "a preserved unsupported local provider must never be labelled Hosted");
  const hostedCapability = await page.locator("#hosted-capability-status").evaluate(node => ({
    available: node.dataset.available,
    text: node.textContent.trim(),
  }));
  assert.equal(hostedCapability.available, "false");
  assert.match(hostedCapability.text, /unavailable/i, "missing-key hosted processing must be labelled unavailable");
  const unifiedReadiness = await page.evaluate(() => {
    delete MODELS_FETCHED.cerebras;
    const hostedDecision = {
      requested_route: "hosted", effective_route: "local", reason: "unconfirmed_model",
      provider: "cerebras", provider_supported: true, key_present: true,
      model: "gpt-oss-120b", ready: false,
    };
    SET._route_state = {
      ...SET._route_state,
      action_processing: {
        provider: "cerebras", provider_supported: true, has_key: true,
        effective: "local", reason: "unconfirmed_model", decision: hostedDecision,
      },
      feature_routes: Object.fromEntries(FEATURE_ROUTE_ROWS.map(([, key]) => [key, {...hostedDecision}])),
    };
    updateSetupSummary();
    return {
      banner: document.querySelector("#hosted-capability-status")?.textContent || "",
      rows: Array.from(document.querySelectorAll('[data-route-value="effective"]')).map(node => node.textContent.trim()),
    };
  });
  assert.match(unifiedReadiness.banner, /unavailable/i);
  assert.equal(unifiedReadiness.rows.every(value => !/Hosted.*ready/i.test(value)), true,
    "the banner and all seven rows must share one fail-closed readiness authority");
  const failClosedMatrix = await page.evaluate(() => {
    const cases = [
      { name: "missing model", reason: "missing_model", supported: true, key: true, model: "", fetched: [] },
      { name: "missing key", reason: "missing_key", supported: true, key: false, model: "gpt-oss-120b", fetched: ["gpt-oss-120b"] },
      { name: "unsupported provider", reason: "unsupported_provider", supported: false, key: false, model: "", fetched: [] },
      { name: "hosted off", reason: "hosted_processing_off", supported: true, key: true, model: "gpt-oss-120b", fetched: ["gpt-oss-120b"] },
      { name: "device only", reason: "device_only", supported: true, key: true, model: "gpt-oss-120b", fetched: ["gpt-oss-120b"] },
      { name: "unconfirmed model", reason: "ready", supported: true, key: true, model: "saved-but-absent", fetched: ["listed-model"] },
    ];
    return cases.map(item => {
      MODELS_FETCHED.cerebras = new Set(item.fetched);
      const decision = {
        requested_route: "hosted", effective_route: "local",
        reason: item.name === "unconfirmed model" ? "unconfirmed_model" : item.reason,
        provider: "cerebras", provider_supported: item.supported,
        key_present: item.key, model: item.model, ready: false,
      };
      SET._route_state = {
        ...SET._route_state,
        action_processing: {
          requested: "hosted", effective: decision.effective_route === "hosted" ? "cloud" : "local",
          reason: item.reason, provider: "cerebras", provider_supported: item.supported,
          has_key: item.key, decision,
        },
        feature_routes: Object.fromEntries(FEATURE_ROUTE_ROWS.map(([, key]) => [key, { ...decision }])),
      };
      updateSetupSummary();
      return {
        name: item.name,
        banner: document.querySelector("#hosted-capability-status")?.dataset.available,
        rows: Array.from(document.querySelectorAll('[data-route-value="effective"]')).map(node => node.textContent.trim()),
      };
    });
  });
  for (const item of failClosedMatrix) {
    assert.equal(item.banner, "false", `${item.name} must fail the banner closed`);
    assert.equal(item.rows.length, 7, `${item.name} must render all seven feature decisions`);
    assert.equal(item.rows.every(value => !/Hosted.*ready/i.test(value)), true,
      `${item.name} must fail every feature row closed`);
  }
  const keyChangeReadiness = await page.evaluate(async () => {
    delete MODELS_FETCHED.cerebras;
    let releaseModels;
    const modelResponse = new Promise(resolve => { releaseModels = resolve; });
    const cloudRoute = {
      ...SET._route_state,
      action_processing: {
        provider: "cerebras", provider_supported: true, has_key: true,
        effective: "cloud", reason: "selected", sends_text: true,
        decision: { requested_route: "hosted", effective_route: "hosted", reason: "ready", provider: "cerebras", model: "gpt-oss-120b", ready: true },
      },
    };
    window.pywebview = { api: {
      set_setting: async () => ({ ok: true }),
      list_models: async () => modelResponse,
      get_settings: async () => ({ _route_state: cloudRoute }),
    } };
    const key = document.querySelector('[data-setting="cerebras_api_key"]');
    key.dataset.masked = "";
    key.value = "saved-test-key";
    key.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 25));
    const before = document.querySelector("#hosted-capability-status").dataset.available;
    releaseModels({ ok: true, models: ["gpt-oss-120b"] });
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (document.querySelector("#hosted-capability-status").dataset.available === "true") break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    const node = document.querySelector("#hosted-capability-status");
    const after = node.dataset.available;
    const text = node.textContent.trim();
    let releaseReplacement;
    const replacementResponse = new Promise(resolve => { releaseReplacement = resolve; });
    window.pywebview.api.list_models = async () => replacementResponse;
    key.value = "invalid-replacement-key";
    key.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 25));
    const duringInvalidReplacement = document.querySelector("#hosted-capability-status").dataset.available;
    releaseReplacement({ ok: false, models: [] });
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (document.querySelector("#hosted-capability-status").dataset.available === "false") break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return {
      before, after, text,
      duringInvalidReplacement,
      afterInvalidReplacement: document.querySelector("#hosted-capability-status").dataset.available,
      afterClearedKey: await (async () => {
        SET._route_state = {
          ...cloudRoute,
          action_processing: {
            provider: "cerebras", provider_supported: true, has_key: true,
            effective: "cloud", reason: "selected", sends_text: true,
            decision: { requested_route: "hosted", effective_route: "hosted", reason: "ready", provider: "cerebras", model: "gpt-oss-120b", ready: true },
          },
        };
        updateSetupSummary();
        if (document.querySelector("#hosted-capability-status").dataset.available !== "true") return "setup-failed";
        window.pywebview.api.get_settings = async () => { throw new Error("route refresh unavailable"); };
        key.value = "";
        key.dispatchEvent(new Event("change", { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 25));
        return document.querySelector("#hosted-capability-status").dataset.available;
      })(),
      afterOverlappingKeys: await (async () => {
        let releaseFirst;
        let releaseSecond;
        const responses = [
          new Promise(resolve => { releaseFirst = resolve; }),
          new Promise(resolve => { releaseSecond = resolve; }),
        ];
        let responseIndex = 0;
        window.pywebview.api.list_models = async () => responses[responseIndex++];
        window.pywebview.api.get_settings = async () => ({ _route_state: cloudRoute });
        key.value = "first-overlapped-key";
        key.dispatchEvent(new Event("change", { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 10));
        key.value = "second-overlapped-key";
        key.dispatchEvent(new Event("change", { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 10));
        releaseSecond({ ok: false, models: [] });
        await new Promise(resolve => setTimeout(resolve, 20));
        releaseFirst({ ok: true, models: ["gpt-oss-120b"] });
        await new Promise(resolve => setTimeout(resolve, 40));
        return document.querySelector("#hosted-capability-status").dataset.available;
      })(),
    };
  });
  assert.deepEqual(
    { before: keyChangeReadiness.before, after: keyChangeReadiness.after },
    { before: "false", after: "true" },
    "a saved key must refresh hosted readiness as soon as the selected model is confirmed",
  );
  assert.match(keyChangeReadiness.text, /available/i);
  assert.equal(
    keyChangeReadiness.duringInvalidReplacement,
    "false",
    "a replacement key must fail closed while live model confirmation is pending",
  );
  assert.equal(
    keyChangeReadiness.afterInvalidReplacement,
    "false",
    "a failing replacement key must invalidate model confirmation from the previous key",
  );
  assert.equal(
    keyChangeReadiness.afterClearedKey,
    "false",
    "clearing a key must invalidate prior model confirmation even if route refresh fails",
  );
  assert.equal(
    keyChangeReadiness.afterOverlappingKeys,
    "false",
    "an older model-discovery response must not restore readiness after a newer key fails",
  );
  const modelRefreshes = await page.evaluate(async () => {
    const modelKeys = [
      "cerebras_model", "openrouter_model", "groq_transcription_model",
      "openai_transcription_model", "openrouter_transcription_model",
    ];
    const refreshed = [];
    let releaseOlder;
    const older = new Promise(resolve => { releaseOlder = resolve; });
    let getIndex = 0;
    window.pywebview = { api: {
      set_setting: async () => ({ok:true}),
      get_settings: async () => {
        getIndex += 1;
        if (getIndex === 1) return older;
        return { _route_state: { ...SET._route_state, action_processing: {
          provider:"openrouter", provider_supported:true, has_key:true,
          effective:"local", reason:"unconfirmed_model", decision:{model:"newer-model"},
        } } };
      },
    } };
    for (const [index, key] of modelKeys.entries()) {
      const control = document.querySelector(`[data-setting="${key}"]`);
      if (!control) throw new Error(`missing real model control: ${key}`);
      const option = document.createElement("option");
      option.value = `correction-model-${index}`;
      option.textContent = option.value;
      control.appendChild(option);
      control.value = option.value;
      control.dispatchEvent(new Event("change", {bubbles:true}));
      await new Promise(resolve => setTimeout(resolve, 20));
      refreshed.push(getIndex);
    }
    releaseOlder({ _route_state: { ...SET._route_state, action_processing: {
      provider:"cerebras", provider_supported:true, has_key:true,
      effective:"cloud", reason:"selected", decision:{model:"stale-model"},
    } } });
    await new Promise(resolve => setTimeout(resolve, 40));
    return {refreshed, finalModel:SET._route_state.action_processing.decision.model};
  });
  assert.deepEqual(modelRefreshes.refreshed, [1,2,3,4,5], "all five real model controls must refresh route truth");
  assert.equal(modelRefreshes.finalModel, "newer-model", "an older route response must not replace newer model truth");
  await page.locator('[data-settings-tab="system"]').click();
  assert.deepEqual(
    await page.locator('[data-setting="ui_effects"] option').allTextContents(),
    ["Light", "Standard", "Full effects"],
    "Appearance must be visibly separate from processing mode",
  );
  await page.getByRole("button", { name: "Home", exact: true }).click();
  for (const destination of packagedContract.destinations) {
    await page.getByRole("button", { name: destination.label, exact: true }).click();
    await page.waitForTimeout(40);
    assert.equal(await page.locator('.view:not([hidden])').count(), 1, `${destination.label} must keep one current page`);
    assert.equal(await page.locator('.view:not([hidden])').getAttribute("data-view"), destination.id);
    assert.equal(await page.locator('.nav-btn[aria-current="page"]').getAttribute("data-nav"), destination.id);
  }
  record("production shell and shared primitives");
  await page.close();
}

async function testStates(browser) {
  const { page, browserErrors } = await openProductionPage(browser);
  assert.equal(browserErrors.length, 0, browserErrors.join(" | "));
  const evidence = await page.evaluate(() => {
    const api = window.MumbleUIFoundation;
    const host = document.createElement("div");
    host.id = "foundation-state-test";
    document.body.append(host);
    const heights = [];
    for (const kind of ["loading", "empty", "degraded", "error", "success"]) {
      const content = document.createElement("div");
      content.textContent = `Usable ${kind} content`;
      const surface = api.createStateSurface(kind, {
        content,
        action: kind === "degraded" ? { label: "Try again" } : null,
      });
      host.append(surface);
      heights.push(surface.getBoundingClientRect().height);
    }
    return {
      stateNames: [...host.querySelectorAll(".state-surface")].map(node => node.dataset.state),
      preserved: [...host.querySelectorAll(".state-surface__content")].map(node => node.textContent.trim()),
      actions: host.querySelectorAll(".state-surface__action").length,
      loadingBusy: host.querySelector('[data-state="loading"]').getAttribute("aria-busy"),
      errorRole: host.querySelector('[data-state="error"]').getAttribute("role"),
      heights,
    };
  });
  assert.deepEqual(evidence.stateNames, ["loading", "empty", "degraded", "error", "success"]);
  assert.deepEqual(evidence.preserved, [
    "Usable loading content", "Usable empty content", "Usable degraded content",
    "Usable error content", "Usable success content",
  ], "state surfaces must preserve usable content");
  assert.equal(evidence.actions, 1, "a state surface exposes at most the supplied recovery action");
  assert.equal(evidence.loadingBusy, "true");
  assert.equal(evidence.errorRole, "alert");
  assert.ok(Math.max(...evidence.heights) - Math.min(...evidence.heights) < 1, "state anatomy must remain stable across kinds");

  const liveRegion = await page.evaluate(async () => {
    const initiallyMounted = Boolean(document.querySelector("#home-live-state > .state-surface"));
    const listening = {
      live: true,
      recording: true,
      state: "listening",
      text: "Listening for unchanged-poll evidence",
    };
    reflectStatus(listening);
    const host = document.querySelector("#home-live-state");
    const first = host.querySelector(".state-surface");
    const mutations = [];
    const observer = new MutationObserver(records => mutations.push(...records));
    observer.observe(host, { subtree: true, childList: true, characterData: true, attributes: true });
    reflectStatus({ ...listening });
    await Promise.resolve();
    mutations.push(...observer.takeRecords());
    const unchangedMutations = mutations.splice(0).length;
    const second = host.querySelector(".state-surface");
    reflectStatus({ ...listening, text: "Listening message changed" });
    await Promise.resolve();
    mutations.push(...observer.takeRecords());
    const changedMutations = mutations.splice(0).length;
    const third = host.querySelector(".state-surface");
    observer.disconnect();
    return {
      initiallyMounted,
      unchangedIdentity: first === second,
      changedIdentity: first === third,
      unchangedMutations,
      changedMutations,
      message: third.querySelector(".state-surface__copy p")?.textContent,
    };
  });
  assert.equal(liveRegion.initiallyMounted, true, "the Home live-region node must be mounted persistently at startup");
  assert.equal(liveRegion.unchangedIdentity, true, "an unchanged poll must keep the live-region node identity");
  assert.equal(liveRegion.changedIdentity, true, "a real message change must update the persistent live-region node");
  assert.equal(liveRegion.unchangedMutations, 0, "an unchanged poll must not mutate or retrigger the live region");
  assert.ok(liveRegion.changedMutations > 0, "a true message change must remain observable to the live region");
  assert.equal(liveRegion.message, "Listening message changed");

  const actionAndAria = await page.evaluate(async () => {
    const activated = [];
    const error = {
      live: true,
      recording: false,
      state: "error",
      text: "Microphone unavailable",
      recoveryAction: { label: "Try again", onActivate: () => activated.push("retry") },
    };
    reflectStatus(error);
    const host = document.querySelector("#home-live-state");
    const surface = host.querySelector(".state-surface");
    const firstAction = surface.querySelector(".state-surface__action");
    if (firstAction) firstAction.focus();
    reflectStatus({
      ...error,
      text: "Microphone permission is still unavailable",
      recoveryAction: { label: "Open Settings", onActivate: () => activated.push("settings") },
    });
    await Promise.resolve();
    const changedAction = surface.querySelector(".state-surface__action");
    if (changedAction) changedAction.click();
    const changed = {
      surfaceIdentity: surface === host.querySelector(".state-surface"),
      actionIdentity: firstAction === changedAction,
      actionCount: surface.querySelectorAll(".state-surface__action:not([hidden])").length,
      actionLabel: changedAction?.textContent,
      actionFocused: changedAction === document.activeElement,
      activated: [...activated],
      role: surface.getAttribute("role"),
      live: surface.getAttribute("aria-live"),
      atomic: surface.getAttribute("aria-atomic"),
      tone: surface.dataset.tone,
      state: surface.dataset.state,
      chipError: document.querySelector("#status-chip").classList.contains("is-error"),
      chipRecording: document.querySelector("#status-chip").classList.contains("is-recording"),
    };
    if (changedAction) changedAction.focus();
    reflectStatus({ ...error, recoveryAction: null });
    await Promise.resolve();
    return {
      changed,
      afterRemoval: {
        actionCount: surface.querySelectorAll(".state-surface__action:not([hidden])").length,
        focusMovedToSurface: document.activeElement === surface,
        surfaceIdentity: surface === host.querySelector(".state-surface"),
      },
    };
  });
  assert.equal(actionAndAria.changed.surfaceIdentity, true, "state changes must retain the persistent live-region node");
  assert.equal(actionAndAria.changed.actionIdentity, true, "recovery action changes must update the persistent button");
  assert.equal(actionAndAria.changed.actionCount, 1, "the live state must expose at most one recovery action");
  assert.equal(actionAndAria.changed.actionLabel, "Open Settings");
  assert.equal(actionAndAria.changed.actionFocused, true, "changing a focused recovery action must preserve focus");
  assert.deepEqual(actionAndAria.changed.activated, ["settings"], "the changed recovery action must use its new behaviour");
  assert.deepEqual(
    {
      role: actionAndAria.changed.role,
      live: actionAndAria.changed.live,
      atomic: actionAndAria.changed.atomic,
      tone: actionAndAria.changed.tone,
      state: actionAndAria.changed.state,
      chipError: actionAndAria.changed.chipError,
      chipRecording: actionAndAria.changed.chipRecording,
    },
    { role: "alert", live: "assertive", atomic: "true", tone: "danger", state: "error", chipError: true, chipRecording: false },
    "error state, colour, and announcement semantics must agree",
  );
  assert.deepEqual(
    actionAndAria.afterRemoval,
    { actionCount: 0, focusMovedToSurface: true, surfaceIdentity: true },
    "removing a focused recovery action must retain the region and move focus safely",
  );
  record("stable loading, empty, degraded, error, and success anatomy");
  await page.close();
}

async function testDeck(browser) {
  const desktop = await openProductionPage(browser, { reducedMotion: "reduce" });
  assert.equal(desktop.browserErrors.length, 0, desktop.browserErrors.join(" | "));
  await desktop.page.getByRole("button", { name: "Deck", exact: true }).click();
  await desktop.page.waitForTimeout(180);

  const surface = desktop.page.locator("#deck-command-surface");
  assert.equal(await surface.count(), 1, "Deck must expose one stable command surface");
  const browse = surface.locator('[data-deck-state="browse"]');
  const selection = surface.locator('[data-deck-state="selection"]');
  assert.equal(await browse.isVisible(), true, "browse controls must be the default command-surface state");
  assert.equal(await selection.isHidden(), true, "selection controls must stay out of permanent view");
  for (const selector of [
    "#hist-content-nav", "#hist-filter", "#hist-sort", "#hist-starred-toggle",
    "#hist-pin", "#deck-more-toggle",
  ]) {
    assert.equal(await browse.locator(selector).count(), 1, `${selector} must belong to the browse state`);
  }
  assert.equal(await desktop.page.locator("#hist-search").count(), 0, "Deck must not keep a duplicate Search control");
  assert.equal(await desktop.page.locator(".hist-toolbox").count(), 0, "preset catalogues must not stay permanently visible");
  assert.equal(await desktop.page.locator("#deck-secondary-actions").isHidden(), true,
    "uncommon browse actions must be closed initially");
  await browse.getByRole("button", { name: "More", exact: true }).click();
  assert.equal(await desktop.page.locator("#deck-secondary-actions").isVisible(), true,
    "the labelled More control must reveal uncommon browse actions");
  for (const name of ["Capture", "Capture chat", "Mumble Find", "Refresh", "Keyboard"]) {
    assert.equal(await browse.getByRole("button", { name, exact: true }).count(), 1,
      `${name} must remain available from More`);
  }
  await desktop.page.evaluate(() => {
    window.__deckRouteCalls = { mumbleFind: 0, webSearch: [] };
    window.pywebview = { api: {
      system_search_show: async () => {
        window.__deckRouteCalls.mumbleFind += 1;
        return { ok: true };
      },
      request_web_search: async text => {
        window.__deckRouteCalls.webSearch.push(text);
        return { ok: true, request_id: "deck-route-test" };
      },
    } };
  });
  await browse.getByRole("button", { name: "Mumble Find", exact: true }).click();
  assert.deepEqual(
    await desktop.page.evaluate(() => window.__deckRouteCalls),
    { mumbleFind: 1, webSearch: [] },
    "browse Mumble Find must call only the existing private system-search route",
  );
  await browse.getByRole("button", { name: "Fewer", exact: true }).click();

  const imageAction = desktop.page.getByRole("button", { name: "Paste image into previous app", exact: true });
  assert.equal(await imageAction.count(), 1, "image rows need one explicit accessible paste action");
  const imageRow = imageAction.locator("xpath=ancestor::article[1]");
  assert.equal(await imageRow.locator(".hist-image-thumb").count(), 1, "image rows must be thumbnail-led");

  const firstTextRow = desktop.page.locator(".deck-selectable-row").first();
  await firstTextRow.focus();
  await firstTextRow.press("Space");
  assert.equal(await firstTextRow.evaluate(node => document.activeElement === node), true,
    "focus must remain on the selected row while the command surface transforms");
  assert.equal(await selection.isVisible(), true, "selection must transform the same command surface");
  assert.equal(await browse.isHidden(), true, "browse controls must yield to selection controls in the same surface");
  assert.match(await selection.locator("#hist-sel-info").innerText(), /^1 selected/);
  for (const name of ["Smart Mode", "Preset", "Run shaping", "Search the web", "Clear selection"]) {
    assert.equal(await selection.getByRole("button", { name, exact: true }).count(), 1,
      `${name} must be available in selection state`);
  }
  const selectedText = await firstTextRow.locator(".row-text").innerText();
  await selection.getByRole("button", { name: "Search the web", exact: true }).click();
  assert.deepEqual(
    await desktop.page.evaluate(() => window.__deckRouteCalls),
    { mumbleFind: 1, webSearch: [selectedText.trim()] },
    "selected Search the web must stay separate and use only the consent-preparation route",
  );
  await selection.getByRole("button", { name: "Smart Mode", exact: true }).click();
  assert.equal(await selection.locator("#hist-mode-menu").isVisible(), true,
    "Smart Mode catalogue must open only when requested");
  assert.equal(await selection.locator("#hist-preset-menu").isHidden(), true);
  await selection.getByRole("button", { name: "Preset", exact: true }).click();
  assert.equal(await selection.locator("#hist-mode-menu").isHidden(), true);
  assert.equal(await selection.locator("#hist-preset-menu").isVisible(), true,
    "Preset catalogue must replace the other on-demand catalogue");
  const reducedTransition = await surface.evaluate(node => getComputedStyle(node).transitionDuration);
  assert.equal(reducedTransition, "0s", "selection remains understandable without motion");
  await selection.getByRole("button", { name: "Clear selection", exact: true }).click();
  await desktop.page.waitForTimeout(40);
  assert.equal(await browse.isVisible(), true, "Clear must restore the browse state");
  assert.equal(await desktop.page.locator("#hist-filter").evaluate(node => document.activeElement === node), true,
    "a disappearing selection control must move focus to the browse filter");
  await desktop.page.close();

  const narrow = await openProductionPage(browser, {
    viewport: { width: 430, height: 900 }, reducedMotion: "reduce",
  });
  assert.equal(narrow.browserErrors.length, 0, narrow.browserErrors.join(" | "));
  await narrow.page.getByRole("button", { name: "Deck", exact: true }).click();
  await narrow.page.waitForTimeout(180);
  const narrowImageAction = narrow.page.getByRole("button", { name: "Paste image into previous app", exact: true });
  assert.equal(await narrowImageAction.isVisible(), true, "image paste must stay visible at narrow widths");
  const narrowRowActions = narrowImageAction.locator("xpath=ancestor::*[contains(@class,'row-actions')][1]");
  assert.equal(await narrowRowActions.evaluate(node => getComputedStyle(node).opacity), "1",
    "hover actions must remain visible without hover at narrow widths");
  await narrow.page.close();
  record("Deck browse/selection surface, image action, focus, reduced motion, and narrow layout");
}

async function testKeyboard(browser) {
  const { page, browserErrors } = await openProductionPage(browser);
  assert.equal(browserErrors.length, 0, browserErrors.join(" | "));
  const first = page.getByRole("button", { name: "Home", exact: true });
  await first.focus();
  const order = [];
  for (let index = 0; index < 7; index += 1) {
    order.push(await page.evaluate(() => (document.activeElement?.getAttribute("aria-label") || document.activeElement?.innerText.trim()).replace(/\s+/g, " ")));
    if (index < 6) await page.keyboard.press("Tab");
  }
  assert.deepEqual(order, ["Home", "Deck", "Stats", "Meetings", "Reader", "Settings", "Start dictation Ctrl + Win"]);
  await first.focus();
  const focusStyle = await first.evaluate(node => ({ outline: getComputedStyle(node).outlineStyle, shadow: getComputedStyle(node).boxShadow }));
  assert.notEqual(focusStyle.outline, "none", "keyboard focus must have a visible outline");
  assert.notEqual(focusStyle.shadow, "none", "keyboard focus must include a contrasting separation ring");

  await page.locator("#record-btn").focus();
  await page.evaluate(() => {
    window.__foundationModalResult = confirmModal({
      title: "Discard draft?",
      body: "This browser-only check never changes saved data.",
      confirmText: "Discard",
    });
  });
  const dialog = page.getByRole("dialog", { name: "Discard draft?" });
  await dialog.waitFor();
  assert.equal(await page.locator("#app").evaluate(node => node.inert), true, "background application must be inert while a modal is open");
  const cancel = dialog.getByRole("button", { name: "Cancel" });
  const discard = dialog.getByRole("button", { name: "Discard" });
  assert.equal(await cancel.evaluate(node => node === document.activeElement), true);
  await page.keyboard.press("Shift+Tab");
  assert.equal(await discard.evaluate(node => node === document.activeElement), true, "reverse tab must remain inside the modal");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  assert.equal(await page.locator("#record-btn").evaluate(node => node === document.activeElement), true, "closing a modal must restore its invoking focus");
  assert.equal(await page.locator("#app").evaluate(node => node.inert), false);
  record("keyboard order, modal containment, accessible naming, and focus restoration");
  await page.close();
}

async function testStatsReader(browser) {
  const partial = await openProductionPage(browser);
  assert.equal(partial.browserErrors.length, 0, partial.browserErrors.join(" | "));
  await partial.page.evaluate(() => {
    window.pywebview = {
      api: {
        get_stats_dashboard: () => new Promise(resolve => { window.__resolveStats = resolve; }),
      },
    };
  });
  await partial.page.getByRole("button", { name: "Stats", exact: true }).click();
  const statsView = partial.page.locator('[data-view="stats"]');
  assert.equal(await statsView.getByRole("heading", { name: "Stats", exact: true }).count(), 1);
  await partial.page.waitForFunction(() => typeof window.__resolveStats === "function");
  assert.equal(await statsView.getAttribute("aria-busy"), "true");
  assert.match(
    await partial.page.locator("#stats-refresh-status").innerText(),
    /loading|refreshing/i,
    "Stats must expose a visible loading state",
  );
  await partial.page.evaluate(() => window.__resolveStats({
    ok: true,
    generated_at: Date.UTC(2026, 6, 26, 12) / 1000,
    dictation: { available: false, health: "error", has_activity: null },
    reader: {
      available: true, health: "ok", has_activity: true,
      total_reading_seconds: 120, total_reading_display: "2m",
      words_read: 55, total_sessions: 2, avg_session_sec: 60,
      avg_session_display: "1m", current_streak: 1, best_streak: 2,
    },
    meetings: {
      available: false, health: "error", has_activity: null,
      saved_count: null, saved_duration_seconds: null,
      duration_complete: false, latest_created: null,
      processing_count: null, attention_count: null,
    },
  }));
  await partial.page.waitForFunction(() => document.querySelector('[data-view="stats"]').getAttribute("aria-busy") === "false");
  assert.match(
    await partial.page.locator("#stats-data-period").innerText(),
    /snapshot through.*where available.*latest 98 days/i,
    "Stats period wording must not imply unavailable totals are complete",
  );
  assert.match(await partial.page.locator("#stats-refresh-status").innerText(), /partial/i);
  const partialTiles = await partial.page.locator("#stat-tiles .tile").evaluateAll(nodes => Object.fromEntries(nodes.map(node => [
    node.querySelector(".tile-lab")?.textContent.trim(),
    node.querySelector(".num")?.textContent.trim(),
  ])));
  assert.equal(partialTiles["Output words"], "\u2014", "unavailable dictation must not be rendered as zero");
  assert.equal(partialTiles["Reader time"], "2m", "available Reader activity must survive a partial snapshot");
  assert.equal(partialTiles["Meetings"], "\u2014", "unavailable Meetings must not be rendered as zero");
  assert.match(await partial.page.locator("#stats-definitions").innerText(), /output words.*active day.*WPM.*estimate/is);
  assert.equal(await partial.page.locator("#stat-chart").getAttribute("role"), "img");
  assert.equal(await partial.page.locator("#stat-table table").count(), 0);
  assert.match(await partial.page.locator("#stat-table").textContent(), /unavailable/i);
  await partial.page.close();

  const empty = await openProductionPage(browser);
  assert.equal(empty.browserErrors.length, 0, empty.browserErrors.join(" | "));
  await empty.page.evaluate(() => {
    window.pywebview = { api: { get_stats_dashboard: async () => ({
      ok: true, generated_at: Date.UTC(2026, 6, 26, 12) / 1000,
      dictation: {
        available: true, health: "ok", has_activity: false,
        summary: { total_words: 0, total_transcripts: 0, typing_hours_saved: 0 },
        current_streak: 0, best_streak: 0,
        daily: Array.from({ length: 98 }, (_, index) => ({
          day: new Date(Date.UTC(2026, 3, index + 1)).toISOString().slice(0, 10),
          words: 0,
          transcripts: 0,
        })),
        modes: [], insights: { tod_hours: Array(24).fill(0), weekday_words: Array(7).fill(0), weekday_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], this_week: 0, prev_week: 0, month_this: 0, month_prev: 0, today_words: 0 },
      },
      reader: { available: true, health: "ok", has_activity: false, total_reading_seconds: 0, words_read: 0, total_sessions: 0 },
      meetings: { available: true, health: "ok", has_activity: false, saved_count: 0, saved_duration_seconds: 0, duration_complete: true, processing_count: 0, attention_count: 0 },
    }) } };
  });
  await empty.page.getByRole("button", { name: "Stats", exact: true }).click();
  await empty.page.waitForFunction(() => document.querySelector('[data-view="stats"]').getAttribute("aria-busy") === "false");
  assert.match(await empty.page.locator("#stat-chart").innerText(), /no output words/i);
  assert.match(await empty.page.locator("#reader-activity-state").innerText(), /no Reader listening/i);
  assert.match(await empty.page.locator("#meeting-activity-state").innerText(), /no meetings are saved/i);
  await empty.page.close();

  const reader = await openProductionPage(browser);
  assert.equal(reader.browserErrors.length, 0, reader.browserErrors.join(" | "));
  await reader.page.evaluate(() => {
    MOCK.readerLib[0].text = Array.from({ length: 1400 }, (_, index) => `word${index}`).join(" ");
    MOCK.readerLib[0].position = 240;
    READER.disclosure.summary = true;
  });
  await reader.page.getByRole("button", { name: "Reader", exact: true }).click();
  assert.equal(await reader.page.getByRole("heading", { name: "Reader", exact: true }).count(), 1);
  await reader.page.locator("#reader-lib-list .rl-item").first().waitFor();
  const readerOrder = await reader.page.evaluate(() => {
    const library = document.querySelector("#reader-library");
    const voice = document.querySelector("#reader-voice-settings");
    return {
      libraryBeforeVoice: Boolean(library.compareDocumentPosition(voice) & Node.DOCUMENT_POSITION_FOLLOWING),
      voiceVisible: !document.querySelector("#reader-voice-primary").hidden,
      technicalCollapsed: !document.querySelector("#reader-voice-technical").open,
      providerInsideTechnical: document.querySelector("#reader-voice-technical").contains(document.querySelector("#reader-provider")),
      modelInsideTechnical: document.querySelector("#reader-voice-technical").contains(document.querySelector("#reader-model")),
    };
  });
  assert.deepEqual(readerOrder, {
    libraryBeforeVoice: true,
    voiceVisible: true,
    technicalCollapsed: true,
    providerInsideTechnical: true,
    modelInsideTechnical: true,
  }, "Reader must remain library-first while keeping human voice choice ahead of technical details");

  await reader.page.locator("#reader-lib-list .rl-item").first().click();
  await reader.page.locator("#reader-player").waitFor({ state: "visible" });
  const disclosure = reader.page.getByRole("dialog", { name: "Send document text?" });
  await disclosure.waitFor();
  await reader.page.keyboard.press("Escape");
  await disclosure.waitFor({ state: "detached" });
  await reader.page.waitForTimeout(550);
  const beforeFind = await reader.page.locator("#reader-pane").evaluate(node => {
    node.scrollTop = Math.min(220, node.scrollHeight - node.clientHeight);
    node.focus();
    return node.scrollTop;
  });
  await reader.page.keyboard.press("Control+f");
  await reader.page.locator("#reader-find-input").fill("word1200");
  await reader.page.waitForTimeout(160);
  const capturedFind = await reader.page.evaluate(() => ({
    scrollTop: READER.findOrigin && READER.findOrigin.scrollTop,
    focusId: READER.findOrigin && READER.findOrigin.focus && READER.findOrigin.focus.id,
    behavior: getComputedStyle(document.querySelector("#reader-pane")).scrollBehavior,
    inlineBehavior: document.querySelector("#reader-pane").style.scrollBehavior,
  }));
  await reader.page.keyboard.press("Escape");
  await reader.page.waitForTimeout(80);
  const afterFind = await reader.page.locator("#reader-pane").evaluate(node => ({
    focused: document.activeElement === node,
    scrollTop: node.scrollTop,
  }));
  assert.equal(afterFind.focused, true, "closing Find must restore focus to the reading pane");
  assert.ok(
    Math.abs(afterFind.scrollTop - beforeFind) <= 2,
    `closing Find must restore the prior reading position: ${JSON.stringify({ beforeFind, capturedFind, afterFind })}`,
  );

  const beforeSummary = await reader.page.locator("#reader-pane").evaluate(node => node.scrollTop);
  await reader.page.locator("#reader-summarize").click();
  await reader.page.locator("#reader-summary").waitFor({ state: "visible" });
  assert.equal(await reader.page.getByRole("region", { name: "AI summary" }).count(), 1);
  await reader.page.locator("#reader-summary-close").click();
  await reader.page.waitForTimeout(80);
  const summaryFocus = await reader.page.locator("#reader-summarize").evaluate(node => ({
    restored: document.activeElement === node,
    activeId: document.activeElement && document.activeElement.id,
    invokerId: READER.summaryInvoker && READER.summaryInvoker.id,
  }));
  assert.equal(summaryFocus.restored, true,
    `closing Summary must restore its invoking focus: ${JSON.stringify(summaryFocus)}`);
  const afterSummary = await reader.page.locator("#reader-pane").evaluate(node => node.scrollTop);
  assert.ok(Math.abs(afterSummary - beforeSummary) <= 2,
    `closing Summary must preserve reading position: ${JSON.stringify({ beforeSummary, afterSummary })}`);

  await reader.page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
  const readerFirst = await reader.page.screenshot();
  const readerSecond = await reader.page.screenshot();
  assert.ok(readerFirst.equals(readerSecond), "Reader screenshot must be deterministic");
  await reader.page.close();

  const narrow = await openProductionPage(browser, { viewport: { width: 430, height: 900 }, reducedMotion: "reduce" });
  assert.equal(narrow.browserErrors.length, 0, narrow.browserErrors.join(" | "));
  for (const destination of ["Stats", "Reader"]) {
    await narrow.page.getByRole("button", { name: destination, exact: true }).click();
    await narrow.page.waitForTimeout(180);
    const overflow = await narrow.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${destination} must not scroll horizontally at 430px`);
  }
  await narrow.page.getByRole("button", { name: "Stats", exact: true }).click();
  await narrow.page.waitForTimeout(180);
  assert.equal(
    await narrow.page.locator(".modebar-row .fill").first().evaluate(node => getComputedStyle(node).animationName),
    "none",
    "Stats chart growth must be removed when reduced motion is requested",
  );
  const statsFirst = await narrow.page.screenshot();
  const statsSecond = await narrow.page.screenshot();
  assert.ok(statsFirst.equals(statsSecond), "Stats screenshot must be deterministic");
  await narrow.page.close();
  record("Stats and Reader truth, focus, reflow, reduced motion, and deterministic screenshots");
}

async function testMeetings(browser) {
  const current = await openProductionPage(browser, { reducedMotion: "reduce" });
  assert.equal(current.browserErrors.length, 0, current.browserErrors.join(" | "));
  await current.page.evaluate(() => {
    MOCK.meetings = [];
    window.__meetingStopCalls = 0;
    window.__meetingStopFailures = ["active", "inactive"];
    window.__meetingListCalls = 0;
    window.__deferMeetingStatus = false;
    window.__meetingStatusPending = false;
    window.__releaseMeetingStatus = null;
    window.__meetingCapture = { active: false, state: "idle", captured_seconds: 0, audio_level: 0, microphone: null };
    window.pywebview = { api: {
      meeting_list: async () => {
        window.__meetingListCalls += 1;
        return MOCK.meetings;
      },
      meeting_start_recording: async () => {
        window.__meetingCapture = {
          active: true, state: "recording", captured_seconds: 0, audio_level: 0.34,
          microphone: { index: 7, name: "Conference microphone" },
        };
        return { ok: true, ...window.__meetingCapture, recording: true, paused: false, max_seconds: 14400 };
      },
      meeting_recording_status: async () => {
        const snapshot = {
          ok: true, ...window.__meetingCapture,
          recording: window.__meetingCapture.state === "recording",
          paused: window.__meetingCapture.state === "paused",
          max_seconds: 14400,
        };
        if (window.__deferMeetingStatus) {
          window.__deferMeetingStatus = false;
          window.__meetingStatusPending = true;
          return new Promise(resolve => {
            window.__releaseMeetingStatus = () => {
              window.__meetingStatusPending = false;
              resolve(snapshot);
            };
          });
        }
        return snapshot;
      },
      meeting_pause_recording: async () => {
        window.__meetingCapture.state = "paused";
        return { ok: true };
      },
      meeting_resume_recording: async () => {
        window.__meetingCapture.state = "recording";
        return { ok: true };
      },
      meeting_stop_recording: async () => {
        window.__meetingStopCalls += 1;
        const failure = window.__meetingStopFailures.shift();
        if (failure) {
          if (failure === "inactive")
            window.__meetingCapture = { active: false, state: "idle", captured_seconds: 0, audio_level: 0 };
          if (failure === "inactive" && !MOCK.meetings.length) MOCK.meetings.push({
            id: "issue23-reconciled", title: "Reconciled meeting", created: Date.now() / 1000,
            duration_sec: 65, duration_display: "1:05", segment_count: 0,
            speaker_count: 0, speakers: [], status: "processing", preview: "Recording saved locally",
          });
          return { ok: false, message: "Test save was not confirmed" };
        }
        window.__meetingCapture = { active: false, state: "idle", captured_seconds: 0, audio_level: 0 };
        if (!MOCK.meetings.length) MOCK.meetings.push({
          id: "issue23-saved", title: "Saved keyboard meeting", created: Date.now() / 1000,
          duration_sec: 65, duration_display: "1:05", segment_count: 1,
          speaker_count: 1, speakers: [], status: "processing", preview: "Recording saved locally",
        });
        return { ok: true, processing: true, meeting_id: "issue23-saved" };
      },
    } };
  });
  await current.page.getByRole("button", { name: "Meetings", exact: true }).click();
  await current.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "before");

  const before = await current.page.evaluate(() => ({
    instruments: document.querySelectorAll("#meeting-instrument").length,
    phase: document.querySelector("#meeting-instrument")?.dataset.phase,
    ledger: [...document.querySelectorAll("#meeting-context-ledger [data-context]")]
      .map(node => [node.dataset.context, node.querySelector("dd")?.textContent.trim()]),
    cancel: [...document.querySelectorAll("button")]
      .some(node => /cancel processing/i.test(node.textContent)),
  }));
  assert.equal(before.instruments, 1, "Meetings must use one transforming instrument");
  assert.equal(before.phase, "before");
  assert.deepEqual(before.ledger.map(row => row[0]), ["microphone", "saved-location", "transcription", "analysis"]);
  assert.ok(before.ledger.every(row => row[1]), `every Meetings context fact needs visible truth: ${JSON.stringify(before.ledger)}`);
  assert.equal(before.cancel, false, "processing Cancel must stay absent without safe cancellation semantics");
  const analysisTruth = await current.page.evaluate(() => ({
    local: meetingAnalysisTruth({ ready: true, effective_route: "local", reason: "local_provider" }),
    blocked: ["device_only", "hosted_processing_off", "missing_key", "unsupported_provider"]
      .map(reason => meetingAnalysisTruth({ ready: false, effective_route: "local", reason })),
  }));
  assert.match(analysisTruth.local, /on-device analysis.*stays on this device/i);
  assert.ok(analysisTruth.blocked.every(value => /^Unavailable/.test(value)),
    `blocked effective routes must remain unavailable: ${JSON.stringify(analysisTruth.blocked)}`);

  await current.page.getByRole("button", { name: "Record meeting", exact: true }).focus();
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "during");
  const during = await current.page.evaluate(() => {
    const instrument = document.querySelector("#meeting-instrument");
    const level = document.querySelector("#meeting-input-level");
    return {
      phase: instrument?.dataset.phase,
      recording: document.querySelector("#meeting-rec-state")?.textContent.trim(),
      timer: document.querySelector("#meeting-rec-timer")?.textContent.trim(),
      level: level?.getAttribute("aria-valuenow"),
      microphone: document.querySelector('[data-context="microphone"] dd')?.textContent.trim(),
      saved: document.querySelector('[data-context="saved-location"] dd')?.textContent.trim(),
      pause: document.querySelector("#meeting-pause")?.textContent.trim(),
      stop: document.querySelector("#meeting-stop")?.textContent.trim(),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      meterAnimation: getComputedStyle(document.querySelector("#meeting-level-fill")).animationName,
      liveContainsMeter: document.querySelector(".meeting-live-state")?.contains(level),
    };
  });
  assert.equal(during.phase, "during");
  assert.equal(during.recording, "Recording");
  assert.match(during.timer, /^\d+:\d{2}$/);
  assert.match(during.level, /^\d+$/);
  assert.ok(during.microphone.length > 0 && during.saved.length > 0);
  assert.match(during.pause, /Pause/);
  assert.match(during.stop, /Stop.*save/i);
  assert.ok(during.overflow <= 1, `Meetings overflowed by ${during.overflow}px`);
  assert.equal(during.meterAnimation, "none");
  assert.equal(during.liveContainsMeter, false, "continuous level changes must stay outside the polite live region");
  const frozenMicrophone = await current.page.evaluate(() => {
    meetingApplyContext({ ok: true, microphone: "Later Settings microphone" });
    return document.querySelector('[data-context="microphone"] dd')?.textContent.trim();
  });
  assert.equal(frozenMicrophone, "Conference microphone",
    "Settings changes must not rewrite the effective microphone for an active meeting");

  await current.page.waitForTimeout(1600);
  await current.page.locator("#meeting-pause").focus();
  const focusBeforeStatus = await current.page.evaluate(() => ({
    id: document.activeElement?.id, disabled: document.querySelector("#meeting-pause")?.disabled,
    panelHidden: document.querySelector("#meeting-recording")?.hidden,
  }));
  await current.page.evaluate(() => {
    meetingApplyCaptureStatus({
      ok: true, active: true, state: "recording", recording: true, paused: false,
      captured_seconds: 65, audio_level: 0.42, max_seconds: 14400,
    });
    clearInterval(MEET.recTimer);
    clearTimeout(MEET.statusTimer);
    MEET.recTimer = null;
    MEET.statusTimer = null;
  });
  const focusAfterStatus = await current.page.evaluate(() => ({
    id: document.activeElement?.id, disabled: document.querySelector("#meeting-pause")?.disabled,
    panelHidden: document.querySelector("#meeting-recording")?.hidden,
  }));
  assert.equal(
    focusAfterStatus.id === "meeting-pause",
    true,
    `authoritative recording updates must not steal focus: ${JSON.stringify({ focusBeforeStatus, focusAfterStatus })}`,
  );
  const first = await current.page.screenshot();
  const second = await current.page.screenshot();
  assert.ok(first.equals(second), "Meetings During screenshot must be deterministic");
  await current.page.keyboard.press("Space");
  await current.page.waitForFunction(() => document.querySelector("#meeting-recording")?.dataset.state === "paused");
  assert.match(await current.page.locator("#meeting-pause").innerText(), /Resume/);
  await current.page.locator("#meeting-pause").focus();
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() => document.querySelector("#meeting-recording")?.dataset.state === "recording");
  await current.page.locator("#meeting-stop").focus();
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() => !document.querySelector("#meeting-stop")?.disabled);
  assert.equal(await current.page.evaluate(() => document.activeElement?.id), "meeting-stop",
    "a rejected keyboard Stop must restore focus to the retryable Stop action");
  assert.equal(await current.page.evaluate(() => window.__meetingStopCalls), 1);
  const beforeInactiveReconciliation = await current.page.evaluate(() => {
    clearTimeout(MEET.statusTimer);
    MEET.statusTimer = null;
    window.__deferMeetingStatus = true;
    meetingScheduleStatusPoll(true);
    const instrument = document.querySelector("#meeting-instrument");
    window.__meetingAfterTransitions = 0;
    window.__meetingPhaseObserver = new MutationObserver(() => {
      if (instrument.dataset.phase === "after") window.__meetingAfterTransitions += 1;
    });
    window.__meetingPhaseObserver.observe(instrument, { attributes: true, attributeFilter: ["data-phase"] });
    return { listCalls: window.__meetingListCalls };
  });
  await current.page.waitForFunction(() => window.__meetingStatusPending === true);
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() =>
    document.querySelector("#meeting-instrument")?.dataset.phase === "after" &&
    document.activeElement?.id === "meeting-library-title");
  assert.equal(await current.page.evaluate(() => document.activeElement?.id), "meeting-library-title",
    "inactive reconciliation must move focus to visible content, not hidden Stop");
  const inactiveReconciliation = await current.page.evaluate(async () => {
    const release = window.__releaseMeetingStatus;
    window.__releaseMeetingStatus = null;
    release();
    await new Promise(resolve => setTimeout(resolve, 700));
    window.__meetingPhaseObserver.disconnect();
    return {
      phase: document.querySelector("#meeting-instrument")?.dataset.phase,
      listRefreshes: window.__meetingListCalls,
      afterTransitions: window.__meetingAfterTransitions,
      pollingRestarted: Boolean(MEET.statusTimer),
      recording: MEET.recording,
      focus: document.activeElement?.id,
    };
  });
  assert.equal(inactiveReconciliation.phase, "after", "a stale pre-Stop status reply must not restore During");
  assert.equal(inactiveReconciliation.recording, false, "a stale pre-Stop status reply must remain rejected");
  assert.equal(inactiveReconciliation.pollingRestarted, false, "a stale pre-Stop reply must not restart polling");
  assert.equal(inactiveReconciliation.focus, "meeting-library-title");
  assert.equal(inactiveReconciliation.listRefreshes, beforeInactiveReconciliation.listCalls + 1,
    "inactive reconciliation must refresh the meeting library exactly once");
  assert.equal(inactiveReconciliation.afterTransitions, 1,
    "inactive reconciliation must enter the After state exactly once");
  await current.page.getByRole("button", { name: "Record meeting", exact: true }).focus();
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "during");
  await current.page.locator("#meeting-stop").focus();
  await current.page.keyboard.press("Enter");
  await current.page.keyboard.press("Enter");
  await current.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "after");
  assert.equal(await current.page.evaluate(() => window.__meetingStopCalls), 3,
    "repeated keyboard activation must not add a second successful finalisation");
  await current.page.waitForFunction(() => document.activeElement?.id === "meeting-library-title");
  await current.page.close();

  const narrow = await openProductionPage(browser, {
    viewport: { width: 430, height: 900 }, reducedMotion: "reduce",
  });
  assert.equal(narrow.browserErrors.length, 0, narrow.browserErrors.join(" | "));
  await narrow.page.evaluate(() => { MOCK.meetings = []; });
  await narrow.page.getByRole("button", { name: "Meetings", exact: true }).click();
  const narrowTruth = await narrow.page.evaluate(() => ({
    columns: getComputedStyle(document.querySelector("#meeting-context-ledger")).gridTemplateColumns.split(" ").length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  assert.equal(narrowTruth.columns, 1, "the Meetings ledger must reflow to one column when narrow");
  assert.ok(narrowTruth.overflow <= 1, `narrow Meetings overflowed by ${narrowTruth.overflow}px`);
  await narrow.page.close();
  record("Meetings instrument, context truth, capture controls, focus, reflow, reduced motion, and deterministic screenshot");
}

function contrastRatio(first, second) {
  const linear = value => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  };
  const luminance = color => 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2]);
  const light = Math.max(luminance(first), luminance(second));
  const dark = Math.min(luminance(first), luminance(second));
  return (light + 0.05) / (dark + 0.05);
}

function rgb(value) {
  const match = String(value).match(/[\d.]+/g);
  return match ? match.slice(0, 3).map(Number) : [0, 0, 0];
}

function pngDimensions(buffer) {
  assert.ok(buffer.subarray(1, 4).equals(Buffer.from("PNG")), "expected a PNG screenshot");
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function removeTemporaryProfile(profileRoot) {
  const resolved = path.resolve(profileRoot);
  const expectedParent = `${path.resolve(os.tmpdir())}${path.sep}`;
  assert.ok(
    resolved.startsWith(expectedParent) && path.basename(resolved).startsWith("mumble-focus-stage-zoom-"),
    `refusing to remove unexpected browser profile: ${resolved}`,
  );
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 3 });
}

async function testBrowserZoom() {
  const profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mumble-focus-stage-zoom-"));
  let context;
  try {
    // Playwright's documented extension path uses its full Chromium channel.
    // The extension then calls Chrome's browser-owned tabs.setZoom API; no CSS
    // transform, document zoom, or narrow-viewport substitute is involved.
    context = await chromium.launchPersistentContext(profileRoot, {
      channel: "chromium",
      headless: true,
      viewport: { width: 1440, height: 1000 },
      colorScheme: "dark",
      args: [
        `--disable-extensions-except=${zoomExtensionRoot}`,
        `--load-extension=${zoomExtensionRoot}`,
      ],
    });
    let [serviceWorker] = context.serviceWorkers();
    if (!serviceWorker) serviceWorker = await context.waitForEvent("serviceworker");
    const page = context.pages()[0] || await context.newPage();
    const browserErrors = [];
    page.on("pageerror", error => browserErrors.push(error.message));
    page.on("console", message => {
      if (message.type() === "error") browserErrors.push(message.text());
    });
    await page.goto(`${entry}?ui-test=1`);
    await page.waitForFunction(() => window.__mumbleBooted === true);
    await page.waitForTimeout(600);
    assert.equal(browserErrors.length, 0, browserErrors.join(" | "));

    const before = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
      documentZoom: getComputedStyle(document.documentElement).zoom,
    }));
    const physicalBefore = pngDimensions(await page.screenshot());
    const browserZoom = await serviceWorker.evaluate(async () => {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (!tab || tab.id == null) throw new Error("The production tab was not available to the zoom driver.");
      await chrome.tabs.setZoomSettings(tab.id, { mode: "automatic", scope: "per-tab" });
      await chrome.tabs.setZoom(tab.id, 2);
      return {
        factor: await chrome.tabs.getZoom(tab.id),
        settings: await chrome.tabs.getZoomSettings(tab.id),
      };
    });
    await page.waitForFunction(
      width => window.innerWidth <= width / 2,
      before.viewportWidth,
    );
    const after = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
      documentZoom: getComputedStyle(document.documentElement).zoom,
      inlineDocumentZoom: document.documentElement.style.zoom,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    const physicalAfter = pngDimensions(await page.screenshot());

    assert.equal(browserZoom.factor, 2, "Chromium must report a 200% tab zoom factor");
    assert.equal(browserZoom.settings.mode, "automatic", "the browser, not page CSS, must own zoom scaling");
    assert.equal(after.inlineDocumentZoom, "", "the browser zoom probe must not apply document CSS zoom");
    assert.equal(after.documentZoom, before.documentZoom, "document CSS zoom must remain unchanged");
    assert.ok(
      Math.abs(after.viewportWidth - before.viewportWidth / 2) <= 1,
      `200% browser zoom must halve the effective CSS width: ${JSON.stringify({ before, after })}`,
    );
    assert.ok(
      after.devicePixelRatio >= before.devicePixelRatio * 2,
      `200% browser zoom must double effective pixel scale: ${JSON.stringify({ before, after })}`,
    );
    assert.deepEqual(physicalAfter, physicalBefore, "the physical browser surface must remain fixed while CSS layout scale changes");
    assert.ok(after.overflow <= 1, `200% browser zoom must not cause horizontal overflow (${after.overflow}px)`);

    const navRows = await page.locator(".nav-btn").evaluateAll(nodes =>
      new Set(nodes.map(node => Math.round(node.getBoundingClientRect().top))).size);
    assert.equal(navRows, 2, "200% browser zoom must reflow the six destinations to two rows");
    const navOverlap = await page.locator(".nav-btn").evaluateAll(nodes => {
      const boxes = nodes.map(node => node.getBoundingClientRect());
      return boxes.some((box, index) => boxes.slice(index + 1).some(other =>
        box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top));
    });
    assert.equal(navOverlap, false, "zoomed destination controls must not overlap");

    await page.evaluate(() => reflectStatus({
      live: true,
      recording: true,
      state: "listening",
      text: "Listening at 200% browser zoom",
    }));
    const importantSelectors = [
      ".centred-destination-grid",
      ".focus-stage",
      "#home-live-state .state-surface",
      "#home-dictation-actions",
    ];
    for (const selector of importantSelectors) {
      const locator = page.locator(selector);
      await locator.scrollIntoViewIfNeeded();
      const geometry = await locator.evaluate(node => {
        const box = node.getBoundingClientRect();
        return {
          visible: box.width > 0 && box.height > 0,
          clippedHorizontally: box.left < -1 || box.right > window.innerWidth + 1,
        };
      });
      assert.equal(geometry.visible, true, `${selector} must remain visible at 200% browser zoom`);
      assert.equal(geometry.clippedHorizontally, false, `${selector} must not be horizontally clipped at 200% browser zoom`);
    }
    for (const destination of ["Stats", "Reader", "Meetings"]) {
      await page.getByRole("button", { name: destination, exact: true }).click();
      await page.waitForTimeout(180);
      const destinationGeometry = await page.locator('.view:not([hidden])').evaluate(node => ({
        destination: node.dataset.view,
        visible: node.getBoundingClientRect().width > 0,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      assert.equal(destinationGeometry.visible, true, `${destination} must remain visible at 200% browser zoom`);
      assert.ok(destinationGeometry.overflow <= 1, `${destination} must not overflow at 200% browser zoom`);
      if (destination === "Meetings") {
        await page.evaluate(() => {
          meetingApplyCaptureStatus({
            ok: true, active: true, state: "recording", recording: true, paused: false,
            captured_seconds: 65, audio_level: 0.42, max_seconds: 14400,
          });
          clearInterval(MEET.recTimer);
          clearTimeout(MEET.statusTimer);
          MEET.recTimer = null;
          MEET.statusTimer = null;
        });
        const instrument = await page.locator("#meeting-instrument").evaluate(node => {
          const box = node.getBoundingClientRect();
          const stop = document.querySelector("#meeting-stop").getBoundingClientRect();
          return {
            phase: node.dataset.phase,
            visible: box.width > 0 && box.height > 0,
            clippedHorizontally: box.left < -1 || box.right > window.innerWidth + 1,
            stopHeight: stop.height,
          };
        });
        assert.equal(instrument.visible, true, "the Meetings instrument must remain visible at genuine 200% zoom");
        assert.equal(instrument.clippedHorizontally, false, "the Meetings instrument must not clip at genuine 200% zoom");
        assert.ok(instrument.stopHeight >= 40, `the zoomed Meetings Stop target must remain operable: ${JSON.stringify(instrument)}`);
      }
    }
    await page.getByRole("button", { name: "Home", exact: true }).click();
    const coreControls = await page.locator(".nav-btn, #record-btn, #open-history-btn").evaluateAll(nodes =>
      nodes.filter(node => !node.hidden).map(node => ({
        label: node.innerText.trim().replace(/\s+/g, " "),
        width: node.getBoundingClientRect().width,
        height: node.getBoundingClientRect().height,
      })));
    assert.equal(coreControls.some(control => !control.label), false, "zoomed controls must retain readable labels");
    assert.deepEqual(
      coreControls.filter(control => control.width < 40 || control.height < 40),
      [],
      `zoomed core controls must remain operable: ${JSON.stringify(coreControls)}`,
    );

    await page.locator("#record-btn").focus();
    await page.evaluate(() => {
      window.__foundationZoomModal = confirmModal({
        title: "Zoom check",
        body: "This browser-only check does not change saved data.",
        confirmText: "Continue",
        danger: false,
      });
    });
    const dialog = page.getByRole("dialog", { name: "Zoom check" });
    await dialog.waitFor();
    const dialogGeometry = await dialog.evaluate(node => {
      const box = node.getBoundingClientRect();
      return {
        left: box.left,
        right: box.right,
        top: box.top,
        bottom: box.bottom,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    assert.ok(dialogGeometry.left >= -1 && dialogGeometry.right <= dialogGeometry.viewportWidth + 1,
      `zoomed modal must not be horizontally clipped: ${JSON.stringify(dialogGeometry)}`);
    assert.ok(dialogGeometry.top >= -1 && dialogGeometry.bottom <= dialogGeometry.viewportHeight + 1,
      `zoomed modal must remain on screen: ${JSON.stringify(dialogGeometry)}`);
    assert.equal(await dialog.getByRole("button", { name: "Cancel" }).evaluate(node => node === document.activeElement), true,
      "zoomed modal focus must remain visible and contained");
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    assert.equal(await page.locator("#record-btn").evaluate(node => node === document.activeElement), true,
      "zoomed modal must restore focus to the invoking control");

    return {
      browserZoom,
      before,
      after,
      physicalBefore,
      physicalAfter,
      navRows,
      coreControls: coreControls.length,
    };
  } finally {
    if (context) await context.close();
    removeTemporaryProfile(profileRoot);
  }
}

async function testReflow(browser) {
  const desktop = await openProductionPage(browser, { viewport: { width: 1440, height: 1000 } });
  assert.equal(desktop.browserErrors.length, 0, desktop.browserErrors.join(" | "));
  await desktop.page.getByRole("button", { name: "Home", exact: true }).click();
  await desktop.page.waitForTimeout(250);
  const navBox = await desktop.page.locator(".centred-destination-grid").boundingBox();
  assert.ok(Math.abs(navBox.x - (1440 - navBox.width) / 2) < 1, "desktop navigation must be centred");
  assert.equal(await desktop.page.locator('.nav-btn.active[aria-current="page"]').count(), 1, "visual and programmatic current destination must agree");
  const activeColors = await desktop.page.evaluate(() => {
    const node = document.querySelector('.nav-btn.active[aria-current="page"]');
    node.style.transition = "none";
    const rootStyle = getComputedStyle(document.documentElement);
    const probe = document.createElement("span");
    document.body.append(probe);
    const resolved = value => {
      probe.style.color = value;
      return getComputedStyle(probe).color;
    };
    const colors = {
      gold: resolved(rootStyle.getPropertyValue("--gold")),
      goldHigh: resolved(rootStyle.getPropertyValue("--gold-hi")),
      dark: resolved(rootStyle.getPropertyValue("--bg")),
      darkHigh: resolved(rootStyle.getPropertyValue("--elevated-2")),
      treatments: [],
    };
    for (const [publicTier, legacyTier] of [["light", "lite"], ["standard", "standard"], ["full", "enhanced"]]) {
      window.MumbleUIFoundation.applyEffectsTier(publicTier);
      applyEffects(legacyTier);
      colors.treatments.push({ tier: publicTier, foreground: getComputedStyle(node).color });
    }
    probe.remove();
    return colors;
  });
  for (const treatment of activeColors.treatments) {
    const backgrounds = treatment.tier === "full"
      ? [activeColors.dark, activeColors.darkHigh]
      : [activeColors.gold, activeColors.goldHigh];
    const ratios = backgrounds.map(background => contrastRatio(rgb(treatment.foreground), rgb(background)));
    assert.ok(Math.min(...ratios) >= 4.5, `${treatment.tier} active destination contrast failed: ${JSON.stringify({ treatment, backgrounds, ratios })}`);
  }
  await desktop.page.close();

  const zoomEvidence = await testBrowserZoom();

  for (const viewport of [{ width: 640, height: 900 }, { width: 430, height: 900 }]) {
    const current = await openProductionPage(browser, { viewport });
    assert.equal(current.browserErrors.length, 0, current.browserErrors.join(" | "));
    const grid = await current.page.locator(".centred-destination-grid").evaluate(node => getComputedStyle(node).gridTemplateColumns.split(" ").length);
    assert.equal(grid, 3, `${viewport.width}px must use a three-column destination grid`);
    const rows = await current.page.locator(".nav-btn").evaluateAll(nodes => new Set(nodes.map(node => Math.round(node.getBoundingClientRect().top))).size);
    assert.equal(rows, 2, `${viewport.width}px must render two navigation rows`);
    const overflow = await current.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(overflow <= 1, `${viewport.width}px must not scroll horizontally (overflow ${overflow}px)`);
    const targetSizes = await current.page.locator(".nav-btn, #record-btn, #open-history-btn").evaluateAll(nodes => nodes.filter(node => !node.hidden).map(node => ({ label: node.innerText.trim(), width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })));
    const undersized = targetSizes.filter(target => target.width < 40 || target.height < 40);
    assert.deepEqual(undersized, [], `core targets smaller than 40px: ${JSON.stringify(undersized)}`);
    await current.page.close();
  }
  record("desktop centring, genuine 200% browser zoom, narrow 3x2 navigation, targets, and contrast", zoomEvidence);
}

async function stableScreenshot(page, filename, options = {}) {
  await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
  await page.waitForTimeout(100);
  const first = await page.screenshot(options);
  const second = await page.screenshot(options);
  assert.ok(first.equals(second), `${filename} was not deterministic across identical captures`);
  const evidencePath = path.join(outputRoot, filename);
  const freshHash = sha256(first);
  if (capture) {
    fs.writeFileSync(evidencePath, first);
    screenshotHashes[filename] = freshHash;
  } else {
    assert.ok(fs.existsSync(evidencePath), `${filename} is missing; regenerate the committed evidence with -Capture`);
    const committed = fs.readFileSync(evidencePath);
    const committedHash = sha256(committed);
    assert.equal(
      committedHash,
      expectedEvidence.screenshots[filename],
      `${filename} does not match its source-bound evidence manifest; regenerate with -Capture`,
    );
    assert.deepEqual(
      pngDimensions(first),
      pngDimensions(committed),
      `${filename} dimensions drifted from the source-bound evidence; regenerate with -Capture`,
    );
    screenshotHashes[filename] = committedHash;
  }
  return first;
}

async function testVisual(browser) {
  if (!capture) expectedEvidence = loadAndValidateEvidenceManifest(browser.version());
  const reduced = await openProductionPage(browser, { reducedMotion: "reduce" });
  assert.equal(reduced.browserErrors.length, 0, reduced.browserErrors.join(" | "));
  await reduced.page.evaluate(() => {
    const probe = window.MumbleUIFoundation.createStateSurface("loading");
    probe.id = "reduced-motion-state-probe";
    document.querySelector("#main-content").prepend(probe);
  });
  const motion = await reduced.page.evaluate(() => ({
    wave: getComputedStyle(document.querySelector("#hero-waveform .wb")).animationName,
    busy: getComputedStyle(document.querySelector('.state-surface[aria-busy="true"]'), "::before").animationName,
    navTransition: getComputedStyle(document.querySelector(".nav-btn")).transitionDuration,
    status: document.querySelector("#status-text").textContent.trim(),
  }));
  assert.equal(motion.wave, "none");
  assert.equal(motion.busy, "none");
  assert.equal(motion.navTransition, "0s");
  assert.ok(motion.status.length > 0, "status must remain understandable without motion");
  await reduced.page.locator("#reduced-motion-state-probe").evaluate(node => node.remove());

  const fingerprints = [];
  for (const tier of ["light", "standard", "full"]) {
    fingerprints.push(await reduced.page.evaluate(tierName => {
      window.MumbleUIFoundation.applyEffectsTier(tierName);
      const nav = document.querySelector(".centred-destination-grid").getBoundingClientRect();
      const stage = document.querySelector(".focus-stage").getBoundingClientRect();
      return {
        tier: tierName,
        labels: [...document.querySelectorAll(".nav-btn")].map(node => node.textContent.trim()),
        nav: [nav.x, nav.y, nav.width, nav.height],
        stage: [stage.x, stage.y, stage.width, stage.height],
        actions: [...document.querySelectorAll("button")].filter(node => !node.hidden).length,
        focusRule: getComputedStyle(document.querySelector(".nav-btn")).outlineOffset,
      };
    }, tier));
  }
  const comparable = fingerprints.map(({ tier, ...fingerprint }) => fingerprint);
  assert.deepEqual(comparable[1], comparable[0], "Standard effects changed information or layout");
  assert.deepEqual(comparable[2], comparable[0], "Full effects changed information or layout");

  const rails = await reduced.page.evaluate(() => {
    const api = window.MumbleUIFoundation;
    return {
      listening: api.describeControlRail({ phase: "listening", mode: "Prompt", language: "English", deckAvailable: true }),
      unsafe: api.describeControlRail({ phase: "processing", cancellable: false }),
      safe: api.describeControlRail({ phase: "processing", cancellable: true }),
    };
  });
  assert.deepEqual(rails.listening.controls.map(control => control.id), ["mode-language", "deck", "stop"]);
  assert.equal(rails.listening.controls.at(-1).minimumTargetPx, 36);
  assert.equal(rails.unsafe.controls.some(control => control.id === "cancel"), false);
  assert.equal(rails.safe.controls.find(control => control.id === "cancel").label, "Cancel processing");

  const productionState = await reduced.page.evaluate(() => {
    const record = document.querySelector("#record-btn");
    record.click();
    const surface = document.querySelector("#home-live-state .state-surface");
    return {
      state: surface?.dataset.state,
      rail: document.querySelector("#home-dictation-actions")?.classList.contains("control-rail"),
      stopControl: record.dataset.control,
    };
  });
  assert.equal(productionState.state, "loading", "the real dictation workflow must expose a loading State Surface while recording");
  assert.equal(productionState.rail, true, "the real dictation actions must be a Control Rail");
  assert.equal(productionState.stopControl, "stop", "the active dictation action must be identified as the time-critical stop control");
  await reduced.page.locator("#record-btn").click();
  await reduced.page.waitForTimeout(180);
  await reduced.page.evaluate(() => document.querySelector("#toast-wrap").replaceChildren());

  const effectsContract = await reduced.page.evaluate(() => {
    applyEffects("lite");
    return document.documentElement.dataset.effectsTier;
  });
  assert.equal(effectsContract, "light", "the existing effects setting must exercise the public Focus Stage tier contract");
  const restoredEffects = await reduced.page.evaluate(() => {
    applyEffects("enhanced");
    return document.documentElement.dataset.effectsTier;
  });
  assert.equal(restoredEffects, "full", "the visual evidence must restore the production default effects tier");

  if (capture) fs.mkdirSync(outputRoot, { recursive: true });
  await stableScreenshot(reduced.page, "01-focus-stage-home-desktop.png");
  await reduced.page.getByRole("button", { name: "Meetings", exact: true }).click();
  await reduced.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "after");
  await stableScreenshot(reduced.page, "07-meetings-after.png");
  await reduced.page.evaluate(async () => {
    MOCK.meetings = [];
    MEET.listAll = null;
    await renderMeetings();
  });
  await reduced.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "before");
  await stableScreenshot(reduced.page, "05-meetings-before.png");
  await reduced.page.evaluate(() => {
    meetingApplyCaptureStatus({
      ok: true, active: true, state: "recording", recording: true, paused: false,
      captured_seconds: 65, audio_level: 0.42, max_seconds: 14400,
    });
    clearInterval(MEET.recTimer);
    clearTimeout(MEET.statusTimer);
    MEET.recTimer = null;
    MEET.statusTimer = null;
  });
  await stableScreenshot(reduced.page, "06-meetings-during.png");
  await reduced.page.evaluate(() => {
    document.querySelectorAll(".view, .titlebar, .navbar").forEach(node => { node.hidden = true; });
    document.querySelector("#toast-wrap").replaceChildren();
    const gallery = document.createElement("main");
    gallery.className = "view";
    gallery.style.display = "grid";
    gallery.style.gap = "12px";
    for (const kind of ["loading", "empty", "degraded", "error", "success"]) {
      gallery.append(window.MumbleUIFoundation.createStateSurface(kind, {
        content: `Preserved ${kind} content`,
        action: ["degraded", "error"].includes(kind) ? { label: "Try again" } : null,
      }));
    }
    document.querySelector("#app").append(gallery);
  });
  await stableScreenshot(reduced.page, "03-shared-state-surfaces.png");
  await reduced.page.close();

  const forced = await openProductionPage(browser, { forcedColors: "active" });
  assert.equal(forced.browserErrors.length, 0, forced.browserErrors.join(" | "));
  await forced.page.getByRole("button", { name: "Meetings", exact: true }).click();
  await forced.page.waitForFunction(() => document.querySelector("#meeting-instrument")?.dataset.phase === "after");
  const forcedButton = forced.page.getByRole("button", { name: "Record meeting", exact: true });
  await forcedButton.focus();
  await forced.page.keyboard.press("Shift+Tab");
  await forced.page.keyboard.press("Tab");
  assert.equal(await forcedButton.evaluate(node => node === document.activeElement), true,
    "forced-colour verification must keyboard-focus the Meetings Record control");
  const forcedStyle = await forced.page.evaluate(() => ({
    buttonBorder: getComputedStyle(document.querySelector("#meeting-record")).borderStyle,
    buttonOutline: getComputedStyle(document.querySelector("#meeting-record")).outlineStyle,
    instrumentBorder: getComputedStyle(document.querySelector("#meeting-instrument")).borderStyle,
    phase: document.querySelector("#meeting-instrument").dataset.phase,
  }));
  assert.equal(forcedStyle.phase, "after");
  assert.equal(forcedStyle.instrumentBorder, "solid");
  assert.equal(forcedStyle.buttonBorder, "solid");
  assert.equal(forcedStyle.buttonOutline, "solid");
  await stableScreenshot(forced.page, "04-forced-colours-focus.png");
  await forced.page.close();

  const narrow = await openProductionPage(browser, { viewport: { width: 430, height: 900 }, reducedMotion: "reduce" });
  assert.equal(narrow.browserErrors.length, 0, narrow.browserErrors.join(" | "));
  await stableScreenshot(narrow.page, "02-focus-stage-navigation-narrow.png");
  await narrow.page.close();

  const deck = await openProductionPage(browser, { reducedMotion: "reduce" });
  assert.equal(deck.browserErrors.length, 0, deck.browserErrors.join(" | "));
  await deck.page.getByRole("button", { name: "Deck", exact: true }).click();
  await deck.page.waitForTimeout(180);
  await deck.page.locator('[data-content-filter="clipboard"]').click();
  await deck.page.waitForTimeout(80);
  await stableScreenshot(deck.page, "05-deck-command-surface.png");
  await deck.page.close();

  const deckNarrow = await openProductionPage(browser, {
    viewport: { width: 430, height: 900 }, reducedMotion: "reduce",
  });
  assert.equal(deckNarrow.browserErrors.length, 0, deckNarrow.browserErrors.join(" | "));
  await deckNarrow.page.getByRole("button", { name: "Deck", exact: true }).click();
  await deckNarrow.page.waitForTimeout(180);
  const selectedRow = deckNarrow.page.locator(".deck-selectable-row").first();
  await selectedRow.focus();
  await selectedRow.press("Space");
  await stableScreenshot(deckNarrow.page, "06-deck-selection-narrow.png");
  await deckNarrow.page.close();
  if (capture) writeEvidenceManifest(browser.version());
  record("effect invariance, reduced motion, Meetings forced colours, Control Rail safety, and deterministic screenshots");
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    if (requestedCase === "all" || requestedCase === "shell") await testShell(browser);
    if (requestedCase === "all" || requestedCase === "states") await testStates(browser);
    if (requestedCase === "all" || requestedCase === "keyboard") await testKeyboard(browser);
    if (requestedCase === "all" || requestedCase === "deck") await testDeck(browser);
    if (requestedCase === "all" || requestedCase === "stats-reader") await testStatsReader(browser);
    if (requestedCase === "all" || requestedCase === "meetings") await testMeetings(browser);
    if (requestedCase === "all" || requestedCase === "reflow") await testReflow(browser);
    if (requestedCase === "zoom") record("genuine 200% browser zoom probe", await testBrowserZoom());
    if (requestedCase === "all" || requestedCase === "visual") await testVisual(browser);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ ok: true, checks: results.length, results, capture, outputRoot, screenshotHashes }, null, 2));
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
