import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { chromium } from 'playwright';
import jobs from '../src/data/jobs.json' with { type: 'json' };
import release from '../src/data/release.json' with { type: 'json' };

const websiteRoot = resolve(import.meta.dirname, '..');
const distRoot = resolve(websiteRoot, 'dist');
const canonicalJobs = JSON.parse(
  await readFile(resolve(websiteRoot, 'src', 'data', 'jobs.json'), 'utf8'),
);
const results = [];
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?.trim() || undefined;
const requestedGroup = process.env.MUMBLE_WEBSITE_BROWSER_GROUP?.trim() || undefined;
const linkedResource = (id) => {
  const resource = release.resources.find((candidate) => candidate.id === id);
  assert.ok(resource && 'href' in resource, `canonical release resource ${id} has no destination`);
  return resource;
};
const sourceResource = linkedResource('source');
const licenceResource = linkedResource('licence');
const supportResource = linkedResource('support');
assert.equal(supportResource.availability, 'available', 'canonical support resource is not available');
const publicRepositoryUrl = sourceResource.href;
const publicIssuesUrl = supportResource.href;
const publicLicenceUrl = licenceResource.href;

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
  'Troubleshoot by symptom',
  'Know the current limitations',
  'Follow releases, inspect source, or contribute',
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

async function assertDeferredMediaContract(browser) {
  const routes = ['/', '/product/', '/use-cases/', '/privacy/', '/downloads/', '/help/', '/missing-media-probe'];
  const viewports = [
    { name: 'desktop', size: { width: 1440, height: 900 } },
    { name: 'mobile', size: { width: 390, height: 844 } },
  ];
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport: viewport.size });
    const page = await context.newPage();
    for (const route of routes) {
      await page.goto(`${origin}${route}`, { waitUntil: 'networkidle' });
      const media = await page.locator('main img').evaluateAll((images) => images.map((image) => {
        const rect = image.getBoundingClientRect();
        const firstMontageMedia = Boolean(image.closest('[data-home-panel="write"]'));
        return {
          alt: image.getAttribute('alt'),
          fetchPriority: image.getAttribute('fetchpriority') ?? 'auto',
          firstMontageMedia,
          height: image.getAttribute('height'),
          loading: image.getAttribute('loading') ?? 'eager',
          src: image.getAttribute('src'),
          top: rect.top + window.scrollY,
          viewportHeight: window.innerHeight,
          width: image.getAttribute('width'),
        };
      }));
      for (const image of media) {
        assert.ok(Number(image.width) > 0 && Number(image.height) > 0, `${route} has undimensioned media: ${JSON.stringify(image)}`);
        assert.ok(image.alt?.trim(), `${route} media loses its textual meaning: ${JSON.stringify(image)}`);
        const eagerOrHigh = image.loading !== 'lazy' || image.fetchPriority === 'high';
        if (!eagerOrHigh) continue;

        // The first montage image is the sole priority exception, and only while
        // its real geometry places it in or immediately beside the opening view.
        assert.ok(
          image.firstMontageMedia && image.top < image.viewportHeight * 1.25,
          `${viewport.name} ${route} eagerly loads below-opening media: ${JSON.stringify(image)}`,
        );
      }
    }
    await context.close();
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

function homeMontageExpectations() {
  const resolveMedia = (job) => {
    const reference = job.homeMontage?.media;
    if (reference?.kind === 'demonstration-presentation') {
      return job.story?.demonstration?.presentation;
    }
    if (reference?.kind === 'demonstration-state') {
      return job.story?.demonstration?.states?.find((state) => state.id === reference.stateId);
    }
    if (reference?.kind === 'job-demonstration-presentation') {
      return jobs.find((candidate) => candidate.id === reference.jobId)?.story?.demonstration?.presentation;
    }
    if (reference?.kind === 'presentation-capture') {
      return job.story?.presentation?.captures?.find((capture) => capture.route === reference.route);
    }
    assert.fail(`${job.id} has no supported canonical Home montage media reference`);
  };

  return jobs.map((job) => {
    const montage = job.homeMontage;
    assert.ok(montage, `${job.id} has no canonical Home montage projection`);
    const media = resolveMedia(job);
    assert.ok(media, `${job.id} Home montage media reference does not resolve`);
    return {
      id: job.id,
      label: job.label,
      action: montage.action,
      title: montage.title,
      body: montage.body,
      evidence: montage.evidence,
      route: montage.route ?? [],
      routeLabel: montage.routeLabel,
      media: {
        src: media.src ?? media.image,
        width: String(media.width),
        height: String(media.height),
        alt: montage.mediaAlt ?? media.alt,
      },
    };
  });
}

async function homeMontageJourney(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36',
  });
  await context.addInitScript(() => {
    const mediaDevices = navigator.mediaDevices ?? {};
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { ...mediaDevices, getUserMedia: (...args) => { window.__homeMicrophoneCalls.push(args); return Promise.reject(new Error('Website microphone use is forbidden')); } },
    });
    window.__homeMicrophoneCalls = [];
  });
  const page = await context.newPage();
  await page.goto(`${origin}/`, { waitUntil: 'networkidle' });

  await page.getByRole('heading', { level: 1, name: /speak once/i }).waitFor();
  const primary = page.locator('[data-home-primary]');
  assert.equal(await primary.getAttribute('data-platform-action'), '');
  const stage = page.locator('[data-home-montage]');
  await stage.waitFor();
  const expectedGuidedJobList = new Intl.ListFormat('en', { style: 'long', type: 'conjunction' })
    .format(jobs.map((job) => job.label));
  assert.equal(await stage.getAttribute('aria-label'), `${expectedGuidedJobList} guided product tour`);
  assert.equal(await stage.getAttribute('data-home-state'), 'playing');
  assert.equal(await stage.locator('[data-home-play]').isDisabled(), true);
  assert.equal(await stage.locator('[data-home-pause]').isEnabled(), true);
  assert.equal(await stage.locator('[data-home-play]').getAttribute('aria-label'), 'Guided pass is playing');
  assert.equal(await stage.locator('[data-home-step-select]').count(), 5);
  assert.equal(await stage.locator('[data-home-panel]').count(), 5);
  const montageExpectations = homeMontageExpectations();
  for (const expected of montageExpectations) {
    const tabId = `home-montage-tab-${expected.id}`;
    const panelId = `home-montage-panel-${expected.id}`;
    const tab = stage.getByRole('tab', { name: `${expected.label}: ${expected.action}`, exact: true });
    const panel = stage.locator(`[data-home-panel="${expected.id}"]`);
    await tab.waitFor();
    assert.equal(await tab.getAttribute('id'), tabId, `${expected.label} tab id is not stable`);
    assert.equal(await tab.getAttribute('aria-controls'), panelId, `${expected.label} tab does not own its panel`);
    assert.equal(await panel.getAttribute('role'), 'tabpanel', `${expected.label} panel has no tabpanel role`);
    assert.equal(await panel.getAttribute('id'), panelId, `${expected.label} panel id is not stable`);
    assert.equal(await panel.getAttribute('aria-labelledby'), tabId, `${expected.label} panel is not named by its tab`);

    const copy = panel.locator('.home-montage-copy');
    assert.equal(await copy.locator(':scope > span').innerText(), expected.label);
    assert.equal(await copy.locator(':scope > h2').innerText(), expected.title);
    assert.equal(await copy.locator(':scope > p').innerText(), expected.action);
    assert.equal(await copy.locator(':scope > small').innerText(), expected.body);
    assert.deepEqual(await copy.locator('.home-shape-route > li').allInnerTexts(), expected.route);
    if (expected.route.length > 0) {
      assert.equal(await copy.locator('.home-shape-route').getAttribute('aria-label'), expected.routeLabel);
    }
    const image = panel.locator('figure img');
    assert.equal(await image.getAttribute('src'), expected.media.src);
    assert.equal(await image.getAttribute('width'), expected.media.width);
    assert.equal(await image.getAttribute('height'), expected.media.height);
    assert.equal(await image.getAttribute('alt'), expected.media.alt);
    assert.equal(await panel.locator('figcaption').innerText(), expected.evidence);
  }
  const viewportProof = await page.evaluate(() => {
    const stage = document.querySelector('[data-home-montage]').getBoundingClientRect();
    const primary = document.querySelector('[data-home-primary]').getBoundingClientRect();
    return { stageTop: stage.top, stageArea: stage.width * stage.height, primaryTop: primary.top };
  });
  assert.ok(viewportProof.stageTop < 900 && viewportProof.primaryTop < 900, `Home offer escapes first viewport: ${JSON.stringify(viewportProof)}`);
  assert.ok(viewportProof.stageArea > 300000, `Home stage is not dominant: ${JSON.stringify(viewportProof)}`);

  const writeTab = stage.getByRole('tab', { name: /^Write:/ });
  const captureTab = stage.getByRole('tab', { name: /^Capture:/ });
  const shapeTab = stage.getByRole('tab', { name: /^Shape:/ });
  const findTab = stage.getByRole('tab', { name: /^Find:/ });
  await writeTab.click();
  for (const [index, expected] of montageExpectations.entries()) {
    const tab = stage.getByRole('tab', { name: new RegExp(`^${expected.label}:`) });
    const panel = stage.locator(`[data-home-panel="${expected.id}"]`);
    assert.equal(await tab.getAttribute('aria-selected'), String(index === 0), `${expected.label} tab selection drifted`);
    assert.equal(await tab.getAttribute('tabindex'), index === 0 ? '0' : '-1', `${expected.label} tab roving tabindex drifted`);
    assert.equal(await panel.isHidden(), index !== 0, `${expected.label} panel hidden state drifted`);
  }
  const accessibilityTree = await stage.ariaSnapshot();
  assert.match(accessibilityTree, /tablist "Choose a Mumble job"/);
  assert.match(accessibilityTree, /tab "Write: Speak into the field already in front of you\." \[selected\]/);
  assert.match(accessibilityTree, /tabpanel "Write: Speak into the field already in front of you\."/);

  await writeTab.focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await captureTab.getAttribute('aria-selected'), 'true');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'home-montage-tab-capture');
  await assertVisibleFocus(page, 'Home Capture tab focus after ArrowRight');
  await page.keyboard.press('ArrowLeft');
  assert.equal(await writeTab.getAttribute('aria-selected'), 'true');
  await page.keyboard.press('End');
  assert.equal(await findTab.getAttribute('aria-selected'), 'true');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'home-montage-tab-find');
  await page.keyboard.press('Home');
  assert.equal(await writeTab.getAttribute('aria-selected'), 'true');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'home-montage-tab-write');

  await shapeTab.click();
  assert.equal(await stage.getAttribute('data-home-step'), '3');
  assert.equal(await stage.getAttribute('data-home-state'), 'manual');
  assert.equal(await shapeTab.getAttribute('aria-selected'), 'true');
  assert.equal(await stage.locator('[data-home-panel="shape"]').isHidden(), false);
  assert.equal(await stage.locator('[data-home-panel="write"]').isHidden(), true);
  assert.equal(await stage.locator('[data-home-play]').isEnabled(), true);
  assert.equal(await stage.locator('[data-home-pause]').isDisabled(), true);
  await stage.locator('[data-home-next]').click();
  assert.equal(await stage.getAttribute('data-home-step'), '4');
  await stage.locator('[data-home-previous]').click();
  assert.equal(await stage.getAttribute('data-home-step'), '3');
  await stage.locator('[data-home-play]').click();
  assert.equal(await stage.getAttribute('data-home-state'), 'playing');
  assert.equal(await stage.locator('[data-home-play]').isDisabled(), true);
  assert.equal(await stage.locator('[data-home-pause]').isEnabled(), true);
  await stage.locator('[data-home-pause]').click();
  assert.equal(await stage.getAttribute('data-home-state'), 'paused');
  assert.equal(await stage.locator('[data-home-play]').getAttribute('aria-label'), 'Resume guided pass');
  assert.equal(await stage.locator('[data-home-pause]').isDisabled(), true);
  await stage.locator('[data-home-play]').click();
  await page.waitForFunction(() => document.querySelector('[data-home-montage]')?.dataset.homeState === 'settled', null, { timeout: 15000 });
  assert.equal(await stage.getAttribute('data-home-pass-count'), '1');
  assert.equal(await stage.locator('[data-home-play]').isDisabled(), true, 'Play remains available after the only pass');
  assert.equal(await stage.locator('[data-home-pause]').isDisabled(), true, 'Pause remains available while settled');
  assert.equal(await stage.locator('[data-home-play]').getAttribute('aria-label'), 'Guided pass complete');
  assert.equal(await stage.locator('[data-home-pause]').getAttribute('aria-label'), 'Guided pass is not playing');
  await page.waitForTimeout(1800);
  assert.equal(await stage.getAttribute('data-home-state'), 'settled', 'Home montage restarted after its single pass');
  assert.equal(await stage.getAttribute('data-home-pass-count'), '1', 'Home montage looped');
  assert.equal(await page.evaluate(() => window.__homeMicrophoneCalls.length), 0, 'Home requested microphone access');

  const bodyText = await page.locator('body').innerText();
  assert.match(bodyText, /local transcription/i);
  assert.match(bodyText, /optional online routes/i);
  assert.match(bodyText, /Mumble Find.*local|local.*Mumble Find/is);
  assert.match(bodyText, /Web Search.*consent|consent.*Web Search/is);
  assert.match(bodyText, /Free.*MIT.*no (?:Mumble )?account/is);
  assert.match(bodyText, /provider.*may charge/is);
  assert.doesNotMatch(bodyText, /testimonial|customers love|pricing|per month|trusted by|\d+[km]\+ users/i);
  await page.locator('[data-home-ending]').getByRole('link', { name: /help/i }).waitFor();
  await page.locator('[data-home-ending] [data-platform-action]').waitFor();
  await noHorizontalOverflow(page, 'desktop Issue #42 Home');
  await context.close();

  for (const mode of ['reduced', 'save-data']) {
    const constrained = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: mode === 'reduced' ? 'reduce' : 'no-preference' });
    if (mode === 'save-data') {
      await constrained.addInitScript(() => Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } }));
    }
    const constrainedPage = await constrained.newPage();
    await constrainedPage.goto(`${origin}/`, { waitUntil: 'networkidle' });
    const constrainedStage = constrainedPage.locator('[data-home-montage]');
    assert.equal(await constrainedStage.getAttribute('data-home-state'), 'manual', `${mode} Home montage auto-played`);
    assert.equal(await constrainedStage.locator('[data-home-step-select]').count(), 5);
    await constrainedStage.getByRole('tab', { name: /^Find:/ }).click();
    assert.equal(await constrainedStage.getAttribute('data-home-step'), '5');
    await noHorizontalOverflow(constrainedPage, `${mode} Issue #42 Home`);
    await constrained.close();
  }

  const noJs = await browser.newContext({ viewport: { width: 390, height: 844 }, javaScriptEnabled: false });
  const noJsPage = await noJs.newPage();
  await noJsPage.goto(`${origin}/`, { waitUntil: 'networkidle' });
  const noJsStage = noJsPage.locator('[data-home-montage]');
  assert.equal(await noJsStage.locator('[data-home-panel]:visible').count(), 5);
  assert.equal(await noJsStage.getByRole('tabpanel').count(), 5);
  assert.equal(await noJsStage.locator('[data-home-controls]:visible').count(), 0);
  for (const label of ['Write', 'Capture', 'Shape', 'Listen', 'Find']) {
    assert.match(await noJsStage.innerText(), new RegExp(label));
  }
  await noHorizontalOverflow(noJsPage, 'no-JavaScript Issue #42 Home');
  await noJs.close();
  record('Issue #42 Home first-viewport offer, five-job guided montage, direct/previous/next/play/pause controls, one-pass settlement, reduced-motion/save-data/no-JavaScript alternatives, privacy/trust truth, platform and Help ending, zero microphone use, and desktop/mobile fit');
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
        fetchPriority: element.getAttribute('fetchpriority') ?? 'auto',
        loading: element.getAttribute('loading') ?? 'eager',
      })),
      {
        src: capture.src,
        width: String(capture.width),
        height: String(capture.height),
        naturalWidth: capture.width,
        naturalHeight: capture.height,
        alt: capture.alt,
        fetchPriority: capture.fetchPriority ?? 'auto',
        loading: capture.loading,
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


async function assertCaptureStage(page, viewportName) {
  const stage = page.locator('[data-job-story="capture"][data-story-surface="home"]');
  await stage.getByRole('heading', { level: 2, name: /complete Capture path/i }).waitFor();
  assert.match(await stage.innerText(), /Genuine Mumble interface/i);
  assert.match(await stage.innerText(), /demonstration meeting data/i);
  assert.match(await stage.innerText(), /website does not use your microphone/i);

  const tabs = stage.getByRole('tab');
  assert.equal(await tabs.count(), 3, `${viewportName} Capture stage does not expose three direct steps`);
  const recordTab = stage.getByRole('tab', { name: 'Record', exact: true });
  await recordTab.focus();
  await assertVisibleFocus(page, `${viewportName} Capture direct-step focus`);
  await page.keyboard.press('Enter');
  assert.equal(await recordTab.getAttribute('aria-selected'), 'true');
  const recordPanel = stage.getByRole('tabpanel', { name: 'Record', exact: true });
  await recordPanel.waitFor();
  assert.match(await recordPanel.innerText(), /Pause, resume, or Stop & save/i);

  await page.keyboard.press('ArrowRight');
  const reviewTab = stage.getByRole('tab', { name: 'Find record', exact: true });
  assert.equal(await reviewTab.getAttribute('aria-selected'), 'true');
  assert.equal(await reviewTab.evaluate((element) => document.activeElement === element), true);
  const reviewPanel = stage.getByRole('tabpanel', { name: 'Find record', exact: true });
  await reviewPanel.waitFor();
  assert.match(await reviewPanel.innerText(), /Search the saved library/i);
  await page.waitForTimeout(550);
  assert.equal(
    await reviewTab.getAttribute('aria-selected'),
    'true',
    `${viewportName} reduced-motion Capture stage advanced without visitor input`,
  );
  await stage.getByRole('button', { name: 'Previous Capture step' }).click();
  assert.equal(await recordTab.getAttribute('aria-selected'), 'true');

  const images = stage.locator('img');
  assert.equal(await images.count(), 3, `${viewportName} Capture stage does not use all three accepted meeting captures`);
  for (let index = 0; index < 3; index += 1) {
    await tabs.nth(index).click();
    const image = images.nth(index);
    await image.scrollIntoViewIfNeeded();
    await image.evaluate(async (element) => {
      if (!element.complete || element.naturalWidth === 0) await element.decode();
    });
  }
  const sources = await images.evaluateAll((items) => items.map((image) => ({
    src: image.getAttribute('src'),
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
    alt: image.getAttribute('alt') || '',
  })));
  assert.deepEqual(
    sources.map((item) => item.src),
    [
      '/product/meetings-before.webp',
      '/product/meetings-during.webp',
      '/product/meetings-after.webp',
    ],
  );
  for (const source of sources) {
    assert.equal(source.naturalWidth, 1152, `${source.src} has an unexpected width`);
    assert.equal(source.naturalHeight, 800, `${source.src} has an unexpected height`);
    assert.match(source.alt, /Genuine Mumble Meetings capture/i);
  }
}

async function captureJourney(browser) {
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
    await assertCaptureStage(page, item.name);
    await page.locator('[data-job-story="write"][data-story-surface="home"]').waitFor();
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    await noHorizontalOverflow(page, `${item.name} Capture Home`);

    await followPrimaryNavigation(page, 'Product', '/product/', item.mobile);
    await assertSharedShell(page, 'Product');
    const product = page.locator('[data-job-story="capture"][data-story-surface="product"]');
    await product.waitFor();
    const productText = await product.innerText();
    for (const requirement of [
      /selected microphone/i,
      /pause, resume, and Stop & save/i,
      /WAV, MP3, FLAC, or OGG/i,
      /private local recording/i,
      /search titles and full transcripts/i,
      /play the original recording/i,
      /TXT, Markdown, JSON, HTML, or the clipboard/i,
      /best-effort speaker/i,
      /original recording is never sent for analysis/i,
    ]) {
      assert.match(productText, requirement);
    }
    assert.match(productText, /Local transcription is the default/i);
    assert.match(productText, /Cloud transcription sends meeting audio/i);
    const futureGate = page.getByRole('note', { name: 'Future media boundary' });
    await futureGate.waitFor();
    const futureText = await futureGate.innerText();
    assert.match(futureText, /Computer-audio capture/i);
    assert.match(futureText, /Direct video import/i);
    assert.match(futureText, /not available in the current product/i);
    assert.equal(await futureGate.getByRole('link').count(), 0, 'future media gate exposes an active link');
    assert.equal(await futureGate.getByRole('button').count(), 0, 'future media gate exposes an active button');
    assert.equal(await page.getByRole('link', { name: /computer-audio|direct video/i }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /computer-audio|direct video/i }).count(), 0);
    await page.locator('[data-job-story="write"][data-story-surface="product"]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Capture Product`);

    await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', item.mobile);
    await assertSharedShell(page, 'Use Cases');
    const captureTasks = page.locator('[data-job-story="capture"][data-story-surface="use-cases"]');
    for (const task of ['Meeting', 'Lecture', 'Existing recording']) {
      const taskRegion = captureTasks.getByRole('article').filter({
        has: page.getByRole('heading', { level: 2, name: task, exact: true }),
      });
      await taskRegion.waitFor();
      assert.ok(await taskRegion.getByRole('listitem').count() >= 4, `${task} is not a complete Capture task sequence`);
    }
    await page.locator('[data-job-story="write"][data-story-surface="use-cases"]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Capture Use Cases`);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    assertNoBrowserErrors(browserErrors, `${item.name} Capture journey browser errors`);
    await context.close();
  }
  record('Issue #38 desktop/mobile Capture journey, accepted meeting evidence, keyboard controls, reduced-motion stability, truthful recording/import/privacy/review/export boundaries, inactive future media gate, complete task sequences, zero microphone requests, and horizontal fit');
}


function assertShapeBoundary(text, label) {
  assert.match(
    text,
    /finished text(?:—|-|,|\s)+not (?:the )?(?:captured|recorded) audio/i,
    `${label} does not distinguish finished-text shaping from recorded-audio transfer`,
  );
  assert.match(
    text,
    /no (?:Mumble )?account(?:,| or| and).*provider key(?:,| or| and).*paid service/i,
    `${label} does not preserve the useful local dictation path`,
  );
  assert.match(
    text,
    /external provider(?:s)? may charge/i,
    `${label} omits the possible external-provider cost boundary`,
  );
}

async function shapeBoundaryText(region) {
  return region.textContent();
}

async function assertShapeBoundaryInRegion(region, label) {
  assertShapeBoundary(await shapeBoundaryText(region), label);
}

async function proveShapeBoundaryScope(page, region, label) {
  await region.evaluate((element) => {
    const replacements = [
      [/finished text/gi, 'selected material'],
      [/recorded audio/gi, 'source media'],
      [/no Mumble account, provider key, or paid service/gi, 'the local path remains available'],
      [/external providers may charge/gi, 'provider terms apply'],
    ];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      for (const [pattern, replacement] of replacements) {
        node.textContent = node.textContent.replace(pattern, replacement);
      }
      node = walker.nextNode();
    }
  });

  assertShapeBoundary(
    await page.locator('main').innerText(),
    `${label} whole-page masking control`,
  );
  await assert.rejects(
    () => assertShapeBoundaryInRegion(region, `${label} scoped adversarial probe`),
    /does not distinguish|does not preserve|omits the possible external-provider cost boundary/,
  );
}

async function shapeJourney(browser) {
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
    const shapeStage = page.getByRole('region', { name: /finished thought become a structured prompt/i });
    assert.equal(await shapeStage.count(), 1, `${item.name} Home is missing the Shape demonstration`);
    assert.match(await shapeStage.innerText(), /Illustrative Shape demonstration—not a live provider result/i);
    await assertShapeBoundaryInRegion(shapeStage, `${item.name} Home Shape region`);
    await proveShapeBoundaryScope(page, shapeStage, `${item.name} Home Shape region`);
    await page.reload({ waitUntil: 'networkidle' });

    const tabs = shapeStage.getByRole('tab');
    assert.equal(await tabs.count(), 3, `${item.name} Shape demonstration does not expose three direct steps`);
    const sourceTab = shapeStage.getByRole('tab', { name: /Finished text/i });
    await sourceTab.focus();
    await assertVisibleFocus(page, `${item.name} Shape direct-step focus`);
    await page.keyboard.press('ArrowRight');
    const routeTab = shapeStage.getByRole('tab', { name: /Choose Prompt/i });
    assert.equal(await routeTab.getAttribute('aria-selected'), 'true');
    await page.keyboard.press('ArrowRight');
    const resultTab = shapeStage.getByRole('tab', { name: /Shaped prompt/i });
    assert.equal(await resultTab.getAttribute('aria-selected'), 'true');
    assert.equal(await resultTab.evaluate((element) => document.activeElement === element), true);
    const resultPanel = shapeStage.getByRole('tabpanel', { name: /Shaped prompt/i });
    assert.match(await resultPanel.innerText(), /Illustrative result/i);
    await page.waitForTimeout(550);
    assert.equal(
      await resultTab.getAttribute('aria-selected'),
      'true',
      `${item.name} reduced-motion Shape stage advanced without visitor input`,
    );
    await shapeStage.getByRole('button', { name: 'Previous Shape step' }).click();
    assert.equal(await routeTab.getAttribute('aria-selected'), 'true');
    await noHorizontalOverflow(page, `${item.name} Shape Home`);

    await followPrimaryNavigation(page, 'Product', '/product/', item.mobile);
    const productShape = page.getByRole('region', { name: /Shape routes finished text/i });
    assert.equal(await productShape.count(), 1, `${item.name} Product is missing the Shape mechanism`);
    const productText = await page.locator('main').innerText();
    for (const mode of ['Text', 'Prompt', 'Email', 'Reply', 'Foreign']) {
      assert.match(productText, new RegExp(`\\b${mode}\\b`), `${item.name} Product omits ${mode}`);
    }
    assert.match(productText, /built-in presets/i);
    assert.match(productText, /custom shaping/i);
    assert.match(productText, /Cerebras and OpenRouter/i);
    assert.match(productText, /compatible local model/i);
    assert.match(productText, /no local model is bundled or adopted/i);
    await assertShapeBoundaryInRegion(productShape, `${item.name} Product Shape region`);
    assert.doesNotMatch(productText, /\b[0-9]+(?:\.[0-9]+)?[×x]\s*(?:faster|speed)/i);
    await noHorizontalOverflow(page, `${item.name} Shape Product`);

    await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', item.mobile);
    for (const task of ['AI prompt', 'Business email', 'Contextual reply', 'Language-assisted text']) {
      const taskRegion = page.getByRole('article').filter({
        has: page.getByRole('heading', { level: 2, name: task }),
      });
      assert.equal(await taskRegion.count(), 1, `${item.name} Use Cases omits ${task}`);
      assert.ok(await taskRegion.getByRole('listitem').count() >= 4, `${task} is not a complete shaping sequence`);
    }
    const useCasesText = await page.locator('main').innerText();
    const useCasesShape = page.locator('[data-job-story="shape"][data-story-surface="use-cases"]');
    assert.match(useCasesText, /tasks rather than professions/i);
    await assertShapeBoundaryInRegion(useCasesShape, `${item.name} Use Cases Shape region`);
    await noHorizontalOverflow(page, `${item.name} Shape Use Cases`);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    assertNoBrowserErrors(browserErrors, `${item.name} Shape journey browser errors`);
    await context.close();
  }

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, javaScriptEnabled: false });
    const page = await context.newPage();
    await page.goto(`${origin}/`, { waitUntil: 'load' });
    const shapeStage = page.getByRole('region', { name: /finished thought become a structured prompt/i });
    assert.equal(await shapeStage.getByRole('tabpanel').count(), 3, `no-JavaScript ${viewport.width}px Shape states are incomplete`);
    for (const panel of ['Finished text', 'Choose Prompt', 'Shaped prompt']) {
      await shapeStage.getByRole('tabpanel', { name: new RegExp(panel, 'i') }).waitFor();
    }
    await assertShapeBoundaryInRegion(shapeStage, `no-JavaScript ${viewport.width}px Home Shape region`);
    await noHorizontalOverflow(page, `no-JavaScript ${viewport.width}px Shape Home`);

    await followPrimaryNavigation(page, 'Product', '/product/', false);
    const productShape = page.getByRole('region', { name: /Shape routes finished text/i });
    assert.match(await page.locator('main').innerText(), /Cerebras and OpenRouter/i);
    await assertShapeBoundaryInRegion(productShape, `no-JavaScript ${viewport.width}px Product Shape region`);
    await noHorizontalOverflow(page, `no-JavaScript ${viewport.width}px Shape Product`);

    await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', false);
    for (const task of ['AI prompt', 'Business email', 'Contextual reply', 'Language-assisted text']) {
      await page.getByRole('heading', { level: 2, name: task }).waitFor();
    }
    const useCasesShape = page.locator('[data-job-story="shape"][data-story-surface="use-cases"]');
    await assertShapeBoundaryInRegion(useCasesShape, `no-JavaScript ${viewport.width}px Use Cases Shape region`);
    await noHorizontalOverflow(page, `no-JavaScript ${viewport.width}px Shape Use Cases`);
    await context.close();
  }

  record('Issue #39 desktop/mobile Shape journey, before/after controls, optional-AI boundary, complete task sequences, reduced-motion stability, no-JavaScript completeness, no microphone request, and horizontal fit');
}


async function assertListenStage(page, viewportName) {
  const stage = page.locator('[data-job-story="listen"][data-story-surface="home"]');
  await stage.getByRole('heading', { level: 2, name: /complete Listen path/i }).waitFor();
  const stageText = await stage.innerText();
  assert.match(stageText, /Genuine Mumble Reader/i);
  assert.match(stageText, /demonstration library content/i);
  assert.match(stageText, /configured online text-to-speech route/i);

  const tabs = stage.getByRole('tab');
  assert.equal(await tabs.count(), 3, `${viewportName} Listen stage does not expose three direct steps`);
  const listenTab = stage.getByRole('tab', { name: 'Listen', exact: true });
  await listenTab.focus();
  await assertVisibleFocus(page, `${viewportName} Listen direct-step focus`);
  await page.keyboard.press('Enter');
  assert.equal(await listenTab.getAttribute('aria-selected'), 'true');
  const listenPanel = stage.getByRole('tabpanel', { name: 'Listen', exact: true });
  await listenPanel.waitFor();
  assert.match(await listenPanel.innerText(), /Play or pause/i);
  assert.match(await listenPanel.innerText(), /voice and speed/i);

  await page.keyboard.press('ArrowRight');
  const returnTab = stage.getByRole('tab', { name: 'Return', exact: true });
  assert.equal(await returnTab.getAttribute('aria-selected'), 'true');
  assert.equal(await returnTab.evaluate((element) => document.activeElement === element), true);
  const returnPanel = stage.getByRole('tabpanel', { name: 'Return', exact: true });
  await returnPanel.waitFor();
  assert.match(await returnPanel.innerText(), /saved reading position/i);
  assert.match(await returnPanel.innerText(), /find and bookmarks/i);
  await page.waitForTimeout(550);
  assert.equal(
    await returnTab.getAttribute('aria-selected'),
    'true',
    `${viewportName} reduced-motion Listen stage advanced without visitor input`,
  );
  await stage.getByRole('button', { name: 'Previous Listen step' }).click();
  assert.equal(await listenTab.getAttribute('aria-selected'), 'true');

  const image = stage.locator('img');
  assert.equal(await image.count(), 1, `${viewportName} Listen stage does not use one genuine Reader capture`);
  await image.scrollIntoViewIfNeeded();
  await image.evaluate(async (element) => {
    if (!element.complete || element.naturalWidth === 0) await element.decode();
  });
  const imageFacts = await image.evaluate((element) => ({
    src: element.getAttribute('src'),
    alt: element.getAttribute('alt') || '',
    naturalWidth: element.naturalWidth,
    naturalHeight: element.naturalHeight,
  }));
  assert.equal(imageFacts.src, '/product/reader.webp');
  assert.equal(imageFacts.naturalWidth, 1180);
  assert.equal(imageFacts.naturalHeight, 820);
  assert.match(imageFacts.alt, /Genuine Mumble Reader capture/i);
  assert.match(imageFacts.alt, /demonstration library content/i);
}

async function listenJourney(browser) {
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
    await assertListenStage(page, item.name);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    await noHorizontalOverflow(page, `${item.name} Listen Home`);

    await followPrimaryNavigation(page, 'Product', '/product/', item.mobile);
    await assertSharedShell(page, 'Product');
    const product = page.locator('[data-job-story="listen"][data-story-surface="product"]');
    await product.waitFor();
    const productText = await product.innerText();
    for (const format of ['TXT', 'Markdown', 'PDF', 'DOCX', 'HTML', 'EPUB', 'RTF', 'CSV', 'XLSX', 'PPTX', 'ODT']) {
      assert.match(productText, new RegExp(`\\b${format}\\b`, 'i'), `Product omits supported Reader format ${format}`);
    }
    for (const truth of [
      /library/i,
      /collections/i,
      /play or pause/i,
      /voice and speed/i,
      /find/i,
      /bookmark/i,
      /saved reading position/i,
      /progress/i,
    ]) assert.match(productText, truth);

    const onlineBoundary = product.getByRole('note', { name: 'Reader speech online requirements' });
    await onlineBoundary.waitFor();
    const onlineText = await onlineBoundary.innerText();
    assert.match(onlineText, /configured online text-to-speech route/i);
    assert.match(onlineText, /network access/i);
    assert.match(onlineText, /provider key/i);
    assert.match(onlineText, /provider availability/i);
    assert.match(onlineText, /provider credits/i);
    assert.doesNotMatch(onlineText, /speech (?:is|runs|stays) (?:local|offline)/i);

    const summaryBoundary = product.getByRole('note', { name: 'Reader summary boundary' });
    await summaryBoundary.waitFor();
    const summaryText = await summaryBoundary.innerText();
    assert.match(summaryText, /separate from playback/i);
    assert.match(summaryText, /document text/i);
    assert.match(summaryText, /only when you choose/i);
    await page.locator('.journey-next [data-platform-action]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Listen Product`);

    await followPrimaryNavigation(page, 'Use Cases', '/use-cases/', item.mobile);
    await assertSharedShell(page, 'Use Cases');
    for (const task of ['Long document', 'Resume reading', 'Find a passage', 'Retain progress']) {
      const taskRegion = page.getByRole('article').filter({
        has: page.getByRole('heading', { level: 2, name: task, exact: true }),
      });
      await taskRegion.waitFor();
      assert.ok(await taskRegion.getByRole('listitem').count() >= 4, `${task} is not a complete Listen sequence`);
    }
    const useCasesText = await page.locator('[data-job-story="listen"][data-story-surface="use-cases"]').innerText();
    assert.match(useCasesText, /configured online text-to-speech route/i);
    assert.match(useCasesText, /provider credits/i);
    assert.match(useCasesText, /tasks rather than professions/i);
    assert.equal(await page.evaluate(() => window.__mumbleMicrophoneRequests), 0);
    await page.locator('.journey-next [data-platform-action]').waitFor();
    await noHorizontalOverflow(page, `${item.name} Listen Use Cases`);
    assertNoBrowserErrors(browserErrors, `${item.name} Listen journey browser errors`);
    await context.close();
  }
  record('Issue #40 desktop/mobile Listen journey, genuine Reader evidence, keyboard controls, reduced-motion stability, accepted formats, library/playback/navigation/progress truth, distinct summary route, visible online requirements, zero microphone requests, release action, and horizontal fit');
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
  await page.getByRole('heading', { level: 1, name: /solve the task/i }).waitFor();
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
    for (const blockHeading of ['Do this', 'Privacy boundary', 'If it does not work']) {
      await article.getByRole('heading', { level: 4, name: blockHeading, exact: true }).waitFor();
    }
  }

  const readerGuideText = await page.locator('#listen-guide').innerText();
  assert.match(
    readerGuideText,
    /one attempt using the frozen selected provider and model/i,
    'Reader Help does not state the one-attempt frozen provider/model authority',
  );
  assert.match(
    readerGuideText,
    /failure stops.+verify the selected model.+key.+credits.+retry/is,
    'Reader Help does not give honest one-attempt recovery guidance',
  );
  assert.doesNotMatch(
    readerGuideText,
    /may try (?:a )?compatible model|tries? (?:a )?sibling model/i,
    'Reader Help promises an unauthorised compatible or sibling model attempt',
  );

  const privacyRoutes = page.locator('#privacy-routes');
  for (const route of [
    'Local transcription',
    'Cloud transcription',
    'Text shaping',
    'Reader speech',
    'Mumble Find',
    'Web Search',
  ]) {
    await privacyRoutes.locator('dt').filter({ hasText: route }).waitFor();
  }
  assert.equal(
    await privacyRoutes.getByRole('link', { name: 'Privacy boundaries', exact: true }).getAttribute('href'),
    '/privacy/',
    'Help privacy ledger does not reach the canonical Privacy page',
  );
  const readerRouteText = await privacyRoutes.locator('dt')
    .filter({ hasText: 'Reader speech' }).locator('..').innerText();
  assert.match(
    readerRouteText,
    /one attempt.+frozen selected provider and model.+no sibling model/is,
    'Reader privacy Help does not preserve the exact one-attempt route boundary',
  );

  const accessTable = page.getByRole('region', { name: 'Accessibility guidance table' });
  await accessTable.getByRole('table', { name: /Keyboard, visual state, motion/i }).waitFor();
  await accessTable.focus();
  await assertVisibleFocus(page, 'Help accessibility table focus');
  assert.match(
    await accessTable.innerText(),
    /Physical screen-reader output.+have not been independently accepted/is,
    'Help accessibility guidance omits its physical evidence limit',
  );

  const troubleshootingTopics = [
    'Package or installer',
    'Capture or import',
    'Transcription route',
    'Provider keys and costs',
    'Text shaping',
    'Reader',
    'Deck recovery',
    'Mumble Find',
    'Web Search',
    'Updates',
    'Uninstall and recovery',
  ];
  for (const topic of troubleshootingTopics) {
    await page.locator('#troubleshooting summary').filter({ hasText: topic }).waitFor();
  }
  const readerTroubleshooting = page.locator('#reader-troubleshooting');
  await readerTroubleshooting.locator('summary').click();
  const readerTroubleshootingText = await readerTroubleshooting.innerText();
  assert.match(
    readerTroubleshootingText,
    /one attempt using the frozen selected provider and model.+failure stops.+verify the selected model.+key.+credits.+retry/is,
    'Reader troubleshooting does not match the accepted one-attempt runtime behaviour',
  );
  assert.doesNotMatch(
    readerTroubleshootingText,
    /may try (?:a )?compatible model|tries? (?:a )?sibling model|fallback cannot cross/is,
    'Reader troubleshooting still implies an unauthorised fallback attempt',
  );

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
  assert.match(helpText, /b096b762be1075622bc443bd06564e568d709be129fc0fb4a0c87da382da4534/i);
  assert.doesNotMatch(helpText, /Mumble Search/i);
  assert.match(helpText, /public GitHub issue tracker.+sole support and defect-reporting route/is);
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
    ['Previous accepted versions', '/downloads/#history-title'],
    ['Known limitations', '#known-limitations'],
    ['Platform status', '/downloads/#platforms-title'],
    ['MIT licence', publicLicenceUrl],
    ['Inspect the source', publicRepositoryUrl],
    ['The public GitHub issue tracker', publicIssuesUrl],
  ]);
  for (const [name, href] of expectedDestinations) {
    assert.equal(
      await page.getByRole('link', { name, exact: true }).first().getAttribute('href'),
      href,
      `${name} has the wrong Help destination`,
    );
  }
  assert.equal(await page.locator('a[href^="mailto:"]').count(), 0, 'Help exposes an email support route');
  assert.doesNotMatch(helpText, /contact us|donation|sales enquiry/i, 'Help exposes an unwanted support channel');
  const missingFragment = await page.locator('a[href^="#"]').evaluateAll((links) => {
    for (const link of links) {
      const target = link.getAttribute('href')?.slice(1);
      if (target && !document.getElementById(target)) return target;
    }
    return null;
  });
  assert.equal(missingFragment, null, `Help links to missing fragment #${missingFragment}`);

  const search = page.getByRole('searchbox', { name: 'Search Mumble Help' });
  await search.focus();
  await assertVisibleFocus(page, 'Help search focus');
  const articles = page.getByRole('article');
  const articleCount = await articles.count();
  assert.equal(articleCount, helpArticleHeadings.length, 'Help article contract drifted');
  await search.fill('Reader speech one attempt');
  await page.getByRole('status').filter({ hasText: /help topics? shown/i }).waitFor();
  const visibleCount = await articles.count();
  assert.ok(visibleCount > 0 && visibleCount < articleCount, 'Help search did not filter the static article set');
  const visibleContexts = await page.locator('[data-help-result-context]:visible').allTextContents();
  assert.ok(visibleContexts.length > 0, 'Help search results omit useful topic context');
  assert.ok(
    visibleContexts.some((contextLabel) => /Everyday jobs|Privacy and access|Troubleshooting/i.test(contextLabel)),
    'Help search results expose no meaningful category context',
  );
  assert.match(await page.getByRole('status').innerText(), /Everyday jobs|Privacy and access|Troubleshooting/i);
  await page.keyboard.press('Escape');
  assert.equal(await search.inputValue(), '', 'Escape did not clear Help search');
  assert.equal(await articles.count(), articleCount, 'clearing Help search did not restore all topics');
  await assertReducedMotion(page, 'desktop Help reduced-motion mode');

  await noHorizontalOverflow(page, 'desktop Help');
  assertNoBrowserErrors(browserErrors, 'desktop Help browser errors');
  await context.close();
  record('complete Help search, five-job procedures, six privacy routes, accessibility evidence limits, troubleshooting, canonical destinations, fragment integrity, public issue reporting, reduced motion, and keyboard focus');
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
  await search.fill('Reader speech one attempt');
  await page.getByRole('status').filter({ hasText: /help topics? shown/i }).waitFor();
  const visibleCount = await articles.count();
  assert.ok(visibleCount > 0 && visibleCount < articleCount, 'mobile Help search did not filter the article set');
  assert.ok(
    await page.locator('[data-help-result-context]:visible').count() > 0,
    'mobile Help search results omit useful topic context',
  );

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

  const captureSummary = page.locator('#troubleshooting summary').filter({ hasText: 'Capture or import' });
  await page.keyboard.press('Tab');
  await captureSummary.focus();
  await assertVisibleFocus(page, 'mobile Help troubleshooting disclosure focus');
  await page.keyboard.press('Enter');
  assert.equal(
    await captureSummary.evaluate((summary) => summary.parentElement?.open),
    true,
    'mobile Help troubleshooting disclosure did not open from the keyboard',
  );

  await assertReducedMotion(page, 'mobile Help reduced-motion mode');
  await noHorizontalOverflow(page, 'mobile Help');
  assertNoBrowserErrors(browserErrors, 'mobile Help browser errors');
  await context.close();
  record('mobile Help contextual search, clear control, taxonomy and platform paths, disclosure keyboard use, reduced motion, and horizontal fit');
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
    const captureStage = page.locator('[data-job-story="capture"][data-story-surface="home"]');
    assert.equal(await captureStage.getByRole('tabpanel').count(), 3, 'no-JavaScript Capture states are incomplete');
    for (const panelId of ['source', 'record', 'review']) {
      await captureStage.locator(`#job-capture-panel-${panelId}`).waitFor();
    }
    const listenStage = page.locator('[data-job-story="listen"][data-story-surface="home"]');
    assert.equal(await listenStage.getByRole('tabpanel').count(), 3, 'no-JavaScript Listen states are incomplete');
    const noScriptListenStageText = await listenStage.innerText();
    for (const state of ['Start from the document', 'Play or pause', 'Resume from the saved reading position']) {
      assert.match(noScriptListenStageText, new RegExp(state, 'i'));
    }
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
    const captureProduct = page.locator('[data-job-story="capture"][data-story-surface="product"]');
    await captureProduct.waitFor();
    assert.match(await captureProduct.innerText(), /WAV, MP3, FLAC, or OGG/i);
    const futureGate = page.getByRole('note', { name: 'Future media boundary' });
    await futureGate.waitFor();
    assert.equal(await futureGate.getByRole('link').count(), 0);
    assert.equal(await futureGate.getByRole('button').count(), 0);
    assert.match(productText, /supported documents into a library-first listening workspace/i);
    assert.match(productText, /network access/i);
    assert.match(productText, /provider credits/i);
    assert.match(productText, /Summary stays separate from playback/i);
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
    for (const task of [
      'Everyday notes',
      'Longer text',
      'Across applications',
      'Long document',
      'Resume reading',
      'Find a passage',
      'Retain progress',
    ]) {
      await page.getByRole('heading', { level: 2, name: task }).waitFor();
    }
    const noScriptUseCasesFind = page.locator('[data-job-story="find"][data-story-surface="use-cases"]');
    assert.equal(await noScriptUseCasesFind.locator('article').count(), 4, 'no-JavaScript Find tasks are incomplete');
    assert.match(await noScriptUseCasesFind.innerText(), /Deterministic demonstration selected text · not user data/i);
    for (const task of ['Meeting', 'Lecture', 'Existing recording']) {
      await page.getByRole('heading', { level: 2, name: task, exact: true }).waitFor();
    }
    const noScriptListenTasks = page.locator('[data-job-story="listen"][data-story-surface="use-cases"]');
    const noScriptListenText = await noScriptListenTasks.innerText();
    assert.match(noScriptListenText, /configured online text-to-speech route/i);
    assert.match(noScriptListenText, /provider credits/i);
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
      `no-JavaScript ${viewport.width}px Use Cases Downloads link did not reach Downloads`,
    );
    await assertSharedShell(page, 'Downloads');
    await page.getByText('0.95', { exact: true }).first().waitFor();
    await page.getByRole('link', { name: /Download candidate for Windows/i }).waitFor();
    await page.getByRole('heading', { level: 2, name: 'macOS' }).waitFor();
    await page.getByRole('heading', { level: 2, name: 'Linux' }).waitFor();
    const pageText = await page.locator('body').innerText();
    assert.match(pageText, /b096b762be1075622bc443bd06564e568d709be129fc0fb4a0c87da382da4534/i);
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
  record('no-JavaScript desktop/mobile Home-to-Product-to-Use-Cases-to-Downloads-to-Help journeys retain complete Write, Capture, Shape, Listen, and Find states, distinct Deck/local/web boundaries, truthful destination/media/privacy guidance, complete semantic Help reachability, non-sticky mobile navigation, visible activated article/platform targets, ≥44px mobile header action geometry, mechanisms, task sequences, inactive future media gate, online Reader requirements, release facts, platform states, download access, and horizontal fit');
}

let browser;
try {
  console.log(`Browser executable route: ${executablePath ?? 'Playwright-managed Chromium'}`);
  browser = await chromium.launch({ headless: true, executablePath });
  console.log(`Browser version: ${browser.version()}`);
  if (requestedGroup === 'media') {
    await assertDeferredMediaContract(browser);
    console.log('MEDIA_DEFERRAL_CONTRACT_OK');
  } else if (requestedGroup === 'privacy') {
    await privacyRoutes(browser);
  } else if (requestedGroup) {
    throw new Error(`Unknown browser contract group: ${requestedGroup}`);
  } else {
    await assertPublicProjectDestinations(browser);
    await assertAcceptedPageGeometry(browser);
    await assertDeferredMediaContract(browser);
    await homeMontageJourney(browser);
    await desktopJourney(browser);
    await correctionConstraints(browser);
    await captureJourney(browser);
    await writeJourney(browser);
    await shapeJourney(browser);
    await listenJourney(browser);
    await findJourney(browser);
    await helpJourney(browser);
    await mobileHelpJourney(browser);
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
