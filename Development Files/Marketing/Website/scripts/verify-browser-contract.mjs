import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { chromium } from 'playwright';
import release from '../src/data/release.json' with { type: 'json' };

const websiteRoot = resolve(import.meta.dirname, '..');
const distRoot = resolve(websiteRoot, 'dist');
const canonicalJobs = JSON.parse(
  await readFile(resolve(websiteRoot, 'src', 'data', 'jobs.json'), 'utf8'),
);
const results = [];
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?.trim() || undefined;
const publicRepositoryUrl = 'https://github.com/mongre25-droid/mumble';
const publicIssuesUrl = 'https://github.com/mongre25-droid/mumble/issues';
const expectedPublicSupport = {
  id: 'support',
  label: 'Issue reporting',
  availability: 'available',
  href: publicIssuesUrl,
};

const helpArticleHeadings = [
  'Choose the accepted package',
  'Install the Windows candidate',
  'Complete first launch',
  'Make your first dictation',
  'Recover a result from the Deck',
  'Use effective global commands',
  ...canonicalJobs.map((job) => job.label),
  'Understand privacy routes',
  'Use Mumble accessibly',
  'Fix initial setup problems',
  'Follow release and source status',
];

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

async function assertAnchorVisibleBelowHeader(page, targetSelector, name) {
  const activated = await page.evaluate((selector) => {
    const target = document.querySelector(selector);
    return Boolean(target?.matches(':target'));
  }, targetSelector);
  assert.equal(activated, true, `${name}: link activation did not select the requested target`);
  await page.evaluate((selector) => {
    document.querySelector(selector)?.scrollIntoView({ behavior: 'instant', block: 'start' });
  }, targetSelector);
  const geometry = await page.evaluate((selector) => {
    const header = document.querySelector('[data-site-header]');
    const target = document.querySelector(selector);
    if (!header || !target) return null;
    const headerRect = header.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const headerPosition = getComputedStyle(header).position;
    const occludingHeaderBottom = ['fixed', 'sticky'].includes(headerPosition)
      ? Math.max(0, headerRect.bottom)
      : 0;
    return {
      headerPosition,
      occludingHeaderBottom,
      targetBottom: targetRect.bottom,
      targetTop: targetRect.top,
      viewportHeight: window.innerHeight,
    };
  }, targetSelector);
  assert.ok(geometry, `${name}: target or header is missing`);
  assert.ok(
    geometry.targetTop >= geometry.occludingHeaderBottom - 1
      && geometry.targetBottom > geometry.occludingHeaderBottom
      && geometry.targetTop < geometry.viewportHeight,
    `${name}: activated target is obscured or outside the viewport: ${JSON.stringify(geometry)}`,
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

async function assertReducedMotion(page, name) {
  const motion = await page.evaluate(() => {
    const input = document.querySelector('[data-help-search] input');
    return {
      scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
      transitionDuration: input ? getComputedStyle(input).transitionDuration : null,
    };
  });
  assert.equal(motion.scrollBehavior, 'auto', `${name} retains smooth scrolling`);
  assert.ok(motion.transitionDuration, `${name} search control is missing`);
  for (const duration of motion.transitionDuration.split(',')) {
    const seconds = duration.trim().endsWith('ms')
      ? Number.parseFloat(duration) / 1000
      : Number.parseFloat(duration);
    assert.ok(seconds <= 0.001, `${name} retains a ${duration.trim()} transition`);
  }
}

async function assertPublicProjectDestinations(browser) {
  const supportResource = release.resources.find((resource) => resource.id === 'support');
  assert.deepEqual(
    supportResource,
    expectedPublicSupport,
    'release support resource does not match the independently expected public destination',
  );

  const routes = ['/', '/privacy/', '/downloads/', '/help/', '/missing-public-support-probe'];
  for (const javaScriptEnabled of [true, false]) {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      javaScriptEnabled,
    });
    const page = await context.newPage();
    for (const route of routes) {
      await page.goto(`${origin}${route}`, { waitUntil: javaScriptEnabled ? 'networkidle' : 'load' });
      const issueLinks = await page.locator('a[href]').evaluateAll((anchors) =>
        anchors
          .map((anchor) => anchor.href)
          .filter((href) => {
            const destination = new URL(href);
            return (
              destination.hostname === 'github.com' &&
              destination.pathname.startsWith('/mongre25-droid/mumble/issues')
            );
          }),
      );
      assert.ok(
        issueLinks.length >= 1,
        `${route} omits public issue reporting with JavaScript ${javaScriptEnabled ? 'on' : 'off'}`,
      );
      assert.ok(
        issueLinks.every((href) => href === publicIssuesUrl),
        `${route} exposes a non-canonical issue-reporting destination`,
      );
      assert.equal(
        await page.locator(`footer a[href="${publicIssuesUrl}"]`).count(),
        1,
        `${route} footer omits canonical issue reporting with JavaScript ${javaScriptEnabled ? 'on' : 'off'}`,
      );
      assert.equal(
        await page.locator(`footer a[href="${publicRepositoryUrl}"]`).count(),
        1,
        `${route} footer omits public source inspection with JavaScript ${javaScriptEnabled ? 'on' : 'off'}`,
      );
      if (route === '/downloads/') {
        assert.equal(
          await page.locator(`.release-resources a[href="${publicIssuesUrl}"]`).count(),
          1,
          `Downloads resources omit canonical issue reporting with JavaScript ${javaScriptEnabled ? 'on' : 'off'}`,
        );
      }
      if (route === '/missing-public-support-probe') {
        assert.equal(
          await page.locator(`.not-found a[href="${publicIssuesUrl}"]`).count(),
          1,
          `404 recovery omits canonical issue reporting with JavaScript ${javaScriptEnabled ? 'on' : 'off'}`,
        );
      }
      assert.equal(await page.locator('a[href^="mailto:"]').count(), 0, `${route} invents a support email`);
      assert.doesNotMatch(
        await page.locator('body').innerText(),
        /source repository remains private|issue reporting is not publicly available|no public support or defect-reporting route/i,
        `${route} retains stale private-repository support copy`,
      );
    }
    await context.close();
  }
}

async function assertAcceptedPageGeometry(browser) {
  const cases = [
    {
      name: 'desktop',
      viewport: { width: 1440, height: 900 },
      privacy: { heroPadding: '128px', explorerPadding: '128px', routeTitle: '72px' },
      help: {
        categoryGap: '32px',
        categoryMarginTop: '40px',
        articleGap: '40px',
        articlePadding: '32px',
        articleTitle: '28px',
      },
    },
    {
      name: 'mobile',
      viewport: { width: 390, height: 844 },
      privacy: { heroPadding: '64px', explorerPadding: '64px', routeTitle: '39px' },
      help: {
        categoryGap: '24px',
        categoryMarginTop: '40px',
        articleGap: '16px',
        articlePadding: '32px',
        articleTitle: '20.8px',
      },
    },
  ];

  for (const javaScriptEnabled of [true, false]) {
    for (const item of cases) {
      const context = await browser.newContext({
        viewport: item.viewport,
        javaScriptEnabled,
      });
      const page = await context.newPage();
      await page.goto(`${origin}/privacy/`, { waitUntil: javaScriptEnabled ? 'networkidle' : 'load' });
      const tokens = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        return {
          space8: root.getPropertyValue('--space-8').trim(),
          space10: root.getPropertyValue('--space-10').trim(),
          typeTitle: root.getPropertyValue('--type-title').trim(),
          helpSpace8: root.getPropertyValue('--help-space-8').trim(),
          helpSpace10: root.getPropertyValue('--help-space-10').trim(),
          helpTypeTitle: root.getPropertyValue('--help-type-title').trim(),
        };
      });
      assert.deepEqual(
        tokens,
        {
          space8: '64px',
          space10: '128px',
          typeTitle: 'clamp(2.4rem, 5vw, 4.6rem)',
          helpSpace8: '2rem',
          helpSpace10: '2.5rem',
          helpTypeTitle: 'clamp(1.3rem, 2vw, 1.75rem)',
        },
        `${item.name} ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} token authority drifted`,
      );
      const privacyGeometry = await page.evaluate(() => ({
        heroPadding: getComputedStyle(document.querySelector('.privacy-hero')).paddingBlockStart,
        explorerPadding: getComputedStyle(document.querySelector('.privacy-explorer')).paddingBlockStart,
        routeTitle: getComputedStyle(
          document.querySelector('#local-transcription .privacy-route-heading h2'),
        ).fontSize,
      }));
      assert.deepEqual(
        privacyGeometry,
        item.privacy,
        `${item.name} ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} Privacy geometry drifted`,
      );

      await page.goto(`${origin}/help/`, { waitUntil: javaScriptEnabled ? 'networkidle' : 'load' });
      const helpGeometry = await page.evaluate(() => {
        const categories = getComputedStyle(document.querySelector('.help-category-grid'));
        const article = getComputedStyle(document.querySelector('.help-article'));
        const articleTitle = getComputedStyle(document.querySelector('.help-article > h3'));
        return {
          categoryGap: categories.columnGap,
          categoryMarginTop: categories.marginTop,
          articleGap: article.columnGap,
          articlePadding: article.paddingBlockStart,
          articleTitle: articleTitle.fontSize,
        };
      });
      assert.deepEqual(
        helpGeometry,
        item.help,
        `${item.name} ${javaScriptEnabled ? 'JavaScript' : 'no-JavaScript'} Help geometry drifted`,
      );
      await context.close();
    }
  }
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
        if ('href' in resource) {
          const link = resources.getByRole('link', { name: resource.label, exact: true });
          assert.equal(await link.getAttribute('href'), resource.href, `${resource.id} resource destination drifted`);
        } else {
          assert.equal(
            await resources.getByRole('link', { name: resource.label, exact: true }).count(),
            0,
            `${resource.id} gated resource became an active link`,
          );
          await resources.getByText(resource.statusLabel, { exact: true }).waitFor();
        }
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

async function helpJourney(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  const browserErrors = browserErrorsFor(page);

  await page.goto(`${origin}/help/`, { waitUntil: 'networkidle' });
  await assertSharedShell(page, 'Help');
  await page.getByRole('heading', { level: 1, name: /first successful dictation/i }).waitFor();
  await page.getByRole('navigation', { name: 'Popular help tasks' }).waitFor();
  await page.getByRole('navigation', { name: 'Browse Help topics' }).waitFor();
  await page.getByRole('navigation', { name: 'Choose Getting Started platform' }).waitFor();

  for (const heading of helpArticleHeadings) {
    await page.getByRole('heading', { level: 3, name: heading, exact: true }).waitFor();
  }

  for (const job of canonicalJobs) {
    const article = page.locator(`#${job.id}-guide`);
    await article.getByRole('heading', { level: 3, name: job.label, exact: true }).waitFor();
    assert.ok(
      (await article.innerText()).includes(job.summary),
      `${job.label} Help guidance does not render its canonical summary`,
    );
  }

  const platformLinks = [
    ['Read the Windows candidate path', '#install-windows'],
    ['Read macOS status', '#install-macos'],
    ['Read Linux status', '#install-linux'],
  ];
  for (const [name, href] of platformLinks) {
    assert.equal(
      await page.getByRole('link', { name, exact: true }).getAttribute('href'),
      href,
      `${name} does not reach its static platform path`,
    );
  }
  const macSection = page.locator('#install-macos');
  const linuxSection = page.locator('#install-linux');
  assert.match(await macSection.innerText(), /No accepted artifact/i);
  assert.match(await linuxSection.innerText(), /No accepted artifact/i);
  assert.equal(await macSection.getByRole('link', { name: /download/i }).count(), 0, 'macOS exposes a download');
  assert.equal(await linuxSection.getByRole('link', { name: /download/i }).count(), 0, 'Linux exposes a download');

  const helpText = await page.locator('body').innerText();
  assert.match(helpText, /Ctrl\s*\+\s*Windows/i);
  assert.match(helpText, /Ctrl\s*\+\s*Alt\s*\+\s*V/i);
  assert.match(helpText, /Ctrl\s*\+\s*Alt\s*\+\s*D/i);
  assert.match(helpText, /Listening.+Transcribing.+Inserted.+Sent.+Not inserted.+Delivery uncertain/is);
  assert.match(helpText, /result remains in (?:the )?Deck and History/i);
  assert.match(helpText, /Public release remains gated/i);
  assert.match(helpText, /Publisher signature.+Not accepted/is);
  assert.match(helpText, /70794b4d13c1c38662425deb5700865728955f4fac78dc2d083436f63fb99493/i);
  assert.doesNotMatch(helpText, /Mumble Search/i);
  assert.match(helpText, /public GitHub tracker for support and defect reports/i);
  assert.doesNotMatch(
    helpText,
    /source repository remains private|issue reporting is not publicly available|no public support or defect-reporting route/i,
  );
  assert.equal(
    await page.locator(`#release-source a[href="${publicIssuesUrl}"]`).count(),
    1,
    'Help release/source guidance omits point-of-need public issue reporting',
  );
  assert.equal(
    await page.locator(`.help-category-grid a[href="${publicIssuesUrl}"]`).count(),
    1,
    'Help project taxonomy omits public issue reporting',
  );
  assert.equal(
    await page.locator(`.help-empty a[href="${publicIssuesUrl}"]`).count(),
    1,
    'Help empty-search recovery omits public issue reporting',
  );

  const installText = await page.locator('#install-windows').innerText();
  assert.match(installText, /Install Mumble\.bat/i);
  assert.match(installText, /small\.en/i);
  assert.match(installText, /model ready/i);
  assert.match(installText, /All done/i);
  assert.match(installText, /unverified-publisher or security warning/i);
  const firstLaunchText = await page.locator('#first-launch').innerText();
  assert.match(firstLaunchText, /Local transcription/i);
  assert.match(firstLaunchText, /Settings\s*→\s*Speech to text/i);
  assert.match(firstLaunchText, /optional cloud transcription.+keys unconfigured/is);
  assert.match(firstLaunchText, /Home status to read Ready/i);

  const expectedDestinations = new Map([
    ['Downloads', '/downloads/'],
    ['Privacy boundaries', '/privacy/'],
    ['Product overview', '/#jobs'],
    ['Release notes', '/downloads/#release-notes-title'],
    ['Source repository', 'https://github.com/mongre25-droid/mumble'],
  ]);
  for (const [name, href] of expectedDestinations) {
    assert.equal(
      await page.getByRole('link', { name, exact: true }).first().getAttribute('href'),
      href,
      `${name} has the wrong Help destination`,
    );
  }

  const search = page.getByRole('searchbox', { name: 'Search Mumble Help' });
  await search.focus();
  await assertVisibleFocus(page, 'Help search focus');
  const articles = page.getByRole('article');
  const articleCount = await articles.count();
  assert.equal(articleCount, helpArticleHeadings.length, 'Help article contract drifted');
  await search.fill('microphone');
  await page.getByRole('status').filter({ hasText: /help topics? shown/i }).waitFor();
  const visibleCount = await articles.count();
  assert.ok(visibleCount > 0 && visibleCount < articleCount, 'Help search did not filter the static article set');
  for (let index = 0; index < visibleCount; index += 1) {
    assert.match(await articles.nth(index).innerText(), /microphone/i, 'Help search exposed an unrelated topic');
  }
  await page.keyboard.press('Escape');
  assert.equal(await search.inputValue(), '', 'Escape did not clear Help search');
  assert.equal(await articles.count(), articleCount, 'clearing Help search did not restore all topics');
  await assertReducedMotion(page, 'desktop Help reduced-motion mode');

  await noHorizontalOverflow(page, 'desktop Help');
  assertNoBrowserErrors(browserErrors, 'desktop Help browser errors');
  await context.close();
  record('Help search enhancement, canonical job authority, actual Island outcome labels, public source and issue reporting, platform gates, shortcuts, recovery, reduced motion, and keyboard focus');
}

async function mobileHelpJourney(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  const browserErrors = browserErrorsFor(page);

  await page.goto(`${origin}/help/`, { waitUntil: 'networkidle' });
  await assertSharedShell(page, 'Help');
  const search = page.getByRole('searchbox', { name: 'Search Mumble Help' });
  const articles = page.getByRole('article');
  const articleCount = await articles.count();
  await search.fill('model ready');
  await page.getByRole('status').filter({ hasText: /help topics? shown/i }).waitFor();
  const visibleCount = await articles.count();
  assert.ok(visibleCount > 0 && visibleCount < articleCount, 'mobile Help search did not filter the article set');
  for (let index = 0; index < visibleCount; index += 1) {
    assert.match(await articles.nth(index).innerText(), /model/i, 'mobile Help search exposed an unrelated topic');
  }

  const clearSearch = page.getByRole('button', { name: 'Clear search' });
  await clearSearch.focus();
  await assertVisibleFocus(page, 'mobile Help clear-search focus');
  await page.keyboard.press('Enter');
  assert.equal(await search.inputValue(), '', 'mobile Clear search did not clear the query');
  assert.equal(await articles.count(), articleCount, 'mobile Clear search did not restore the article set');

  const categoryLink = page.getByRole('navigation', { name: 'Browse Help topics' })
    .getByRole('link', { name: 'Install and verify', exact: true });
  await Promise.all([
    page.waitForURL(`${origin}/help/#install-windows`),
    categoryLink.click(),
  ]);
  await page.getByRole('heading', { level: 3, name: 'Install the Windows candidate', exact: true }).waitFor();

  const macosPath = page.getByRole('link', { name: 'Read macOS status', exact: true });
  await Promise.all([
    page.waitForURL(`${origin}/help/#install-macos`),
    macosPath.click(),
  ]);
  await page.locator('#install-macos-title').waitFor();

  const microphoneSummary = page.locator('#troubleshooting summary').filter({ hasText: 'Microphone' });
  await page.keyboard.press('Tab');
  await microphoneSummary.focus();
  await assertVisibleFocus(page, 'mobile Help troubleshooting disclosure focus');
  await page.keyboard.press('Enter');
  assert.equal(
    await microphoneSummary.evaluate((summary) => summary.parentElement?.open),
    true,
    'mobile Help troubleshooting disclosure did not open from the keyboard',
  );

  await assertReducedMotion(page, 'mobile Help reduced-motion mode');
  await noHorizontalOverflow(page, 'mobile Help');
  assertNoBrowserErrors(browserErrors, 'mobile Help browser errors');
  await context.close();
  record('mobile Help search, clear control, taxonomy and platform paths, disclosure keyboard use, reduced motion, and horizontal fit');
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

    const helpLink = page.getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Help', exact: true });
    await Promise.all([
      page.waitForURL(`${origin}/help/`),
      helpLink.click(),
    ]);
    await assertSharedShell(page, 'Help');
    if (viewport.width === 390) {
      const headerPosition = await page.locator('[data-site-header]').evaluate(
        (header) => getComputedStyle(header).position,
      );
      assert.ok(
        !['fixed', 'sticky'].includes(headerPosition),
        `no-JavaScript mobile header can obscure fragment targets: ${headerPosition}`,
      );
    }
    for (const heading of helpArticleHeadings) {
      await page.getByRole('heading', { level: 3, name: heading, exact: true }).waitFor();
    }
    assert.equal(
      await page.getByRole('searchbox', { name: 'Search Mumble Help' }).count(),
      0,
      'no-JavaScript Help exposes a non-functional search control',
    );
    await page.getByText(/Search enhancement needs JavaScript/i).waitFor();
    const articleIds = await page.locator('article[data-help-article]').evaluateAll(
      (articles) => articles.map((article) => article.id),
    );
    assert.equal(articleIds.length, helpArticleHeadings.length, 'no-JavaScript Help article contract drifted');
    for (const id of articleIds) {
      assert.ok(
        await page.locator(`a[href="#${id}"]`).count() > 0,
        `no-JavaScript Help article #${id} has no semantic navigation link`,
      );
    }
    const installPath = page.getByRole('navigation', { name: 'Browse Help topics' })
      .getByRole('link', { name: 'Install and verify', exact: true });
    await installPath.focus();
    await Promise.all([
      page.waitForURL(`${origin}/help/#install-windows`),
      page.keyboard.press('Enter'),
    ]);
    await page.getByRole('heading', { level: 3, name: 'Install the Windows candidate', exact: true }).waitFor();
    await assertAnchorVisibleBelowHeader(
      page,
      '#install-windows',
      `no-JavaScript Help install target ${viewport.width}px`,
    );
    const macosPath = page.getByRole('link', { name: 'Read macOS status', exact: true });
    await macosPath.focus();
    await Promise.all([
      page.waitForURL(`${origin}/help/#install-macos`),
      page.keyboard.press('Enter'),
    ]);
    await page.locator('#install-macos-title').waitFor();
    await assertAnchorVisibleBelowHeader(
      page,
      '#install-macos',
      `no-JavaScript Help macOS target ${viewport.width}px`,
    );
    const linuxPath = page.getByRole('link', { name: 'Read Linux status', exact: true });
    await linuxPath.focus();
    await Promise.all([
      page.waitForURL(`${origin}/help/#install-linux`),
      page.keyboard.press('Enter'),
    ]);
    await page.locator('#install-linux-title').waitFor();
    await assertAnchorVisibleBelowHeader(
      page,
      '#install-linux',
      `no-JavaScript Help Linux target ${viewport.width}px`,
    );
    await noHorizontalOverflow(page, `no-JavaScript Help ${viewport.width}px`);
    assertNoBrowserErrors(browserErrors, `no-JavaScript ${viewport.width}px browser errors`);
    await context.close();
  }
  record('no-JavaScript desktop/mobile Home-to-Downloads-to-Help journeys retain complete semantic Help reachability, non-sticky mobile navigation, visible activated article/platform targets, release truth, and horizontal fit');
}

let browser;
try {
  console.log(`Browser executable route: ${executablePath ?? 'Playwright-managed Chromium'}`);
  browser = await chromium.launch({ headless: true, executablePath });
  console.log(`Browser version: ${browser.version()}`);
  await assertPublicProjectDestinations(browser);
  await assertAcceptedPageGeometry(browser);
  await desktopJourney(browser);
  await helpJourney(browser);
  await mobileHelpJourney(browser);
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
