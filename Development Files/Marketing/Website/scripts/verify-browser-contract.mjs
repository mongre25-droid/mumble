import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { chromium } from 'playwright';
import release from '../src/data/release.json' with { type: 'json' };

const websiteRoot = resolve(import.meta.dirname, '..');
const distRoot = resolve(websiteRoot, 'dist');
const results = [];
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?.trim() || undefined;
const requestedGroup = process.env.MUMBLE_WEBSITE_BROWSER_GROUP?.trim() || undefined;

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

const routeFactNames = [
  'Input',
  'Local stage',
  'Egress',
  'Provider',
  'Network',
  'Key or account',
  'External cost',
  'Output',
  'User control',
  'Failure boundary',
];
const routeContracts = [
  {
    id: 'local-transcription',
    tabName: /Local Transcription/i,
    tableName: 'Local Transcription data path',
    evidence: [/faster-whisper/i, /nothing leaves/i, /no provider/i],
    facts: {
      Input: /one-channel audio.*microphone selected in Settings/is,
      'Local stage': /durable recovery segments.*faster-whisper.*local CPU/is,
      Egress: /nothing leaves this computer.*finished text only/is,
      Provider: /no external provider.*faster-whisper model/is,
      Network: /not required.*local speech model is present/is,
      'Key or account': /no Mumble account.*no provider key/is,
      'External cost': /no external provider charge.*computer's own processing and storage/is,
      Output: /saved in local History\/Deck.*target-bound insertion/is,
      'User control': /choose and test the microphone.*device-only override/is,
      'Failure boundary': /failure stays local.*never silently sends audio online/is,
    },
  },
  {
    id: 'cloud-transcription',
    tabName: /Cloud Transcription/i,
    tableName: 'Cloud Transcription data path',
    evidence: [/recorded audio/i, /Groq, OpenAI, or OpenRouter/i, /deliberately select/i],
    facts: {
      Input: /recorded microphone-audio clip.*effective route is Cloud/is,
      'Local stage': /durable local recovery segments.*freezes one route snapshot/is,
      Egress: /WAV representation of recorded audio.*effective provider.*finished text shaping is not part/is,
      Provider: /Groq, OpenAI, or OpenRouter.*does not switch providers/is,
      Network: /internet connection.*provider request and response/is,
      'Key or account': /own provider account.*matching API key.*no built-in key/is,
      'External cost': /provider may charge.*does not absorb that provider cost/is,
      Output: /provider's transcript.*local History\/Deck.*target-bound delivery/is,
      'User control': /deliberately select Cloud.*device-only override.*inactive/is,
      'Failure boundary': /missing key\/model.*blocks egress.*no silent online fallback/is,
    },
  },
  {
    id: 'text-shaping',
    tabName: /Text Shaping/i,
    tableName: 'Text Shaping data path',
    evidence: [/finished text/i, /Cerebras or OpenRouter/i, /never sends (?:the )?captured/i],
    facts: {
      Input: /finished transcript or selected text.*Prompt.*Reader summary/is,
      'Local stage': /offline cleanup and mode inference.*freezes the effective provider/is,
      Egress: /finished text.*never sends captured audio or a microphone recording/is,
      Provider: /Cerebras or OpenRouter.*confirmed selected model/is,
      Network: /internet connection.*effective shaping route is hosted/is,
      'Key or account': /own provider account.*matching API key.*no hosted-processing key/is,
      'External cost': /provider may charge.*between the user and that provider/is,
      Output: /shaped text returns.*saved locally.*target-bound path/is,
      'User control': /choose the action, provider.*hosted processing off.*device-only override/is,
      'Failure boundary': /missing key\/model.*blocks egress.*does not try another online provider/is,
    },
  },
  {
    id: 'reader-speech',
    tabName: /Reader speech/i,
    tableName: 'Reader speech data path',
    evidence: [/online text-to-speech/i, /OpenRouter or OpenAI/i, /provider credits/i],
    facts: {
      Input: /current text passage.*Reader document.*built-in phrase.*test a voice/is,
      'Local stage': /extracts and chunks document text locally.*Reader library.*bookmarks.*provider choice/is,
      Egress: /passage text.*speech model and voice request.*document file and microphone audio are not sent/is,
      Provider: /configured OpenRouter or OpenAI.*selected model.*frozen.*one synthesis attempt.*exact pair/is,
      Network: /internet connection.*each text-to-speech request/is,
      'Key or account': /own OpenRouter or OpenAI account.*matching API key.*no speech-provider key/is,
      'External cost': /speech consumes provider credits.*does not include or absorb/is,
      Output: /provider-generated audio.*playback.*progress and bookmarks.*locally/is,
      'User control': /choose provider, model, voice, speed.*device-only mode.*unavailable/is,
      'Failure boundary': /exhausted credits.*stops speech after that one attempt.*no local speech fallback.*no sibling-model fallback.*no switch/is,
    },
  },
  {
    id: 'mumble-find',
    tabName: /Mumble Find/i,
    tableName: 'Mumble Find data path',
    evidence: [/Windows Search.*SystemIndex/is, /no hosted results/i, /nothing leaves/i],
    facts: {
      Input: /words typed.*Mumble Find overlay/is,
      'Local stage': /versioned app catalogue.*Windows Search SystemIndex.*local workers/is,
      Egress: /nothing leaves this computer.*no hosted results.*no automatic Web Search fallback/is,
      Provider: /no external provider.*local application catalogue.*local search index/is,
      Network: /not required.*local query.*local result actions/is,
      'Key or account': /no Mumble account.*no provider key/is,
      'External cost': /no external provider charge/is,
      Output: /up to 12 text-first rows.*Open, Show in folder, or native drag/is,
      'User control': /separate global shortcut.*category filters.*local-index refresh/is,
      'Failure boundary': /stale operating-system index.*remain local.*never become Web Search/is,
    },
  },
  {
    id: 'web-search',
    tabName: /Web Search/i,
    tableName: 'Web Search data path',
    evidence: [/Google, Perplexity, or Brave/i, /Search online/i, /Keep private/i],
    facts: {
      Input: /words explicitly selected or dictated.*separate Web Search command/is,
      'Local stage': /query and selected provider.*bounded, expiring local request.*consent/is,
      Egress: /only after Search online.*selected words.*external provider's search URL/is,
      Provider: /Google, Perplexity, or Brave.*named in the confirmation before egress/is,
      Network: /internet connection.*working configured or default browser.*after consent/is,
      'Key or account': /no API key.*destination may apply its own account/is,
      'External cost': /Mumble charges nothing.*supplies no provider access.*remain external/is,
      Output: /confirmed external browser destination.*not hosted results.*only when the browser opener confirms/is,
      'User control': /choose the provider.*provider-named confirmation.*Keep private or Search online/is,
      'Failure boundary': /blank input, Keep private, expiry, replay.*no successful external-open claim.*Mumble Find never falls back/is,
    },
  },
];

async function assertRouteTable(table, expectedFacts, name) {
  await table.waitFor();
  assert.deepEqual(
    Object.keys(expectedFacts),
    routeFactNames,
    `${name} independent fact oracle is incomplete or out of order`,
  );
  assert.equal(
    await table.locator('tbody tr').count(),
    routeFactNames.length,
    `${name} does not contain exactly ten semantic fact rows`,
  );
  for (const [fact, expectedValue] of Object.entries(expectedFacts)) {
    const rowHeader = table.getByRole('rowheader', { name: fact, exact: true });
    assert.equal(
      await rowHeader.count(),
      1,
      `${name} is missing the ${fact} semantic fact`,
    );
    const row = rowHeader.locator('xpath=ancestor::tr');
    assert.equal(await row.count(), 1, `${name} ${fact} is not bound to one table row`);
    const valueCell = row.getByRole('cell');
    assert.equal(await valueCell.count(), 1, `${name} ${fact} is not bound to one value cell`);
    const actualValue = (await valueCell.innerText()).trim();
    assert.notEqual(actualValue, '', `${name} ${fact} value is empty`);
    assert.match(
      actualValue,
      expectedValue,
      `${name} ${fact} value does not match its independent expected fact`,
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
  await desktopPage.getByRole('heading', { level: 1, name: /when anything leaves your computer/i }).waitFor();

  const explorer = desktopPage.getByRole('tablist', { name: 'Explore privacy routes' });
  const tabs = routeContracts.map((route) => explorer.getByRole('tab', { name: route.tabName }));
  const panels = routeContracts.map((route) => desktopPage.locator(`#${route.id}`));
  assert.equal(await explorer.getByRole('tab').count(), 6);
  assert.equal(await explorer.getAttribute('aria-orientation'), 'horizontal');

  for (const [index, route] of routeContracts.entries()) {
    const tab = tabs[index];
    const panel = panels[index];
    assert.equal(await tab.getAttribute('aria-controls'), route.id);
    assert.equal(await panel.getAttribute('role'), 'tabpanel');
    assert.equal(await panel.getAttribute('aria-labelledby'), await tab.getAttribute('id'));
    assert.equal(await tab.getAttribute('aria-selected'), String(index === 0));
    assert.equal(await panel.isVisible(), index === 0);
  }

  await tabs[0].focus();
  await assertVisibleFocus(desktopPage, 'Privacy Local Transcription route focus');
  await desktopPage.keyboard.press('ArrowDown');
  assert.equal(
    await tabs[0].getAttribute('aria-selected'),
    'true',
    'desktop horizontal tablist intercepted the vertical scrolling key',
  );

  for (const [index, route] of routeContracts.entries()) {
    if (index > 0) await desktopPage.keyboard.press('ArrowRight');
    assert.equal(await tabs[index].getAttribute('aria-selected'), 'true');
    assert.equal(await panels[index].isVisible(), true);
    await assertVisibleFocus(desktopPage, `Privacy ${route.id} route focus`);
    await assertRouteTable(
      panels[index].getByRole('table', { name: route.tableName }),
      route.facts,
      route.tableName,
    );
    assert.equal(
      await panels[index].getByRole('group', { name: /data-path explanation/i })
        .getByRole('listitem').count(),
      4,
      `${route.tableName} visual route is incomplete`,
    );
    const panelText = await panels[index].innerText();
    for (const evidence of route.evidence) assert.match(panelText, evidence);
  }

  await desktopPage.keyboard.press('Home');
  assert.equal(await tabs[0].getAttribute('aria-selected'), 'true');
  await desktopPage.keyboard.press('End');
  assert.equal(await tabs.at(-1).getAttribute('aria-selected'), 'true');

  const localTranscriptionText = await panels[0].innerText();
  const cloudTranscriptionText = await panels[1].innerText();
  const textShapingText = await panels[2].innerText();
  const readerSpeechText = await panels[3].innerText();
  const findText = await panels[4].innerText();
  const webSearchText = await panels[5].innerText();
  assert.doesNotMatch(localTranscriptionText, /Google|Perplexity|Brave|Search online/i);
  assert.match(cloudTranscriptionText, /own provider account.*API key/is);
  assert.match(cloudTranscriptionText, /provider may charge/i);
  assert.match(cloudTranscriptionText, /never switches to another online provider/i);
  assert.match(textShapingText, /offline cleanup and mode inference/i);
  assert.match(textShapingText, /missing key|device-only/i);
  assert.match(readerSpeechText, /not local playback/i);
  assert.match(readerSpeechText, /one synthesis attempt with that exact pair/i);
  assert.match(readerSpeechText, /no sibling-model fallback/i);
  assert.match(findText, /versioned application catalogue/i);
  assert.doesNotMatch(findText, /Google|Perplexity|Brave|Search online/i);
  assert.match(webSearchText, /provider-named confirmation/i);
  assert.match(webSearchText, /browser.*confirm/is);
  assert.doesNotMatch(await desktopPage.locator('body').innerText(), /Mumble Search/i);

  const websitePrivacyText = await desktopPage.locator('.website-privacy').innerText();
  assert.match(websitePrivacyText, /Zero-CDN/i);
  assert.match(websitePrivacyText, /no analytics/i);
  assert.match(websitePrivacyText, /no tracking/i);
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
  const mobileExplorer = mobilePage.getByRole('tablist', { name: 'Explore privacy routes' });
  const mobileTabs = routeContracts.map((route) => mobileExplorer.getByRole('tab', { name: route.tabName }));
  assert.equal(await mobileExplorer.getAttribute('aria-orientation'), 'vertical');
  await mobileTabs[0].focus();
  await mobilePage.keyboard.press('ArrowDown');
  assert.equal(await mobileTabs[1].getAttribute('aria-selected'), 'true');
  assert.equal(await mobilePage.locator('#cloud-transcription').isVisible(), true);
  await assertVisibleFocus(mobilePage, 'mobile Privacy Cloud Transcription route focus');
  await mobilePage.keyboard.press('End');
  assert.equal(await mobileTabs.at(-1).getAttribute('aria-selected'), 'true');
  assert.equal(await mobilePage.locator('#web-search').isVisible(), true);
  await mobilePage.waitForTimeout(250);
  assert.equal(
    await mobileTabs.at(-1).getAttribute('aria-selected'),
    'true',
    'reduced-motion route state changed without visitor input',
  );
  await assertRouteTable(
    mobilePage.locator('#web-search').getByRole('table', { name: 'Web Search data path' }),
    routeContracts.at(-1).facts,
    'mobile Web Search',
  );
  await noHorizontalOverflow(mobilePage, 'mobile reduced-motion Privacy');
  assertNoBrowserErrors(mobileErrors, 'mobile Privacy browser errors');
  await mobileContext.close();
  record('Privacy six-route explorer, complete semantic tables, local-first consent boundaries, orientation-aware keyboard tabs, reduced motion, and desktop/mobile fit');
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
    const privacyLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Privacy', exact: true });
    await privacyLink.waitFor();
    await noHorizontalOverflow(page, `no-JavaScript Home ${viewport.width}px`);

    await Promise.all([
      page.waitForURL(`${origin}/privacy/`),
      privacyLink.click(),
    ]);
    assert.equal(
      page.url(),
      `${origin}/privacy/`,
      `no-JavaScript ${viewport.width}px Home Privacy link did not reach Privacy`,
    );
    const noScriptExplorer = page.getByRole('navigation', { name: 'Explore privacy routes' });
    for (const route of routeContracts) {
      assert.equal(
        await noScriptExplorer.getByRole('link', { name: route.tabName }).getAttribute('href'),
        `#${route.id}`,
      );
      const panel = page.locator(`#${route.id}`);
      assert.equal(await panel.isVisible(), true, `no-JavaScript ${route.id} route is hidden`);
      await assertRouteTable(
        panel.getByRole('table', { name: route.tableName }),
        route.facts,
        `no-JavaScript ${viewport.width}px ${route.tableName}`,
      );
    }
    assert.equal(
      await page.getByRole('group', { name: /data-path explanation/i }).count(),
      6,
    );
    const localTranscriptionText = await page.locator('#local-transcription').innerText();
    const cloudTranscriptionText = await page.locator('#cloud-transcription').innerText();
    const textShapingText = await page.locator('#text-shaping').innerText();
    const readerSpeechText = await page.locator('#reader-speech').innerText();
    const findText = await page.locator('#mumble-find').innerText();
    const webSearchText = await page.locator('#web-search').innerText();
    assert.doesNotMatch(localTranscriptionText, /Google|Perplexity|Brave|Search online/i);
    assert.match(cloudTranscriptionText, /Groq, OpenAI, or OpenRouter/i);
    assert.match(textShapingText, /finished text/i);
    assert.match(readerSpeechText, /online text-to-speech/i);
    assert.doesNotMatch(findText, /Google|Perplexity|Brave|Search online/i);
    assert.match(webSearchText, /Google, Perplexity, or Brave/i);
    assert.match(webSearchText, /Search online/i);
    assert.match(webSearchText, /Keep private/i);
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
  if (requestedGroup === 'privacy') {
    await privacyRoutes(browser);
  } else if (requestedGroup) {
    throw new Error(`Unknown browser contract group: ${requestedGroup}`);
  } else {
    await desktopJourney(browser);
    await platformRecommendations(browser);
    await downloadsContract(browser);
    await mobileMenu(browser);
    await noJavaScriptPath(browser);
    await privacyRoutes(browser);
  }
  console.log(`Browser contract passed: ${results.length} visitor-behaviour groups.`);
} finally {
  await browser?.close();
  await new Promise((resolveClosed) => server.close(resolveClosed));
}
