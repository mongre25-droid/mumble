import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { chromium } from 'playwright';
import jobs from '../src/data/jobs.json' with { type: 'json' };
import release from '../src/data/release.json' with { type: 'json' };

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

function contrastRatio(foreground, background) {
  const channels = (color) => {
    const hex = color.match(/^#([\da-f]{6})$/i);
    const rgb = color.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    const values = hex
      ? [1, 3, 5].map((offset) => Number.parseInt(hex[1].slice(offset - 1, offset + 1), 16))
      : rgb?.slice(1, 4).map(Number);
    assert.ok(values, `Unsupported contrast colour: ${color}`);
    return values.map((value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
  };
  const luminance = (color) => {
    const [red, green, blue] = channels(color);
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
  };
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

async function markerContrast(page, selector) {
  const colours = await page.locator(selector).first().evaluate((element) => {
    const root = getComputedStyle(document.documentElement);
    return {
      foreground: getComputedStyle(element).color,
      surfaces: [root.getPropertyValue('--ink').trim(), '#0a0907'],
    };
  });
  return Math.min(...colours.surfaces.map((surface) => contrastRatio(colours.foreground, surface)));
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
  await page.getByRole('heading', { level: 1, name: /field where your words belong/i }).waitFor();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Downloads' }).waitFor();
  await page.getByRole('heading', { level: 3, name: 'Write' }).waitFor();
  await page.getByRole('heading', { level: 3, name: 'Find', exact: true }).waitFor();
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
  for (const platform of release.platforms) {
    await page.getByRole('heading', { level: 2, name: platform.label }).waitFor();
  }

  const candidatePlatform = release.platforms.find((platform) => platform.availability === 'candidate');
  const candidate = candidatePlatform?.variants.find((variant) => variant.availability === 'candidate');
  assert.ok(candidatePlatform && candidate?.integrity && candidate.artifactLocation);
  const windowsPanel = page.locator('article').filter({
    has: page.getByRole('heading', { level: 2, name: candidatePlatform.label }),
  });
  const windowsText = await windowsPanel.innerText();
  assert.ok(windowsText.includes(candidate.availabilityLabel));
  assert.ok(windowsText.includes(release.publication.label));
  const windowsDownload = windowsPanel.getByRole('link', {
    name: release.recommendations.windows.label,
    exact: true,
  });
  assert.equal(await windowsDownload.getAttribute('href'), candidate.artifactLocation);
  assert.ok(windowsText.includes(candidate.integrity.value));
  assert.ok(windowsText.includes(candidate.architecture));
  assert.ok(windowsText.includes(candidate.format));
  assert.ok(windowsText.includes(`${candidate.sizeBytes.toLocaleString('en-GB')} bytes`));
  for (const requirement of candidate.requirements) {
    assert.ok(windowsText.includes(requirement), `Windows panel omits requirement ${requirement}`);
  }
  assert.match(
    windowsText,
    new RegExp(`Publisher signature\\s+${candidate.integrity.publisherSignature.label}`, 'i'),
  );

  for (const platform of release.platforms.filter((entry) => entry.availability === 'gated')) {
    const panel = page.locator('article').filter({
      has: page.getByRole('heading', { level: 2, name: platform.label }),
    });
    assert.ok((await panel.innerText()).includes(platform.availabilityLabel));
    assert.equal(await panel.getByRole('link').count(), 0, `${platform.label} exposes an unsupported download`);
  }

  const pageText = await page.locator('body').innerText();
  assert.doesNotMatch(pageText, /available now|macOS available|Linux available|public release available/i);
  assert.ok(pageText.includes(candidatePlatform.gate));
  assert.ok(pageText.includes(release.publication.label));
  await noHorizontalOverflow(page, 'desktop Downloads');
  assertNoBrowserErrors(browserErrors, 'desktop journey browser errors');
  await context.close();
  record('desktop Home-to-Downloads journey, keyboard focus, release truth, and horizontal fit');
}

async function followPrimaryNavigation(page, label, href, mobile) {
  if (mobile) {
    await page.getByRole('button', { name: 'Open menu' }).click();
  }
  const link = page.getByRole('navigation', { name: 'Primary' })
    .getByRole('link', { name: label, exact: true });
  await Promise.all([
    page.waitForURL(`${origin}${href}`),
    link.click(),
  ]);
}

function stopTimeDestinationFindings(text, surface, label) {
  const requiredBySurface = {
    home: [
      /Keep or return focus to the intended field before you choose Stop\./,
      /When Stop is requested, Mumble freezes that field as the destination\./,
    ],
    product: [
      /Keep or return focus to the intended field before you choose Stop\./,
      /The destination is frozen when Stop is requested—not when recording starts\./,
    ],
    'use-cases': [
      /keep or return focus to (?:that|the intended) field before you choose Stop/i,
      /field frozen at Stop/i,
    ],
  };
  const findings = [];
  for (const requirement of requiredBySurface[surface]) {
    if (!requirement.test(text)) {
      findings.push(`${label} omits truthful Stop-time guidance (${requirement})`);
    }
  }
  for (const staleWording of [
    /Write keeps the starting field/i,
    /The destination is chosen before recording begins/i,
    /The destination comes first/i,
  ]) {
    if (staleWording.test(text)) {
      findings.push(`${label} retains stale start-time destination wording (${staleWording})`);
    }
  }
  return findings;
}


async function correctionConstraints(browser) {
  const context = await browser.newContext({
    viewport: { width: 1365, height: 900 },
    reducedMotion: 'reduce',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
  });
  const page = await context.newPage();
  const findings = [];

  await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
  findings.push(...stopTimeDestinationFindings(
    await page.locator('main').innerText(),
    'home',
    'desktop JavaScript Home',
  ));
  const stage = page.locator('[data-job-story="write"][data-story-surface="home"]');
  const storyId = await stage.getAttribute('data-job-story');
  const expectedTitleId = `job-${storyId}-stage-title`;
  if (await stage.getAttribute('aria-labelledby') !== expectedTitleId) {
    findings.push('reusable story title ID is not derived from the job ID');
  }
  if (await stage.getAttribute('data-job-demo') !== storyId) {
    findings.push('interaction dispatch still lacks the job-scoped demo hook');
  }
  const tabs = stage.locator(`[data-job-tab="${storyId}"]`);
  const panels = stage.locator(`[data-job-panel="${storyId}"]`);
  if (await tabs.count() !== 3 || await panels.count() !== 3) {
    findings.push('tabs and panels do not expose the shared job-scoped hooks');
  } else {
    for (let index = 0; index < await tabs.count(); index += 1) {
      const tab = tabs.nth(index);
      const panel = panels.nth(index);
      const tabId = await tab.getAttribute('id');
      const panelId = await panel.getAttribute('id');
      if (!tabId?.startsWith(`job-${storyId}-tab-`) || !panelId?.startsWith(`job-${storyId}-panel-`)) {
        findings.push('tab and panel IDs do not use the shared job-scoped convention');
        break;
      }
      if (await tab.getAttribute('aria-controls') !== panelId || await panel.getAttribute('aria-labelledby') !== tabId) {
        findings.push('job-scoped tab and panel relationships are broken');
        break;
      }
    }
  }
  if (await stage.getByRole('tablist', { name: 'Write demonstration steps' }).count() !== 1
    || await stage.getByRole('group', { name: 'Write demonstration navigation' }).count() !== 1) {
    findings.push('demonstration labels are not derived from the supplied job label');
  }
  const controlHeights = await Promise.all(
    ['Previous Write step', 'Next Write step'].map(async (name) => (
      await stage.getByRole('button', { name }).boundingBox()
    )),
  );
  if (controlHeights.some((box) => !box || box.height < 44)) {
    findings.push(`Previous/Next targets are below 44px (${controlHeights.map((box) => box?.height ?? 0).join(', ')})`);
  }

  await page.goto(`${origin}/product/`, { waitUntil: 'networkidle' });
  const deckCapture = page.locator('.recovery-capture img');
  const deckFacts = await deckCapture.evaluate((image) => ({
    src: image.getAttribute('src'),
    alt: image.getAttribute('alt') ?? '',
    width: image.getAttribute('width'),
    height: image.getAttribute('height'),
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  }));
  if (deckFacts.src !== '/product/deck-command-surface.webp'
    || deckFacts.width !== '1152'
    || deckFacts.height !== '800'
    || deckFacts.naturalWidth !== 1152
    || deckFacts.naturalHeight !== 800
    || !['content type', 'Starred', 'Pin', 'Unpin', 'More'].every((term) => deckFacts.alt.includes(term))) {
    findings.push(`Product uses stale or inaccurately described Deck proof (${JSON.stringify(deckFacts)})`);
  }
  findings.push(...stopTimeDestinationFindings(
    await page.locator('main').innerText(),
    'product',
    'desktop JavaScript Product',
  ));
  const mechanismContrast = await markerContrast(page, '.job-sequence-index');
  if (mechanismContrast < 4.5) {
    findings.push(`Product sequence contrast is ${mechanismContrast.toFixed(2)}:1`);
  }

  await page.goto(`${origin}/use-cases/`, { waitUntil: 'networkidle' });
  findings.push(...stopTimeDestinationFindings(
    await page.locator('main').innerText(),
    'use-cases',
    'desktop JavaScript Use Cases',
  ));
  const taskContrast = await markerContrast(page, '.task-ledger li > span');
  if (taskContrast < 4.5) {
    findings.push(`Use Cases sequence contrast is ${taskContrast.toFixed(2)}:1`);
  }

  await context.close();

  const noJavaScriptContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    javaScriptEnabled: false,
  });
  const noJavaScriptPage = await noJavaScriptContext.newPage();
  await noJavaScriptPage.goto(`${origin}/`, { waitUntil: 'load' });
  const noJavaScriptHeaderAction = await noJavaScriptPage.locator('.site-header .header-action').boundingBox();
  if (!noJavaScriptHeaderAction
    || noJavaScriptHeaderAction.width < 44
    || noJavaScriptHeaderAction.height < 44) {
    findings.push(`no-JavaScript mobile header action is below 44x44px (${noJavaScriptHeaderAction?.width ?? 0}x${noJavaScriptHeaderAction?.height ?? 0}px)`);
  }
  findings.push(...stopTimeDestinationFindings(
    await noJavaScriptPage.locator('main').innerText(),
    'home',
    'mobile no-JavaScript Home',
  ));
  await followPrimaryNavigation(noJavaScriptPage, 'Product', '/product/', false);
  findings.push(...stopTimeDestinationFindings(
    await noJavaScriptPage.locator('main').innerText(),
    'product',
    'mobile no-JavaScript Product',
  ));
  await followPrimaryNavigation(noJavaScriptPage, 'Use Cases', '/use-cases/', false);
  findings.push(...stopTimeDestinationFindings(
    await noJavaScriptPage.locator('main').innerText(),
    'use-cases',
    'mobile no-JavaScript Use Cases',
  ));
  await noJavaScriptContext.close();

  assert.deepEqual(findings, [], `Issue #37 integration correction constraints:\n- ${findings.join('\n- ')}`);
  console.log(`Correction measurements: controls ${controlHeights.map((box) => box.height).join('px, ')}px; contrast ${mechanismContrast.toFixed(2)}:1 / ${taskContrast.toFixed(2)}:1; no-JavaScript mobile header action ${noJavaScriptHeaderAction?.width ?? 0}x${noJavaScriptHeaderAction?.height ?? 0}px`);
  record('Issue #37 reusable job story, current Deck proof, 44px controls, 4.5:1 sequence markers, Stop-time destination truth, and no-JavaScript mobile target geometry');
}

async function assertWriteStage(page, viewportName) {
  const stage = page.locator('[data-job-story="write"][data-story-surface="home"]');
  await stage.getByRole('heading', { level: 2, name: /where your words return/i }).waitFor();
  assert.match(await stage.innerText(), /Genuine Mumble capture/i);
  assert.match(await stage.innerText(), /Illustrative cursor close-up—not a live transcription/i);
  assert.match(await stage.innerText(), /Keep the intended field ready for Stop/i);
  assert.match(await stage.innerText(), /Keep it focused, or return to it, before choosing Stop/i);

  const tabs = stage.getByRole('tab');
  assert.equal(await tabs.count(), 3, `${viewportName} Write stage does not expose three direct steps`);
  const speak = stage.getByRole('tab', { name: /Speak/i });
  await speak.focus();
  await assertVisibleFocus(page, `${viewportName} Write direct-step focus`);
  await page.keyboard.press('Enter');
  assert.equal(await speak.getAttribute('aria-selected'), 'true');
  await stage.getByRole('tabpanel', { name: /Speak/i }).waitFor();

  await page.keyboard.press('ArrowRight');
  const returned = stage.getByRole('tab', { name: /Text returned/i });
  assert.equal(await returned.getAttribute('aria-selected'), 'true');
  assert.equal(await returned.evaluate((element) => document.activeElement === element), true);
  const returnedPanel = stage.getByRole('tabpanel', { name: /Text returned/i });
  await returnedPanel.waitFor();
  assert.match(await returnedPanel.innerText(), /freezes the focused field/i);
  assert.match(await returnedPanel.innerText(), /recoverable in the Deck/i);

  await page.waitForTimeout(550);
  assert.equal(
    await returned.getAttribute('aria-selected'),
    'true',
    `${viewportName} reduced-motion Write stage advanced without visitor input`,
  );
  await stage.getByRole('button', { name: 'Previous Write step' }).click();
  assert.equal(await speak.getAttribute('aria-selected'), 'true');
}

async function writeJourney(browser) {
  const cases = [
    { name: 'desktop', viewport: { width: 1365, height: 900 }, mobile: false },
    { name: 'mobile', viewport: { width: 390, height: 844 }, mobile: true },
  ];

  for (const item of cases) {
    const context = await browser.newContext({
      viewport: item.viewport,
      reducedMotion: 'reduce',
      userAgent: item.mobile
        ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'
        : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
    });
    await context.addInitScript(() => {
      window.__mumbleMicrophoneRequests = 0;
      if (navigator.mediaDevices?.getUserMedia) {
        navigator.mediaDevices.getUserMedia = () => {
          window.__mumbleMicrophoneRequests += 1;
          return Promise.reject(new Error('Website microphone access is forbidden'));
        };
      }
    });
    const page = await context.newPage();
    const browserErrors = browserErrorsFor(page);

    await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
    await assertSharedShell(page, 'Home');
    await page.getByRole('heading', { level: 1, name: /field where your words belong/i }).waitFor();
    await assertWriteStage(page, item.name);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    const homeText = await page.locator('body').innerText();
    assert.deepEqual(
      stopTimeDestinationFindings(homeText, 'home', `${item.name} JavaScript Home`),
      [],
    );
    assert.doesNotMatch(homeText, /\b[0-9]+(?:\.[0-9]+)?[×x]\s*(?:faster|speed)/i);
    await noHorizontalOverflow(page, `${item.name} Write Home`);

    await followPrimaryNavigation(page, 'Product', '/product/', item.mobile);
    await assertSharedShell(page, 'Product');
    const productText = await page.locator('main').innerText();
    assert.deepEqual(
      stopTimeDestinationFindings(productText, 'product', `${item.name} JavaScript Product`),
      [],
    );
    assert.match(productText, /deliberate global Dictate command/i);
    assert.match(productText, /Island shows Listening, then Transcribing/i);
    assert.match(productText, /Local transcription is the default/i);
    assert.match(productText, /field frozen when Stop was requested/i);
    assert.match(productText, /saved in the Deck/i);
    await page.locator('.journey-next [data-platform-action]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Product`);

    await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', item.mobile);
    await assertSharedShell(page, 'Use Cases');
    for (const task of ['Everyday notes', 'Longer text', 'Across applications']) {
      const taskRegion = page.getByRole('article').filter({
        has: page.getByRole('heading', { level: 2, name: task }),
      });
      await taskRegion.waitFor();
      assert.ok(await taskRegion.getByRole('listitem').count() >= 4, `${task} is not a complete task sequence`);
    }
    const useCasesText = await page.locator('main').innerText();
    assert.deepEqual(
      stopTimeDestinationFindings(useCasesText, 'use-cases', `${item.name} JavaScript Use Cases`),
      [],
    );
    assert.match(useCasesText, /tasks rather than professions/i);
    await page.locator('.journey-next [data-platform-action]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Use Cases`);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    assertNoBrowserErrors(browserErrors, `${item.name} Write journey browser errors`);
    await context.close();
  }
  record('Issue #37 desktop/mobile Write journey, direct-step keyboard controls, reduced-motion stability, truthful mechanism, complete task sequences, no microphone request, release action, and horizontal fit');
}

async function findJourney(browser) {
  const findJob = jobs.find((job) => job.id === 'find');
  assert.ok(findJob?.story?.presentation, 'canonical Find presentation data is missing');
  const context = await browser.newContext({
    viewport: { width: 1365, height: 900 },
    reducedMotion: 'reduce',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
  });
  const page = await context.newPage();
  const browserErrors = browserErrorsFor(page);

  await page.goto(`${origin}/`, { waitUntil: 'networkidle' });
  const stage = page.locator('[data-job-story="find"][data-story-surface="home"]');
  assert.equal(
    await stage.count(),
    1,
    'Home must expose one major Find job stage, not only a small job-ledger entry',
  );
  const stageText = await stage.innerText();
  for (const capture of findJob.story.presentation.captures) {
    const figure = stage.locator(`[data-find-capture="${capture.route}"]`);
    const image = figure.locator('img');
    assert.equal(await figure.count(), 1, `${capture.route} product capture is missing from Home`);
    await figure.scrollIntoViewIfNeeded();
    await image.evaluate(async (element) => {
      if (!element.complete || element.naturalWidth === 0) await element.decode();
    });
    assert.deepEqual(
      await image.evaluate((element) => ({
        src: element.getAttribute('src'),
        width: element.getAttribute('width'),
        height: element.getAttribute('height'),
        naturalWidth: element.naturalWidth,
        naturalHeight: element.naturalHeight,
        alt: element.getAttribute('alt'),
      })),
      {
        src: capture.src,
        width: String(capture.width),
        height: String(capture.height),
        naturalWidth: capture.width,
        naturalHeight: capture.height,
        alt: capture.alt,
      },
      `${capture.route} product capture does not match canonical evidence metadata`,
    );
    const accessibleImage = figure.getByRole('img', { name: capture.alt, exact: true });
    assert.equal(
      await accessibleImage.count(),
      1,
      `${capture.route} product capture is missing from the browser accessibility tree`,
    );
    if (capture.route === 'deck') {
      assert.doesNotMatch(
        await accessibleImage.ariaSnapshot(),
        /\bMumble Find\b/i,
        'Deck accessibility output must not claim the pinned Deck image shows Mumble Find',
      );
    }
    const caption = await figure.locator('figcaption').textContent() ?? '';
    assert.ok(caption.includes(capture.label), `${capture.route} capture label is missing`);
    assert.ok(caption.includes(capture.truthLabel), `${capture.route} capture truth label is missing`);
    assert.equal(await figure.locator('figcaption small').isVisible(), true, `${capture.route} capture truth label is hidden`);
  }
  assert.match(stageText, /Genuine Mumble Deck capture/i);
  assert.match(stageText, /Genuine Mumble Find capture/i);
  assert.match(stageText, /Genuine Mumble Web Search capture/i);
  assert.match(stageText, /Mumble Find/i);
  assert.match(stageText, /Find apps & files/i);
  assert.match(stageText, /private on this device/i);
  assert.match(stageText, /deterministic demonstration local result · not user data/i);
  assert.match(stageText, /Web Search/i);
  assert.match(stageText, /selected words/i);
  assert.match(stageText, /Google|Perplexity|Brave/i);
  assert.match(stageText, /confirm/i);
  assert.doesNotMatch(stageText, /Mumble Search/i);
  const findStageLink = stage.getByRole('link', { name: 'See how Find works', exact: true });
  await findStageLink.focus();
  await assertVisibleFocus(page, 'desktop Home Find stage link focus');
  const stableStageText = await stage.innerText();
  await page.waitForTimeout(400);
  assert.equal(await stage.innerText(), stableStageText, 'reduced-motion Home Find stage changed without visitor input');
  await noHorizontalOverflow(page, 'desktop Find Home');

  await followPrimaryNavigation(page, 'Product', '/product/', false);
  const productFind = page.locator('[data-job-story="find"][data-story-surface="product"]');
  assert.equal(
    await productFind.count(),
    1,
    'Product must expose one complete Find mechanism region',
  );
  const productText = await productFind.innerText();
  for (const required of [
    /Deck history/i,
    /Search all Deck content/i,
    /Starred/i,
    /copy|paste|shape/i,
    /local apps and indexed files/i,
    /Open/i,
    /Show in folder/i,
    /Drag to another app/i,
    /hosted web results never/i,
    /Google/i,
    /Perplexity/i,
    /Brave/i,
    /selected words/i,
    /Search online/i,
    /browser could not open/i,
  ]) {
    assert.match(productText, required, `Product Find region omits ${required}`);
  }
  assert.equal(
    await productFind.locator('[data-find-boundary]').count(),
    3,
    'Product must keep Deck, local Mumble Find, and Web Search as three named boundaries',
  );
  assert.doesNotMatch(productText, /Mumble Search/i);
  await noHorizontalOverflow(page, 'desktop Find Product');

  await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', false);
  const useCasesFind = page.locator('[data-job-story="find"][data-story-surface="use-cases"]');
  assert.equal(
    await useCasesFind.count(),
    1,
    'Use Cases must expose one complete Find task region',
  );
  for (const [taskId, task] of [
    ['prior-text', 'Find prior text'],
    ['deck-material', 'Reopen useful Deck material'],
    ['local-app-file', 'Locate a local app or file'],
    ['web-search', 'Search the web deliberately'],
  ]) {
    const taskRegion = useCasesFind.locator(`#job-find-task-${taskId}`);
    assert.equal(await taskRegion.count(), 1, `${task} is missing`);
    assert.equal(
      await taskRegion.getByRole('heading', { level: 2, name: task, exact: true }).count(),
      1,
      `${task} heading is missing`,
    );
    assert.ok(await taskRegion.getByRole('listitem').count() >= 4, `${task} is not an end-to-end sequence`);
  }
  const useCasesText = await useCasesFind.innerText();
  assert.match(useCasesText, /Search all Deck content/i);
  assert.match(useCasesText, /Starred/i);
  assert.match(useCasesText, /Mumble Find/i);
  assert.match(useCasesText, /local results/i);
  assert.match(useCasesText, /Open|Show in folder/i);
  assert.match(useCasesText, /chosen provider/i);
  assert.match(useCasesText, /selected words/i);
  assert.match(useCasesText, /Search online/i);
  assert.match(useCasesText, /browser could not open/i);
  assert.match(useCasesText, /Deterministic demonstration selected text · not user data/i);
  assert.doesNotMatch(useCasesText, /Mumble Search/i);
  await noHorizontalOverflow(page, 'desktop Find Use Cases');
  assertNoBrowserErrors(browserErrors, 'desktop Find journey browser errors');
  await context.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: 'reduce',
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
  });
  await mobileContext.addInitScript(() => {
    window.__mumbleMicrophoneRequests = 0;
    if (navigator.mediaDevices?.getUserMedia) {
      navigator.mediaDevices.getUserMedia = () => {
        window.__mumbleMicrophoneRequests += 1;
        return Promise.reject(new Error('Website microphone access is forbidden'));
      };
    }
  });
  const mobilePage = await mobileContext.newPage();
  const mobileErrors = browserErrorsFor(mobilePage);

  await mobilePage.goto(`${origin}/`, { waitUntil: 'networkidle' });
  const mobileStage = mobilePage.locator('[data-job-story="find"][data-story-surface="home"]');
  assert.equal(await mobileStage.count(), 1, 'mobile Home Find stage is missing');
  assert.equal(await mobileStage.locator('[data-find-capture]').count(), 3, 'mobile Home product captures are incomplete');
  assert.equal(await mobileStage.locator('[data-find-route]').count(), 2, 'mobile Home local and web routes merged');
  const mobileFindLink = mobileStage.getByRole('link', { name: 'See how Find works', exact: true });
  await mobileFindLink.focus();
  await assertVisibleFocus(mobilePage, 'mobile Home Find stage link focus');
  await noHorizontalOverflow(mobilePage, 'mobile Find Home');

  await followPrimaryNavigation(mobilePage, 'Product', '/product/', true);
  const mobileProductFind = mobilePage.locator('[data-job-story="find"][data-story-surface="product"]');
  assert.equal(await mobileProductFind.locator('[data-find-boundary]').count(), 3, 'mobile Product Find boundaries merged');
  assert.match(await mobileProductFind.innerText(), /browser could not open/i);
  await noHorizontalOverflow(mobilePage, 'mobile Find Product');

  await followPrimaryNavigation(mobilePage, 'Use Cases', '/use-cases/', true);
  const mobileUseCasesFind = mobilePage.locator('[data-job-story="find"][data-story-surface="use-cases"]');
  assert.equal(await mobileUseCasesFind.locator('article').count(), 4, 'mobile Find task sequences are incomplete');
  const mobilePriorText = mobilePage.locator('a[href="#job-find-task-prior-text"]');
  assert.equal(await mobilePriorText.count(), 1, 'mobile Find task jump is missing');
  await mobilePriorText.focus();
  await assertVisibleFocus(mobilePage, 'mobile Find task-jump focus');
  assert.equal(await mobilePage.evaluate(() => window.__mumbleMicrophoneRequests), 0);
  await noHorizontalOverflow(mobilePage, 'mobile Find Use Cases');
  assertNoBrowserErrors(mobileErrors, 'mobile Find journey browser errors');
  await mobileContext.close();

  record('Issue #41 desktop/mobile Find journey keeps Deck, local Mumble Find, provider consent, failure truth, keyboard focus, reduced motion, zero microphone use, and fit distinct');
}


async function platformRecommendations(browser) {
  const cases = [
    {
      name: 'Windows',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36',
      action: release.recommendations.windows,
    },
    {
      name: 'macOS',
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 Safari/605.1.15',
      action: release.recommendations.macos,
    },
    {
      name: 'Linux',
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: release.recommendations.linux,
    },
    {
      name: 'unknown desktop',
      userAgent: 'CustomDesktop/1.0',
      action: release.recommendations.unknown,
    },
    {
      name: 'ChromeOS unknown desktop',
      userAgent: 'Mozilla/5.0 (X11; CrOS x86_64 16093.68.0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: release.recommendations.unknown,
    },
    {
      name: 'FreeBSD X11 unknown desktop',
      userAgent: 'Mozilla/5.0 (X11; FreeBSD amd64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: release.recommendations.unknown,
    },
    {
      name: 'mobile',
      userAgent: 'Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36',
      action: release.recommendations.mobile,
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
    const action = page.getByRole('link', { name: item.action.label }).first();
    await action.waitFor();
    assert.equal(
      await action.getAttribute('href'),
      item.action.href,
      `${item.name} recommendation has the wrong destination`,
    );
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

async function downloadsContract(browser) {
  const cases = [
    {
      name: 'Windows',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36',
      action: release.recommendations.windows,
    },
    {
      name: 'macOS',
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 Safari/605.1.15',
      action: release.recommendations.macos,
    },
    {
      name: 'Linux',
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
      action: release.recommendations.linux,
    },
    {
      name: 'unknown desktop',
      userAgent: 'CustomDesktop/1.0',
      action: release.recommendations.unknown,
    },
    {
      name: 'mobile',
      userAgent: 'Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36',
      action: release.recommendations.mobile,
    },
  ];
  const variants = release.platforms.flatMap((platform) =>
    platform.variants.map((variant) => ({ platform, variant })),
  );
  const windows = variants.find(({ variant }) => variant.availability === 'candidate');
  assert.ok(windows, 'release authority has no candidate variant');

  for (const javaScriptEnabled of [true, false]) {
    for (const item of cases) {
      const context = await browser.newContext({
        viewport: item.name === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 800 },
        userAgent: item.userAgent,
        javaScriptEnabled,
      });
      const page = await context.newPage();
      const browserErrors = browserErrorsFor(page);
      await page.goto(`${origin}/downloads/`, { waitUntil: javaScriptEnabled ? 'networkidle' : 'load' });
      await assertSharedShell(page, 'Downloads');
      await page.getByRole('heading', { level: 1, name: /download facts before the download/i }).waitFor();

      const expectedAction = javaScriptEnabled ? item.action : release.recommendations.unknown;
      const recommendation = page.locator('.recommendation')
        .getByRole('link', { name: expectedAction.label, exact: true });
      await recommendation.waitFor();
      assert.equal(
        await recommendation.getAttribute('href'),
        expectedAction.href,
        `${item.name} ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} recommendation destination drifted`,
      );
      assert.equal(
        await recommendation.getAttribute('download'),
        expectedAction.download ? '' : null,
        `${item.name} ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} recommendation download state drifted`,
      );

      const matrix = page.locator('[data-downloads-matrix]');
      await matrix.waitFor();
      assert.equal(
        await matrix.locator('[data-variant-id]').count(),
        variants.length,
        'Downloads matrix does not expose every accepted variant',
      );
      for (const { platform, variant } of variants) {
        const panel = matrix.locator(`[data-variant-id="${variant.id}"]`);
        await panel.waitFor();
        const text = await panel.innerText();
        assert.match(text, new RegExp(platform.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
        assert.match(text, new RegExp(variant.architecture.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
        assert.match(text, new RegExp(variant.format.replace('.', '\\.'), 'i'));
        assert.match(text, new RegExp(release.channel.label, 'i'));
        assert.match(text, new RegExp(release.version.replace('.', '\\.'), 'i'));
        assert.match(text, new RegExp(release.publication.statusLabel, 'i'));
        assert.match(text, new RegExp(release.publication.dateLabel, 'i'));
        if (variant.availability === 'candidate') {
          assert.match(text, new RegExp(`${variant.sizeBytes.toLocaleString('en-GB')} bytes`));
          for (const requirement of variant.requirements) {
            assert.ok(text.includes(requirement), `${variant.id} omits requirement ${requirement}`);
          }
          assert.ok(text.includes(variant.integrity.algorithm), `${variant.id} omits checksum algorithm`);
          assert.ok(text.includes(variant.integrity.value), `${variant.id} omits checksum`);
          assert.ok(text.includes(variant.integrity.checksumStatus.label), `${variant.id} omits checksum status`);
          assert.ok(
            text.includes(variant.integrity.publisherSignature.label),
            `${variant.id} omits publisher-signature status`,
          );
          const action = panel.getByRole('link', { name: release.recommendations.windows.label, exact: true });
          assert.equal(await action.getAttribute('href'), variant.artifactLocation);
        } else {
          assert.match(text, new RegExp(variant.statusLabel, 'i'));
          assert.ok(text.includes(variant.gate), `${variant.id} omits its unavailable reason`);
          assert.ok(text.includes(release.unavailableFacts.size), `${variant.id} omits unavailable size truth`);
          assert.ok(text.includes(release.unavailableFacts.integrity), `${variant.id} omits unavailable integrity truth`);
          assert.ok(text.includes(release.unavailableFacts.action), `${variant.id} omits unavailable action truth`);
          assert.equal(await panel.getByRole('link').count(), 0, `${variant.id} exposes an unavailable action`);
        }
      }

      const resources = page.getByRole('navigation', { name: 'Release resources' });
      await resources.waitFor();
      for (const resource of release.resources) {
        const link = resources.getByRole('link', { name: resource.label, exact: true });
        assert.equal(await link.getAttribute('href'), resource.href, `${resource.id} resource destination drifted`);
      }
      const history = page.locator('[data-release-history]');
      assert.match(await history.innerText(), new RegExp(release.history.statusLabel, 'i'));
      assert.match(await history.innerText(), new RegExp(release.history.summary, 'i'));
      assert.equal(
        await history.locator('[data-previous-release]').count(),
        release.history.acceptedVersions.length,
        'previous-version rendering disagrees with release authority',
      );
      const bodyText = await page.locator('body').innerText();
      assert.doesNotMatch(
        bodyText,
        /\b(?:Mumble for (?:iOS|Android)|iOS app|Android app|mobile app)\b/i,
        `${item.name} path implies a Mumble mobile app`,
      );
      await resources.getByRole('link', { name: 'Integrity guide', exact: true }).focus();
      await assertVisibleFocus(page, `${item.name} release-resource focus`);
      await noHorizontalOverflow(page, `${item.name} Downloads ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'}`);
      assertNoBrowserErrors(
        browserErrors,
        `${item.name} Downloads ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} browser errors`,
      );
      await context.close();
    }
  }

  const reducedContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: 'reduce',
  });
  const reducedPage = await reducedContext.newPage();
  await reducedPage.goto(`${origin}/downloads/`, { waitUntil: 'networkidle' });
  assert.equal(
    await reducedPage.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior),
    'auto',
    'reduced-motion Downloads keeps smooth scrolling',
  );
  await noHorizontalOverflow(reducedPage, 'reduced-motion mobile Downloads');
  await reducedContext.close();

  record('Downloads full platform matrix, authority facts, gated variants, resources, history, no-mobile-app promise, focus, reduced motion, JavaScript/no-JavaScript, and horizontal fit');
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
  const productLink = page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Product', exact: true });
  await productLink.focus();
  await page.keyboard.press('Enter');
  await page.waitForURL(`${origin}/product/`);
  assert.equal(await page.getByRole('button', { name: 'Open menu' }).getAttribute('aria-expanded'), 'false');
  await assertSharedShell(page, 'Product');
  await noHorizontalOverflow(page, 'mobile Product');

  await page.getByRole('button', { name: 'Open menu' }).click();
  const mobileDownloadsLink = page.getByRole('navigation', { name: 'Primary' })
    .getByRole('link', { name: 'Downloads', exact: true });
  await mobileDownloadsLink.waitFor();
  await Promise.all([
    page.waitForURL(`${origin}/downloads/`),
    mobileDownloadsLink.click(),
  ]);
  assert.equal(page.url(), `${origin}/downloads/`, 'mobile Product Downloads link did not reach Downloads');
  await assertSharedShell(page, 'Downloads');
  await noHorizontalOverflow(page, 'mobile Downloads');
  assertNoBrowserErrors(browserErrors, 'mobile navigation browser errors');
  await context.close();
  record('mobile Home-to-Product-to-Downloads visible-link journey, menu containment, Escape focus restoration, inertness, and horizontal fit');
}

const transcriptionRouteFacts = [
  'Captured input',
  'Processing location',
  'Temporary and durable data',
  'Durable output',
  'What leaves the device',
  'Requirements',
  'Controls',
  'Current limitations',
];
const findRouteFacts = [
  'Query input',
  'Processing location',
  'What leaves the device',
  'Output',
  'Controls',
  'Requirements',
  'Current limitations',
];

async function assertRouteTable(table, facts, name) {
  await table.waitFor();
  for (const fact of facts) {
    assert.equal(
      await table.getByRole('rowheader', { name: fact, exact: true }).count(),
      1,
      `${name} is missing the ${fact} semantic fact`,
    );
  }
}

async function privacyRoutes(browser) {
  const desktopContext = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
  });
  const desktopPage = await desktopContext.newPage();
  const desktopErrors = browserErrorsFor(desktopPage);

  await desktopPage.goto(`${origin}/privacy/`, { waitUntil: 'networkidle' });
  await assertSharedShell(desktopPage, 'Privacy');
  await desktopPage.getByRole('heading', { level: 1, name: /what stays on your computer/i }).waitFor();

  const explorer = desktopPage.getByRole('tablist', { name: 'Explore local routes' });
  const transcriptionTab = explorer.getByRole('tab', { name: /Local Transcription/i });
  const findTab = explorer.getByRole('tab', { name: /Mumble Find/i });
  const transcriptionPanel = desktopPage.locator('#local-transcription');
  const findPanel = desktopPage.locator('#mumble-find');
  const transcriptionTable = transcriptionPanel.getByRole('table', { name: 'Local Transcription data path' });
  const findTable = findPanel.getByRole('table', { name: 'Mumble Find data path' });

  assert.equal(await explorer.getAttribute('aria-orientation'), 'horizontal');
  assert.equal(await transcriptionTab.getAttribute('aria-selected'), 'true');
  assert.equal(await transcriptionTab.getAttribute('aria-controls'), 'local-transcription');
  assert.equal(await findTab.getAttribute('aria-selected'), 'false');
  assert.equal(await findTab.getAttribute('aria-controls'), 'mumble-find');
  assert.equal(await transcriptionPanel.getAttribute('role'), 'tabpanel');
  assert.equal(await transcriptionPanel.getAttribute('aria-labelledby'), 'privacy-tab-transcription');
  assert.equal(await findPanel.getAttribute('role'), 'tabpanel');
  assert.equal(await findPanel.getAttribute('aria-labelledby'), 'privacy-tab-find');
  assert.equal(await transcriptionPanel.isVisible(), true);
  assert.equal(await findPanel.isVisible(), false);
  await assertRouteTable(transcriptionTable, transcriptionRouteFacts, 'Local Transcription');
  assert.equal(
    await transcriptionPanel.getByRole('group', { name: /Local Transcription data-path explanation/i })
      .getByRole('listitem').count(),
    4,
  );

  await transcriptionTab.focus();
  await assertVisibleFocus(desktopPage, 'Privacy Local Transcription route focus');
  await desktopPage.keyboard.press('ArrowDown');
  assert.equal(
    await transcriptionTab.getAttribute('aria-selected'),
    'true',
    'desktop horizontal tablist intercepted the vertical scrolling key',
  );
  await desktopPage.keyboard.press('ArrowRight');
  assert.equal(await findTab.getAttribute('aria-selected'), 'true');
  assert.equal(await transcriptionPanel.isVisible(), false);
  assert.equal(await findPanel.isVisible(), true);
  assert.match(await desktopPage.evaluate(() => document.activeElement?.textContent?.trim() || ''), /Mumble Find/i);
  await assertVisibleFocus(desktopPage, 'Privacy Mumble Find route focus');
  await assertRouteTable(findTable, findRouteFacts, 'Mumble Find');
  assert.equal(
    await findPanel.getByRole('group', { name: /Mumble Find data-path explanation/i })
      .getByRole('listitem').count(),
    4,
  );

  const findText = await findPanel.innerText();
  assert.match(findText, /Windows Search.*SystemIndex/is);
  assert.match(findText, /versioned application catalogue/i);
  assert.match(findText, /no hosted results/i);
  assert.doesNotMatch(findText, /Google|Perplexity|Brave|Search online/i);

  await desktopPage.keyboard.press('ArrowLeft');
  assert.equal(await transcriptionTab.getAttribute('aria-selected'), 'true');
  assert.equal(await transcriptionPanel.isVisible(), true);
  assert.equal(await findPanel.isVisible(), false);
  const transcriptionText = await transcriptionPanel.innerText();
  assert.match(transcriptionText, /bounded.*recovery/is);
  assert.match(transcriptionText, /faster-whisper/i);
  assert.match(transcriptionText, /no account, provider key, or internet connection/i);
  assert.doesNotMatch(transcriptionText, /Google|Perplexity|Brave|Search online/i);

  const onlineBoundary = desktopPage.locator('.online-boundary');
  const onlineText = await onlineBoundary.innerText();
  assert.match(onlineText, /Google, Perplexity, or Brave/i);
  assert.match(onlineText, /Search online/i);
  assert.match(onlineText, /Keep private/i);
  assert.equal(
    await onlineBoundary.evaluate((element) => element.closest('.privacy-explorer') === null),
    true,
    'online-route boundary was nested inside the local explorer',
  );
  const websitePrivacyText = await desktopPage.locator('.website-privacy').innerText();
  assert.match(websitePrivacyText, /Zero-CDN/i);
  assert.match(websitePrivacyText, /no analytics/i);
  assert.match(websitePrivacyText, /no tracking/i);
  assert.doesNotMatch(await desktopPage.locator('body').innerText(), /Mumble Search/i);
  assert.equal(
    await explorer.getByRole('tab', { name: /Web Search/i }).count(),
    0,
    'Web Search was incorrectly presented as a local route',
  );
  await noHorizontalOverflow(desktopPage, 'desktop Privacy');
  assertNoBrowserErrors(desktopErrors, 'desktop Privacy browser errors');
  await desktopContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    reducedMotion: 'reduce',
  });
  const mobilePage = await mobileContext.newPage();
  const mobileErrors = browserErrorsFor(mobilePage);
  await mobilePage.goto(`${origin}/privacy/`, { waitUntil: 'networkidle' });
  await assertSharedShell(mobilePage, 'Privacy');
  const mobileExplorer = mobilePage.getByRole('tablist', { name: 'Explore local routes' });
  const mobileTranscriptionTab = mobileExplorer.getByRole('tab', { name: /Local Transcription/i });
  const mobileFindTab = mobileExplorer.getByRole('tab', { name: /Mumble Find/i });
  assert.equal(await mobileExplorer.getAttribute('aria-orientation'), 'vertical');
  await mobileTranscriptionTab.focus();
  await mobilePage.keyboard.press('ArrowDown');
  assert.equal(await mobileFindTab.getAttribute('aria-selected'), 'true');
  assert.equal(await mobilePage.locator('#local-transcription').isVisible(), false);
  assert.equal(await mobilePage.locator('#mumble-find').isVisible(), true);
  await assertVisibleFocus(mobilePage, 'mobile Privacy Mumble Find route focus');
  await mobilePage.waitForTimeout(250);
  assert.equal(
    await mobileFindTab.getAttribute('aria-selected'),
    'true',
    'reduced-motion route state changed without visitor input',
  );
  await assertRouteTable(
    mobilePage.locator('#mumble-find').getByRole('table', { name: 'Mumble Find data path' }),
    findRouteFacts,
    'mobile Mumble Find',
  );
  await noHorizontalOverflow(mobilePage, 'mobile reduced-motion Privacy');
  assertNoBrowserErrors(mobileErrors, 'mobile Privacy browser errors');
  await mobileContext.close();
  record('Privacy local-route explorer, complete semantic tables, orientation-aware keyboard tabs, online separation, reduced motion, and desktop/mobile fit');
}

async function noJavaScriptPath(browser) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, javaScriptEnabled: false });
    const page = await context.newPage();
    const browserErrors = browserErrorsFor(page);
    await page.goto(`${origin}/`, { waitUntil: 'load' });
    await assertSharedShell(page, 'Home');
    await page.getByRole('heading', { level: 1, name: /field where your words belong/i }).waitFor();
    for (const job of ['Write', 'Capture', 'Shape', 'Listen', 'Find']) {
      await page.getByRole('heading', { level: 3, name: job, exact: true }).waitFor();
    }
    const stage = page.locator('[data-job-story="write"][data-story-surface="home"]');
    assert.equal(await stage.getByRole('tabpanel').count(), 3, 'no-JavaScript Write states are incomplete');
    for (const panel of ['Choose cursor', 'Speak', 'Text returned']) {
      await stage.getByRole('tabpanel', { name: new RegExp(panel, 'i') }).waitFor();
    }
    const noScriptFindStage = page.locator('[data-job-story="find"][data-story-surface="home"]');
    assert.equal(await noScriptFindStage.count(), 1, 'no-JavaScript Home Find stage is missing');
    assert.equal(await noScriptFindStage.locator('[data-find-capture]').count(), 3, 'no-JavaScript Home product captures are incomplete');
    assert.equal(await noScriptFindStage.locator('[data-find-route]').count(), 2, 'no-JavaScript Home Find routes merged');
    assert.match(await noScriptFindStage.innerText(), /Private on this device/i);
    assert.match(await noScriptFindStage.innerText(), /Search online/i);
    assert.deepEqual(
      stopTimeDestinationFindings(
        await page.locator('main').innerText(),
        'home',
        `no-JavaScript Home ${viewport.width}px`,
      ),
      [],
    );
    if (viewport.width === 390) {
      const headerActionBox = await page.locator('.site-header .header-action').boundingBox();
      assert.ok(
        headerActionBox && headerActionBox.width >= 44 && headerActionBox.height >= 44,
        `no-JavaScript mobile header action is below 44x44px (${headerActionBox?.width ?? 0}x${headerActionBox?.height ?? 0}px)`,
      );
    }
    await noHorizontalOverflow(page, `no-JavaScript Home ${viewport.width}px`);

    const productLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Product', exact: true });
    await Promise.all([
      page.waitForURL(`${origin}/product/`),
      productLink.click(),
    ]);
    await assertSharedShell(page, 'Product');
    const productText = await page.locator('main').innerText();
    assert.match(productText, /deliberate global Dictate command/i);
    assert.match(productText, /Local transcription is the default/i);
    assert.match(productText, /saved in the Deck/i);
    const noScriptProductFind = page.locator('[data-job-story="find"][data-story-surface="product"]');
    assert.equal(await noScriptProductFind.count(), 1, 'no-JavaScript Product Find region is missing');
    assert.equal(await noScriptProductFind.locator('[data-find-boundary]').count(), 3, 'no-JavaScript Product Find boundaries merged');
    assert.match(await noScriptProductFind.innerText(), /browser could not open/i);
    assert.deepEqual(
      stopTimeDestinationFindings(productText, 'product', `no-JavaScript Product ${viewport.width}px`),
      [],
    );
    await noHorizontalOverflow(page, `no-JavaScript Product ${viewport.width}px`);

    const useCasesLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Use Cases', exact: true });
    await Promise.all([
      page.waitForURL(`${origin}/use-cases/`),
      useCasesLink.click(),
    ]);
    await assertSharedShell(page, 'Use Cases');
    for (const task of ['Everyday notes', 'Longer text', 'Across applications']) {
      await page.getByRole('heading', { level: 2, name: task }).waitFor();
    }
    const noScriptUseCasesFind = page.locator('[data-job-story="find"][data-story-surface="use-cases"]');
    assert.equal(await noScriptUseCasesFind.locator('article').count(), 4, 'no-JavaScript Find tasks are incomplete');
    assert.match(await noScriptUseCasesFind.innerText(), /Deterministic demonstration selected text · not user data/i);
    assert.deepEqual(
      stopTimeDestinationFindings(
        await page.locator('main').innerText(),
        'use-cases',
        `no-JavaScript Use Cases ${viewport.width}px`,
      ),
      [],
    );
    await noHorizontalOverflow(page, `no-JavaScript Use Cases ${viewport.width}px`);

    const privacyLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Privacy', exact: true });
    await privacyLink.waitFor();
    await Promise.all([
      page.waitForURL(`${origin}/privacy/`),
      privacyLink.click(),
    ]);
    assert.equal(
      page.url(),
      `${origin}/privacy/`,
      `no-JavaScript ${viewport.width}px Use Cases Privacy link did not reach Privacy`,
    );
    const noScriptExplorer = page.getByRole('navigation', { name: 'Explore local routes' });
    assert.equal(
      await noScriptExplorer.getByRole('link', { name: /Local Transcription/i }).getAttribute('href'),
      '#local-transcription',
    );
    assert.equal(
      await noScriptExplorer.getByRole('link', { name: /Mumble Find/i }).getAttribute('href'),
      '#mumble-find',
    );
    await page.getByRole('heading', { level: 2, name: 'Local Transcription' }).waitFor();
    await page.getByRole('heading', { level: 2, name: 'Mumble Find' }).waitFor();
    await assertRouteTable(
      page.getByRole('table', { name: 'Local Transcription data path' }),
      transcriptionRouteFacts,
      `no-JavaScript ${viewport.width}px Local Transcription`,
    );
    await assertRouteTable(
      page.getByRole('table', { name: 'Mumble Find data path' }),
      findRouteFacts,
      `no-JavaScript ${viewport.width}px Mumble Find`,
    );
    assert.equal(
      await page.getByRole('group', { name: /data-path explanation/i }).count(),
      2,
    );
    const onlineText = await page.locator('.online-boundary').innerText();
    assert.match(onlineText, /Google, Perplexity, or Brave/i);
    assert.match(onlineText, /Search online/i);
    assert.match(onlineText, /Keep private/i);
    for (const panel of [page.locator('#local-transcription'), page.locator('#mumble-find')]) {
      assert.doesNotMatch(await panel.innerText(), /Google|Perplexity|Brave|Search online/i);
    }
    assert.doesNotMatch(await page.locator('body').innerText(), /Mumble Search/i);
    await noHorizontalOverflow(page, `no-JavaScript Privacy ${viewport.width}px`);

    const downloadsLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Downloads', exact: true });
    await downloadsLink.waitFor();
    await Promise.all([
      page.waitForURL(`${origin}/downloads/`),
      downloadsLink.click(),
    ]);
    assert.equal(
      page.url(),
      `${origin}/downloads/`,
      `no-JavaScript ${viewport.width}px Use Cases Downloads link did not reach Downloads`,
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
  record('no-JavaScript desktop/mobile journeys retain complete Write and Find stages, distinct Deck/local/web boundaries, task sequences, release facts, download access, and horizontal fit');
}

let browser;
try {
  console.log(`Browser executable route: ${executablePath ?? 'Playwright-managed Chromium'}`);
  browser = await chromium.launch({ headless: true, executablePath });
  console.log(`Browser version: ${browser.version()}`);
  await desktopJourney(browser);
  await correctionConstraints(browser);
  await writeJourney(browser);
  await findJourney(browser);
  await platformRecommendations(browser);
  await downloadsContract(browser);
  await mobileMenu(browser);
  await noJavaScriptPath(browser);
  await privacyRoutes(browser);
  console.log(`Browser contract passed: ${results.length} visitor-behaviour groups.`);
} finally {
  await browser?.close();
  await new Promise((resolveClosed) => server.close(resolveClosed));
}
