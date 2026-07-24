const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const webuiRoot = path.join(repoRoot, "Internal", "app", "webui");
const entry = pathToFileURL(path.join(webuiRoot, "index.html")).href;
const requestedCase = (process.argv.find(arg => arg.startsWith("--case=")) || "").split("=")[1] || "all";
const capture = process.argv.includes("--capture");
const outputRoot = path.join(repoRoot, "Development Files", "Research", "focus-stage-production-screenshots");
const results = [];

function record(name, details = "passed") {
  results.push({ name, details });
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
  assert.equal(await page.locator(".context-ledger").count(), 1, "the Home shortcut panel must use the optional context ledger primitive");
  assert.equal(await page.locator(".numbered-spine").count(), 1, "the genuine Home sequence must use the numbered spine primitive");
  assert.equal(await page.locator(".command-surface").count(), 1, "the existing Deck toolbar must use the command surface primitive");
  assert.equal(await page.locator(".focus-stage .control-rail").count(), 1, "the production dictation actions must use the Control Rail primitive");
  assert.equal(await page.locator(".focus-stage .btn-gold").count(), 1, "the Focus Stage must expose one primary action");
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
  record("stable loading, empty, degraded, error, and success anatomy");
  await page.close();
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

  const zoomed = await openProductionPage(browser, { viewport: { width: 1440, height: 1000 } });
  assert.equal(zoomed.browserErrors.length, 0, zoomed.browserErrors.join(" | "));
  const zoomEvidence = await zoomed.page.evaluate(() => {
    document.documentElement.style.zoom = "200%";
    return {
      requestedZoom: document.documentElement.style.zoom,
      computedZoom: getComputedStyle(document.documentElement).zoom,
      viewportWidth: window.innerWidth,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  assert.equal(zoomEvidence.requestedZoom, "200%", "the accessibility check must apply literal 200% zoom");
  assert.equal(zoomEvidence.computedZoom, "2", "the browser must compute the page at 200% zoom");
  assert.equal(zoomEvidence.viewportWidth, 1440, "the 200% check must not substitute a narrow viewport");
  assert.ok(zoomEvidence.overflow <= 1, `200% zoom must reflow without horizontal overflow (${zoomEvidence.overflow}px)`);
  await zoomed.page.close();

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
  record("desktop centring, literal 200% zoom reflow, narrow 3x2 navigation, targets, and contrast");
}

async function stableScreenshot(page, filename, options = {}) {
  await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
  await page.waitForTimeout(100);
  const first = await page.screenshot(options);
  const second = await page.screenshot(options);
  assert.ok(first.equals(second), `${filename} was not deterministic across identical captures`);
  if (capture) fs.writeFileSync(path.join(outputRoot, filename), first);
  return first;
}

async function testVisual(browser) {
  const reduced = await openProductionPage(browser, { reducedMotion: "reduce" });
  assert.equal(reduced.browserErrors.length, 0, reduced.browserErrors.join(" | "));
  await reduced.page.evaluate(() => document.querySelector("#main-content").prepend(window.MumbleUIFoundation.createStateSurface("loading")));
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

  const effectsContract = await reduced.page.evaluate(() => {
    applyEffects("lite");
    return document.documentElement.dataset.effectsTier;
  });
  assert.equal(effectsContract, "light", "the existing effects setting must exercise the public Focus Stage tier contract");

  if (capture) fs.mkdirSync(outputRoot, { recursive: true });
  await stableScreenshot(reduced.page, "01-focus-stage-home-desktop.png");
  await reduced.page.evaluate(() => {
    document.querySelectorAll(".view, .titlebar, .navbar").forEach(node => { node.hidden = true; });
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
  const forcedButton = forced.page.getByRole("button", { name: "Home", exact: true });
  await forcedButton.focus();
  const forcedStyle = await forcedButton.evaluate(node => ({
    border: getComputedStyle(node).borderStyle,
    outline: getComputedStyle(node).outlineStyle,
  }));
  assert.equal(forcedStyle.border, "solid");
  assert.equal(forcedStyle.outline, "solid");
  await stableScreenshot(forced.page, "04-forced-colours-focus.png");
  await forced.page.close();

  const narrow = await openProductionPage(browser, { viewport: { width: 430, height: 900 }, reducedMotion: "reduce" });
  assert.equal(narrow.browserErrors.length, 0, narrow.browserErrors.join(" | "));
  await stableScreenshot(narrow.page, "02-focus-stage-navigation-narrow.png");
  await narrow.page.close();
  record("effect invariance, reduced motion, forced colours, Control Rail safety, and deterministic screenshots");
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    if (requestedCase === "all" || requestedCase === "shell") await testShell(browser);
    if (requestedCase === "all" || requestedCase === "states") await testStates(browser);
    if (requestedCase === "all" || requestedCase === "keyboard") await testKeyboard(browser);
    if (requestedCase === "all" || requestedCase === "reflow") await testReflow(browser);
    if (requestedCase === "all" || requestedCase === "visual") await testVisual(browser);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ ok: true, checks: results.length, results, capture, outputRoot }, null, 2));
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
