const { chromium } = require("playwright");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const root = __dirname;
const entry = pathToFileURL(path.join(root, "index.html")).href;
const screenshots = path.join(root, "screenshots");
const capture = process.argv.includes("--capture");
const results = [];

function check(pass, label, detail = "") {
  results.push({ pass: Boolean(pass), label, detail });
}

function url(query) {
  return `${entry}?${new URLSearchParams({ clean: "1", island: "hidden", ...query })}`;
}

async function inspect(page, query, selector) {
  const errors = [];
  page.removeAllListeners("console");
  page.removeAllListeners("pageerror");
  page.on("console", message => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", error => errors.push(`page: ${error.message}`));
  await page.goto(url(query));
  await page.waitForSelector(selector);
  await page.waitForTimeout(600);
  check(errors.length === 0, `${query.variant}/${query.surface}/${query.state}: no browser errors`, errors.join(" | "));
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark" });

  await inspect(desktop, { variant: "A", surface: "home", state: "ready", selftest: "1" }, ".focus-stage");
  check(await desktop.locator("#selfTestOutput").getAttribute("data-result") === "pass", "built-in contract checks pass");
  check(await desktop.locator(".primary-nav [data-surface]").count() === 6, "exactly six primary destinations");
  check(await desktop.locator('.primary-nav [data-surface="find"]').count() === 0, "Mumble Find stays a command, not navigation");
  check((await desktop.locator("body").evaluate(el => el.scrollWidth <= window.innerWidth)), "desktop has no horizontal overflow");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "01-focus-stage-home-desktop.png"), fullPage: true });

  await inspect(desktop, { variant: "B", surface: "home", state: "ready" }, ".home-dashboard");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "02-centred-workbench-home-desktop.png"), fullPage: true });

  await inspect(desktop, { variant: "C", surface: "home", state: "ready" }, ".home-spine");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "03-guided-spine-home-desktop.png"), fullPage: true });

  await inspect(desktop, { variant: "A", surface: "deck", state: "ready" }, ".deck-item.image-item");
  check(await desktop.getByRole("button", { name: "Paste image into previous app" }).count() === 1, "Deck image has an explicit accessible Paste image action");
  check(await desktop.getByRole("button", { name: "Search the web" }).count() === 1, "Deck keeps explicit Web Search action");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "04-focus-stage-deck-image-action.png"), fullPage: true });

  await inspect(desktop, { variant: "B", surface: "meetings", state: "recording" }, ".meeting-live");
  check(await desktop.getByRole("button", { name: "Stop & save" }).count() === 1, "Meetings recording exposes Stop and save");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "05-workbench-meetings-recording.png"), fullPage: true });

  await inspect(desktop, { variant: "A", surface: "settings", state: "ready" }, ".route-truth");
  check(await desktop.getByText("Speech to text", { exact: true }).count() >= 1, "Settings names Speech to text");
  check(await desktop.getByText("Text shaping", { exact: true }).count() >= 1, "Settings names Text shaping");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "06-focus-stage-settings-route-truth.png"), fullPage: true });

  for (const state of ["loading", "empty", "error"]) {
    await inspect(desktop, { variant: "C", surface: "find", state }, ".find-dialog");
    check(await desktop.locator(".find-results").count() === 1, `Mumble Find ${state} keeps its stable result region`);
    check(await desktop.getByText("Web Search", { exact: true }).count() === 0, `Mumble Find ${state} does not insert Web Search fallback`);
    if (capture) await desktop.screenshot({ path: path.join(screenshots, `07-find-${state}.png`), fullPage: true });
  }

  await inspect(desktop, { variant: "B", surface: "find", state: "loading", motion: "reduce" }, ".find-loading");
  check(await desktop.locator("body").evaluate(el => el.classList.contains("reduce-motion")), "reduced-motion mode is applied");
  check(await desktop.locator(".skeleton-line").first().evaluate(el => getComputedStyle(el).animationName === "none"), "reduced-motion loading is static");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "08-find-loading-reduced-motion.png"), fullPage: true });

  await inspect(desktop, { variant: "A", surface: "home", state: "ready", focus: "primary" }, ".record-orb");
  await desktop.waitForTimeout(50);
  check(await desktop.locator(".record-orb").evaluate(el => el === document.activeElement), "primary keyboard focus is placed deliberately");
  check(await desktop.locator(".record-orb").evaluate(el => getComputedStyle(el).boxShadow !== "none"), "focused control has a visible ring");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "09-focus-stage-keyboard-focus.png"), fullPage: true });

  const narrow = await browser.newPage({ viewport: { width: 430, height: 900 }, colorScheme: "dark" });
  await inspect(narrow, { variant: "C", surface: "home", state: "ready" }, ".home-spine");
  check(await narrow.locator(".primary-nav").evaluate(el => getComputedStyle(el).gridTemplateColumns.split(" ").length === 3), "narrow navigation reflows to three columns");
  check(await narrow.locator("body").evaluate(el => el.scrollWidth <= window.innerWidth), "narrow layout has no horizontal overflow");
  if (capture) await narrow.screenshot({ path: path.join(screenshots, "10-guided-spine-home-narrow.png"), fullPage: true });

  await inspect(desktop, { variant: "C", surface: "home", state: "processing", island: "shown" }, ".island-demo.processing");
  check(await desktop.getByRole("button", { name: "Cancel text shaping" }).count() === 1, "processing Island labels Cancel explicitly");
  if (capture) await desktop.screenshot({ path: path.join(screenshots, "11-guided-spine-island-cancel.png"), fullPage: true });

  await browser.close();
  const failed = results.filter(result => !result.pass);
  console.log(JSON.stringify({ ok: failed.length === 0, checks: results.length, failed }, null, 2));
  process.exitCode = failed.length ? 1 : 0;
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
