import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { release as osRelease, version as osVersion } from 'node:os';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';
import sharp from 'sharp';
import { browserLaunchOptions } from './browser-launch-options.mjs';
import { createFindCapturePlan, readJobs } from './find-job-authority.mjs';

const websiteRoot = resolve(import.meta.dirname, '..');
const repoRoot = resolve(websiteRoot, '..', '..', '..');
const browserRoute = browserLaunchOptions(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH);
const playwrightVersion = JSON.parse(
  await readFile(resolve(websiteRoot, 'node_modules/playwright/package.json'), 'utf8'),
).version;

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

const jobsPath = resolve(argument('--jobs', resolve(websiteRoot, 'src/data/jobs.json')));
const outputOverride = argument('--output', '');
const planOnly = process.argv.includes('--plan');
const plan = createFindCapturePlan(await readJobs(jobsPath));

if (planOnly) {
  console.log(JSON.stringify(plan));
} else {
  const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
  const outputPath = (capture) => outputOverride
    ? resolve(outputOverride, basename(capture.output.path))
    : resolve(websiteRoot, capture.output.path);
  const captureByRoute = Object.fromEntries(plan.captures.map((capture) => [capture.route, capture]));

  async function stableScreenshot(page, path) {
    let previous;
    await page.waitForTimeout(600);
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await page.waitForTimeout(120);
      const current = await page.screenshot();
      if (previous?.equals(current)) {
        await writeFile(path, current);
        return current;
      }
      previous = current;
    }
    throw new Error(`capture did not settle: ${path}`);
  }

  for (const capture of plan.captures) await mkdir(resolve(outputPath(capture), '..'), { recursive: true });

  const deck = captureByRoute.deck;
  if (sharp.versions.sharp !== deck.conversion.version || sharp.versions.vips !== deck.conversion.libvipsVersion) {
    throw new Error(`Deck conversion requires sharp ${deck.conversion.version} / libvips ${deck.conversion.libvipsVersion}`);
  }
  const deckInput = resolve(repoRoot, deck.conversion.input.path);
  const deckInputBytes = await readFile(deckInput);
  const deckInputAuthority = deck.provenance.sources.find((source) => source.role === 'capture-input');
  if (sha256(deckInputBytes) !== deckInputAuthority.sha256) {
    throw new Error('Deck accepted source PNG hash drifted');
  }
  await sharp(deckInput)
    .resize({
      width: deck.conversion.output.width,
      height: deck.conversion.output.height,
      ...deck.conversion.resize,
    })
    .webp(deck.conversion.webp)
    .toFile(outputPath(deck));

  const browser = await chromium.launch({ headless: true, ...browserRoute });
  try {
    const local = captureByRoute.local;
    const localPage = await browser.newPage({
      viewport: { width: local.output.width, height: local.output.height },
      deviceScaleFactor: 1,
      colorScheme: 'dark',
      reducedMotion: 'reduce',
    });
    const localCss = await readFile(resolve(repoRoot, local.sources.style), 'utf8');
    const localScript = await readFile(resolve(repoRoot, local.sources.script), 'utf8');
    await localPage.setContent('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body class="search-popup-page enhanced"></body></html>');
    await localPage.addStyleTag({ content: localCss });
    await localPage.evaluate((rows) => {
      window.pywebview = { api: {
        system_search_status: async () => ({
          ok: true,
          supported: true,
          refreshing: false,
          total: rows.length,
          counts: Object.fromEntries(['app', 'file', 'folder'].map((kind) => [kind, rows.filter((row) => row.kind === kind).length])),
          hotkey: 'ctrl+alt+f',
          icon_version: 'deterministic:issue-41',
          file_provider: { available: true, state: 'ready', name: 'Windows Search', message: '' },
        }),
        system_search_cancel: async () => true,
        system_search_query: async (_query, category, limit, generation) => ({
          ok: true,
          results: rows.filter((row) => category === 'all' || row.kind === category).slice(0, limit),
          total_matches: rows.length,
          generation,
          provider_state: 'complete',
          icon_version: 'deterministic:issue-41',
        }),
        system_search_icons: async (_ids, generation, iconVersion) => ({
          ok: true, icons: {}, stale: false, generation, icon_version: iconVersion,
        }),
        system_search_execute: async () => ({ ok: true }),
        system_search_drag: async () => ({ ok: true, dropped: false, effect: 'none' }),
        system_search_refresh: async () => ({ ok: true, refreshing: false }),
        system_search_hide: async () => ({ ok: true, state: 'hidden' }),
        system_search_show: async () => ({ ok: true, state: 'visible' }),
      } };
    }, local.fixture.rows);
    await localPage.addScriptTag({ content: localScript });
    await localPage.evaluate(() => {
      window.bootSystemSearch();
      window.showSystemSearch();
    });
    await localPage.locator('#ss-input').fill(local.fixture.query);
    await localPage.locator('#ss-input').dispatchEvent('input');
    await localPage.getByText(local.fixture.rows[0].name, { exact: true }).waitFor();
    const localBytes = await stableScreenshot(localPage, outputPath(local));
    await localPage.close();

    const web = captureByRoute.web;
    const webPage = await browser.newPage({
      viewport: { width: web.output.width, height: web.output.height },
      deviceScaleFactor: 1,
      colorScheme: 'dark',
      reducedMotion: 'reduce',
    });
    await webPage.goto(pathToFileURL(resolve(repoRoot, web.sources.document)).href, { waitUntil: 'load' });
    await webPage.waitForFunction(() => typeof window.pyWebSearchConsent === 'function');
    await webPage.evaluate((fixture) => window.pyWebSearchConsent({
      request_id: fixture.requestId,
      provider: fixture.provider,
      query: fixture.query,
      privacy: fixture.privacy,
    }), web.fixture);
    await webPage.getByRole('dialog', { name: web.fixture.dialogName }).waitFor();
    const webBytes = await stableScreenshot(webPage, outputPath(web));
    await webPage.close();

    const deckBytes = await readFile(outputPath(deck));
    const deckHash = sha256(deckBytes);
    if (deckHash !== deck.provenance.outputSha256) {
      throw new Error(`Deck byte replay drifted: ${deckHash}`);
    }
    const localHash = sha256(localBytes);
    const webHash = sha256(webBytes);
    console.log(JSON.stringify({
      ok: true,
      environment: {
        platform: process.platform,
        architecture: process.arch,
        osRelease: osRelease(),
        osVersion: osVersion(),
        nodeVersion: process.version,
        playwrightVersion,
        browser: 'Google Chrome',
        browserVersion: browser.version(),
        browserExecutable: browserRoute.executablePath ?? `channel:${browserRoute.channel}`,
      },
      captures: [
        {
          route: 'deck', path: deck.output.path, width: deck.output.width, height: deck.output.height,
          sha256: deckHash, acceptedSha256: deck.provenance.outputSha256,
          byteIdenticalReplayRequired: deck.provenance.replayBoundary.byteIdenticalReplayRequired,
          matchesAcceptedOutput: true,
        },
        {
          route: 'local', path: local.output.path, width: local.output.width, height: local.output.height,
          sha256: localHash, acceptedSha256: local.provenance.outputSha256,
          byteIdenticalReplayRequired: local.provenance.replayBoundary.byteIdenticalReplayRequired,
          matchesAcceptedOutput: localHash === local.provenance.outputSha256,
        },
        {
          route: 'web', path: web.output.path, width: web.output.width, height: web.output.height,
          sha256: webHash, acceptedSha256: web.provenance.outputSha256,
          byteIdenticalReplayRequired: web.provenance.replayBoundary.byteIdenticalReplayRequired,
          matchesAcceptedOutput: webHash === web.provenance.outputSha256,
        },
      ],
    }));
  } finally {
    await browser.close();
  }
}
