import { mkdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const websiteRoot = resolve(import.meta.dirname, '..');
const repoRoot = resolve(websiteRoot, '..', '..', '..');
const appRoot = resolve(repoRoot, 'Internal/app');
const outputRoot = resolve(websiteRoot, 'public/product');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?.trim()
  || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

await mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath });
try {
  const localPage = await browser.newPage({
    viewport: { width: 900, height: 680 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  });
  const localCss = await readFile(resolve(appRoot, 'experimental/system_search/ui.css'), 'utf8');
  const localScript = await readFile(resolve(appRoot, 'experimental/system_search/ui.js'), 'utf8');
  await localPage.setContent('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body class="search-popup-page enhanced"></body></html>');
  await localPage.addStyleTag({ content: localCss });
  await localPage.evaluate(() => {
    const demonstrationRows = [
      {
        id: 'demo-file-project-brief',
        kind: 'file',
        name: 'Project brief.docx',
        subtitle: 'Demonstration document · not user data',
        meta: 'Demo document · not user data',
        source: 'windows-search',
        actions: ['open', 'reveal'],
      },
      {
        id: 'demo-folder-project-notes',
        kind: 'folder',
        name: 'Project notes',
        subtitle: 'Demonstration folder · not user data',
        meta: 'Demo folder · not user data',
        source: 'windows-search',
        actions: ['open', 'reveal'],
      },
      {
        id: 'demo-app-project-board',
        kind: 'app',
        name: 'Project Board',
        subtitle: 'Demonstration application · not user data',
        meta: 'Demo application · not user data',
        source: 'start-menu',
        actions: ['open', 'reveal'],
      },
    ];
    window.pywebview = { api: {
      system_search_status: async () => ({
        ok: true,
        supported: true,
        refreshing: false,
        total: demonstrationRows.length,
        counts: { app: 1, file: 1, folder: 1 },
        hotkey: 'ctrl+alt+f',
        icon_version: 'deterministic:issue-41',
        file_provider: { available: true, state: 'ready', name: 'Windows Search', message: '' },
      }),
      system_search_cancel: async () => true,
      system_search_query: async (_query, category, limit, generation) => ({
        ok: true,
        results: demonstrationRows.filter((row) => category === 'all' || row.kind === category).slice(0, limit),
        total_matches: demonstrationRows.length,
        generation,
        provider_state: 'complete',
        icon_version: 'deterministic:issue-41',
      }),
      system_search_icons: async (_ids, generation, iconVersion) => ({
        ok: true,
        icons: {},
        stale: false,
        generation,
        icon_version: iconVersion,
      }),
      system_search_execute: async () => ({ ok: true }),
      system_search_drag: async () => ({ ok: true, dropped: false, effect: 'none' }),
      system_search_refresh: async () => ({ ok: true, refreshing: false }),
      system_search_hide: async () => ({ ok: true, state: 'hidden' }),
      system_search_show: async () => ({ ok: true, state: 'visible' }),
    } };
  });
  await localPage.addScriptTag({ content: localScript });
  await localPage.evaluate(() => {
    window.bootSystemSearch();
    window.showSystemSearch();
  });
  await localPage.locator('#ss-input').fill('project');
  await localPage.locator('#ss-input').dispatchEvent('input');
  await localPage.getByText('Project brief.docx', { exact: true }).waitFor();
  await localPage.screenshot({ path: resolve(outputRoot, 'mumble-find-capture.png') });
  await localPage.close();

  const webPage = await browser.newPage({
    viewport: { width: 1180, height: 820 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  });
  await webPage.goto(pathToFileURL(resolve(appRoot, 'webui/index.html')).href, { waitUntil: 'load' });
  await webPage.waitForFunction(() => typeof window.pyWebSearchConsent === 'function');
  await webPage.evaluate(() => window.pyWebSearchConsent({
    request_id: 'deterministic-issue-41-capture',
    provider: 'Perplexity',
    query: 'Compare the project brief with the launch notes.',
    privacy: 'These demonstration words will be sent to Perplexity over the internet only after you choose Search online. Mumble Find stays private on this device.',
  }));
  await webPage.getByRole('dialog', { name: 'Search online with Perplexity?' }).waitFor();
  await webPage.screenshot({ path: resolve(outputRoot, 'web-search-consent-capture.png') });
  await webPage.close();

  console.log(JSON.stringify({
    ok: true,
    browser: browser.version(),
    captures: [
      { route: 'local', path: 'public/product/mumble-find-capture.png', width: 900, height: 680 },
      { route: 'web', path: 'public/product/web-search-consent-capture.png', width: 1180, height: 820 },
    ],
  }));
} finally {
  await browser.close();
}
