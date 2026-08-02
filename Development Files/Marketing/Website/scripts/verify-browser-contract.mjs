import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { chromium } from 'playwright';

const websiteRoot = resolve(import.meta.dirname, '..');
const distRoot = resolve(websiteRoot, 'dist');
const results = [];
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?.trim() || undefined;

const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.png', 'image/png'],
  ['.webp', 'image/webp'],
  ['.zip', 'application/zip'],
]);

function record(name) {
  results.push(name);
  console.log(`PASS ${name}`);
}

function routeFile(pathname) {
  const decoded = decodeURIComponent(pathname);
  const route = decoded === '/' ? '/index.html' : decoded.replace(/\/$/, '') + (extname(decoded) ? '' : '/index.html');
  const candidate = resolve(distRoot, `.${route}`);
  if (candidate !== distRoot && !candidate.startsWith(`${distRoot}${sep}`)) {
    return null;
  }
  return candidate;
}

const server = createServer(async (request, response) => {
  const requestUrl = new URL(request.url ?? '/', 'http://127.0.0.1');
  const candidate = routeFile(requestUrl.pathname);
  if (!candidate) {
    response.writeHead(400).end('Bad request');
    return;
  }

  try {
    const body = await readFile(candidate);
    response.writeHead(200, {
      'cache-control': 'no-store',
      'content-type': contentTypes.get(extname(candidate)) ?? 'application/octet-stream',
    });
    response.end(body);
  } catch {
    try {
      const fallback = await readFile(resolve(distRoot, '404.html'));
      response.writeHead(404, { 'content-type': 'text/html; charset=utf-8' });
      response.end(fallback);
    } catch {
      response.writeHead(404).end('Not found');
    }
  }
});

await new Promise((resolveReady, rejectReady) => {
  server.once('error', rejectReady);
  server.listen(0, '127.0.0.1', resolveReady);
});
const address = server.address();
assert.ok(address && typeof address === 'object');
const origin = `http://127.0.0.1:${address.port}`;

async function noHorizontalOverflow(page, name) {
  const dimensions = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  assert.ok(
    Math.max(dimensions.body, dimensions.document) <= dimensions.viewport,
    `${name} overflows horizontally: ${JSON.stringify(dimensions)}`,
  );
}

function browserErrorsFor(page) {
  const errors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  return errors;
}

function assertNoBrowserErrors(errors, name) {
  assert.deepEqual(errors, [], `${name}: ${errors.join(' | ')}`);
}

async function assertVisibleFocus(page, name) {
  const style = await page.evaluate(() => {
    const element = document.activeElement;
    const computed = element ? getComputedStyle(element) : null;
    return { outlineStyle: computed?.outlineStyle, outlineWidth: computed?.outlineWidth };
  });
  assert.notEqual(style.outlineStyle, 'none', `${name} has no visible outline`);
  assert.notEqual(style.outlineWidth, '0px', `${name} outline has zero width`);
}

async function assertSharedShell(page, currentLabel) {
  assert.equal(await page.locator('a[href="#main-content"]').count(), 1, 'skip navigation is missing or duplicated');
  assert.equal(await page.locator('header[data-site-header]').count(), 1, 'site header is missing or duplicated');
  assert.equal(await page.locator('main#main-content').count(), 1, 'main landmark is missing or duplicated');
  assert.equal(await page.locator('footer[data-site-footer]').count(), 1, 'site footer is missing or duplicated');
  assert.equal(
    await page.getByRole('navigation', { name: 'Primary', includeHidden: true }).count(),
    1,
    'primary navigation is missing or duplicated',
  );
  assert.ok((await page.title()).trim(), 'document title is empty');
  assert.ok((await page.locator('meta[name="description"]').getAttribute('content'))?.trim(), 'metadata description is empty');
  if (currentLabel) {
    assert.equal(
      await page.getByRole('navigation', { name: 'Primary', includeHidden: true })
        .getByRole('link', { name: currentLabel, exact: true, includeHidden: true })
        .getAttribute('aria-current'),
      'page',
      `${currentLabel} is not exposed as the current page`,
    );
  }
}

async function desktopJourney(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
  });
  const page = await context.newPage();
  const browserErrors = browserErrorsFor(page);

  await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
  await assertSharedShell(page, 'Home');
  await page.getByRole('heading', { level: 1, name: /speech into useful work/i }).waitFor();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Downloads' }).waitFor();
  await page.getByRole('heading', { level: 3, name: 'Write' }).waitFor();
  await page.getByRole('heading', { level: 3, name: 'Find' }).waitFor();
  assert.match(await page.getByText('Mumble Find', { exact: false }).first().innerText(), /Mumble Find/);
  assert.match(await page.getByText('Web Search', { exact: false }).first().innerText(), /Web Search/);
  const homeText = await page.locator('body').innerText();
  assert.match(homeText, /Local transcription is the default/i);
  assert.match(homeText, /Audio leaves only after the user explicitly selects/i);
  assert.match(homeText, /Cloud shaping sends finished text.not the recorded audio/is);
  assert.match(homeText, /without hosted results entering the list/i);
  assert.match(homeText, /provider-named confirmation/i);
  assert.match(homeText, /Free and MIT licensed/i);
  assert.match(homeText, /no Mumble account or provider key/i);
  assert.match(homeText, /external providers may charge/i);
  assert.doesNotMatch(homeText, /Mumble Search/i);
  await noHorizontalOverflow(page, 'desktop Home');

  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Skip to content');
  await assertVisibleFocus(page, 'skip link focus');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'main-content');
  await assertVisibleFocus(page, 'skip-navigation destination focus');

  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Downloads' }).click();
  await page.waitForURL(`${origin}/downloads/`);
  await page.getByRole('heading', { level: 1, name: /download facts before the download/i }).waitFor();
  await assertSharedShell(page, 'Downloads');
  await page.getByRole('heading', { level: 2, name: 'Windows' }).waitFor();
  await page.getByRole('heading', { level: 2, name: 'macOS' }).waitFor();
  await page.getByRole('heading', { level: 2, name: 'Linux' }).waitFor();

  const windowsPanel = page.locator('article').filter({ has: page.getByRole('heading', { level: 2, name: 'Windows' }) });
  assert.match(await windowsPanel.innerText(), /Candidate artifact/i);
  assert.match(await windowsPanel.innerText(), /Public release remains gated/i);
  const windowsDownload = windowsPanel.getByRole('link', { name: /Download candidate for Windows/i });
  assert.equal(await windowsDownload.getAttribute('href'), '/Mumble.zip');
  assert.match(await windowsPanel.innerText(), /70794b4d13c1c38662425deb5700865728955f4fac78dc2d083436f63fb99493/i);
  const windowsText = await windowsPanel.innerText();
  assert.match(windowsText, /x86_64/i);
  assert.match(windowsText, /\bZIP\b/);
  assert.match(windowsText, /1,207,711 bytes/);
  assert.match(windowsText, /Internet access for first-time setup/i);
  assert.match(windowsText, /Windows 10 or 11 \(64-bit\)/i);
  assert.match(windowsText, /Publisher signature\s+Not accepted/i);

  for (const platform of ['macOS', 'Linux']) {
    const panel = page.locator('article').filter({ has: page.getByRole('heading', { level: 2, name: platform }) });
    assert.match(await panel.innerText(), /No accepted artifact/i);
    assert.equal(await panel.getByRole('link').count(), 0, `${platform} exposes an unsupported download`);
  }

  const pageText = await page.locator('body').innerText();
  assert.doesNotMatch(pageText, /available now|macOS available|Linux available|public release available/i);
  assert.match(pageText, /not an installed, signed, deployed, or publicly released package/i);
  assert.match(pageText, /public release remains gated/i);
  await noHorizontalOverflow(page, 'desktop Downloads');
  assertNoBrowserErrors(browserErrors, 'desktop journey browser errors');
  await context.close();
  record('desktop Home-to-Downloads journey, keyboard focus, release truth, and horizontal fit');
}

async function platformRecommendations(browser) {
  const cases = [
    {
      name: 'Windows',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36',
      action: 'Download candidate for Windows',
      href: '/Mumble.zip',
    },
    {
      name: 'macOS',
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 Safari/605.1.15',
      action: 'View macOS status',
      href: '/downloads/#macos',
    },
    {
      name: 'Linux',
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: 'View Linux status',
      href: '/downloads/#linux',
    },
    {
      name: 'unknown desktop',
      userAgent: 'CustomDesktop/1.0',
      action: 'View desktop downloads',
      href: '/downloads/#platforms-title',
    },
    {
      name: 'ChromeOS unknown desktop',
      userAgent: 'Mozilla/5.0 (X11; CrOS x86_64 16093.68.0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: 'View desktop downloads',
      href: '/downloads/#platforms-title',
    },
    {
      name: 'FreeBSD X11 unknown desktop',
      userAgent: 'Mozilla/5.0 (X11; FreeBSD amd64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: 'View desktop downloads',
      href: '/downloads/#platforms-title',
    },
    {
      name: 'mobile',
      userAgent: 'Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36',
      action: 'View desktop downloads',
      href: '/downloads/#platforms-title',
    },
  ];

  for (const item of cases) {
    const context = await browser.newContext({
      viewport: item.name === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 800 },
      userAgent: item.userAgent,
    });
    const page = await context.newPage();
    const browserErrors = browserErrorsFor(page);
    await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
    await assertSharedShell(page, 'Home');
    if (item.name === 'mobile') {
      await page.getByRole('button', { name: 'Open menu' }).click();
    }
    const action = page.getByRole('link', { name: item.action }).first();
    await action.waitFor();
    assert.equal(await action.getAttribute('href'), item.href, `${item.name} recommendation has the wrong destination`);
    assert.equal(
      await page.getByRole('link', { name: 'Downloads', exact: true }).count() > 0,
      true,
      `${item.name} recommendation removed complete Downloads access`,
    );
    assertNoBrowserErrors(browserErrors, `${item.name} recommendation browser errors`);
    await context.close();
  }
  record('Windows, macOS, Linux, unknown X11/desktop, and mobile recommendations preserve complete Downloads access');
}

async function mobileMenu(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
  });
  const page = await context.newPage();
  const browserErrors = browserErrorsFor(page);
  await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
  await assertSharedShell(page, 'Home');
  await noHorizontalOverflow(page, 'mobile Home');

  const toggle = page.getByRole('button', { name: 'Open menu' });
  await toggle.focus();
  await assertVisibleFocus(page, 'mobile menu toggle focus');
  await page.keyboard.press('Enter');
  assert.equal(await page.getByRole('button', { name: 'Close menu' }).getAttribute('aria-expanded'), 'true');
  assert.equal(await page.locator('main').getAttribute('inert'), '');
  assert.equal(await page.locator('footer').getAttribute('inert'), '');
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Home');
  await assertVisibleFocus(page, 'mobile menu first-link focus');

  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Close menu');
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'View desktop downloads');
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button', { name: 'Open menu' }).getAttribute('aria-expanded'), 'false');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Open menu');
  assert.equal(await page.locator('main').getAttribute('inert'), null);
  await page.keyboard.press('Enter');
  const jobsLink = page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Five jobs', exact: true });
  await jobsLink.focus();
  await page.keyboard.press('Enter');
  await page.waitForURL(`${origin}/#jobs`);
  assert.equal(await page.getByRole('button', { name: 'Open menu' }).getAttribute('aria-expanded'), 'false');
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Open menu');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Open menu');
  await assertVisibleFocus(page, 'mobile in-page navigation focus restoration');

  await page.getByRole('button', { name: 'Open menu' }).click();
  const mobileDownloadsLink = page.getByRole('navigation', { name: 'Primary' })
    .getByRole('link', { name: 'Downloads', exact: true });
  await mobileDownloadsLink.waitFor();
  await Promise.all([
    page.waitForURL(`${origin}/downloads/`),
    mobileDownloadsLink.click(),
  ]);
  assert.equal(page.url(), `${origin}/downloads/`, 'mobile Home Downloads link did not reach Downloads');
  await assertSharedShell(page, 'Downloads');
  await noHorizontalOverflow(page, 'mobile Downloads');
  assertNoBrowserErrors(browserErrors, 'mobile navigation browser errors');
  await context.close();
  record('mobile Home-to-Downloads visible-link journey, menu containment, focus restoration, inertness, and horizontal fit');
}

async function noJavaScriptPath(browser) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, javaScriptEnabled: false });
    const page = await context.newPage();
    const browserErrors = browserErrorsFor(page);
    await page.goto(`${origin}/`, { waitUntil: 'load' });
    await assertSharedShell(page, 'Home');
    await page.getByRole('heading', { level: 1, name: /speech into useful work/i }).waitFor();
    for (const job of ['Write', 'Capture', 'Shape', 'Listen', 'Find']) {
      await page.getByRole('heading', { level: 3, name: job }).waitFor();
    }
    const downloadsLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Downloads', exact: true });
    await downloadsLink.waitFor();
    await noHorizontalOverflow(page, `no-JavaScript Home ${viewport.width}px`);

    await Promise.all([
      page.waitForURL(`${origin}/downloads/`),
      downloadsLink.click(),
    ]);
    assert.equal(
      page.url(),
      `${origin}/downloads/`,
      `no-JavaScript ${viewport.width}px Home Downloads link did not reach Downloads`,
    );
    await assertSharedShell(page, 'Downloads');
    await page.getByText('0.95', { exact: true }).first().waitFor();
    await page.getByRole('link', { name: /Download candidate for Windows/i }).waitFor();
    await page.getByRole('heading', { level: 2, name: 'macOS' }).waitFor();
    await page.getByRole('heading', { level: 2, name: 'Linux' }).waitFor();
    const pageText = await page.locator('body').innerText();
    assert.match(pageText, /70794b4d13c1c38662425deb5700865728955f4fac78dc2d083436f63fb99493/i);
    assert.match(pageText, /Public release remains gated/i);
    assert.match(pageText, /No accepted artifact/i);
    await noHorizontalOverflow(page, `no-JavaScript Downloads ${viewport.width}px`);
    assertNoBrowserErrors(browserErrors, `no-JavaScript ${viewport.width}px browser errors`);
    await context.close();
  }
  record('no-JavaScript desktop/mobile Home-to-Downloads visible-link journeys retain core content, release facts, platform states, download access, and horizontal fit');
}

let browser;
try {
  console.log(`Browser executable route: ${executablePath ?? 'Playwright-managed Chromium'}`);
  browser = await chromium.launch({ headless: true, executablePath });
  console.log(`Browser version: ${browser.version()}`);
  await desktopJourney(browser);
  await platformRecommendations(browser);
  await mobileMenu(browser);
  await noJavaScriptPath(browser);
  console.log(`Browser contract passed: ${results.length} visitor-behaviour groups.`);
} finally {
  await browser?.close();
  await new Promise((resolveClosed) => server.close(resolveClosed));
}
