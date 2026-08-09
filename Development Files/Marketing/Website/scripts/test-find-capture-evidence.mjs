import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import sharp from 'sharp';
import { browserLaunchOptions } from './browser-launch-options.mjs';

const websiteRoot = resolve(import.meta.dirname, '..');
const repoRoot = resolve(websiteRoot, '..', '..', '..');
const jobs = JSON.parse(await readFile(resolve(websiteRoot, 'src/data/jobs.json'), 'utf8'));
const captures = jobs.find((job) => job.id === 'find')?.story?.presentation?.captures;

assert.deepEqual(captures?.map((capture) => capture.route), ['deck', 'local', 'web']);
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const sourceSha256 = (bytes, path) => sha256(/\.(?:css|html|js|json)$/i.test(path)
  ? Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'))
  : bytes);

assert.deepEqual(browserLaunchOptions(''), { channel: 'chrome' });
assert.deepEqual(
  browserLaunchOptions('  C:\\Browsers\\chrome.exe  '),
  { executablePath: 'C:\\Browsers\\chrome.exe' },
);

const acceptedBytes = new Map();
for (const capture of captures) {
  const outputPath = resolve(websiteRoot, `public${capture.src}`);
  const outputBytes = await readFile(outputPath);
  acceptedBytes.set(capture.route, outputBytes);
  const metadata = await sharp(outputBytes).metadata();
  assert.deepEqual(
    { width: metadata.width, height: metadata.height },
    { width: capture.width, height: capture.height },
    `${capture.route} public dimensions drifted`,
  );
  assert.equal(sha256(outputBytes), capture.provenance.outputSha256, `${capture.route} output hash drifted`);
  assert.match(capture.provenance.sourceCommit ?? '', /^[0-9a-f]{40}$/);
  assert.equal(capture.provenance.generatedBy, 'scripts/capture-find-evidence.mjs');
  assert.equal(typeof capture.provenance.replayBoundary?.byteIdenticalReplayRequired, 'boolean');

  const sourceRoles = new Set();
  for (const source of capture.provenance.sources) {
    assert.match(source.role ?? '', /^[a-z][a-z-]*$/);
    assert.equal(sourceRoles.has(source.role), false, `${capture.route} repeats source role ${source.role}`);
    sourceRoles.add(source.role);
    const committedBytes = execFileSync(
      'git',
      ['show', `${capture.provenance.sourceCommit}:${source.path}`],
      { cwd: repoRoot, encoding: 'buffer', maxBuffer: 20 * 1024 * 1024 },
    );
    assert.equal(sourceSha256(committedBytes, source.path), source.sha256, `${capture.route} committed source drifted: ${source.path}`);
    assert.equal(sourceSha256(await readFile(resolve(repoRoot, source.path)), source.path), source.sha256, `${capture.route} current source drifted: ${source.path}`);
  }
}

const deck = captures.find((capture) => capture.route === 'deck');
const conversion = deck.provenance.conversion;
assert.deepEqual(conversion, {
  tool: 'sharp',
  version: '0.35.3',
  libvipsVersion: '8.18.3',
  input: {
    path: 'Development Files/Research/focus-stage-production-screenshots/05-deck-command-surface.png',
    format: 'png',
    width: 1440,
    height: 1000,
  },
  output: {
    path: 'Development Files/Marketing/Website/public/product/deck-command-surface.webp',
    format: 'webp',
    width: 1152,
    height: 800,
  },
  resize: { fit: 'fill', kernel: 'lanczos3', withoutEnlargement: true },
  webp: {
    quality: 80,
    alphaQuality: 100,
    lossless: false,
    nearLossless: false,
    smartSubsample: false,
    preset: 'default',
    effort: 6,
  },
});
assert.equal(sharp.versions.sharp, conversion.version);
assert.equal(sharp.versions.vips, conversion.libvipsVersion);
assert.equal(deck.provenance.replayBoundary.byteIdenticalReplayRequired, true);
assert.deepEqual(
  await sharp(resolve(repoRoot, conversion.input.path)).metadata().then(({ format, width, height }) => ({ format, width, height })),
  { format: conversion.input.format, width: conversion.input.width, height: conversion.input.height },
);
assert.deepEqual(
  { width: conversion.output.width, height: conversion.output.height },
  { width: deck.width, height: deck.height },
);
assert.equal(conversion.output.path, `Development Files/Marketing/Website/public${deck.src}`);

const replayRoot = await mkdtemp(join(tmpdir(), 'mumble-find-replay-'));
try {
  const report = JSON.parse(execFileSync(
    process.execPath,
    [resolve(websiteRoot, 'scripts/capture-find-evidence.mjs'), '--output', replayRoot],
    { cwd: websiteRoot, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 },
  ));
  assert.deepEqual(report.captures.map((capture) => capture.route), ['deck', 'local', 'web']);
  assert.match(report.environment.browserVersion, /^\d+\.\d+\.\d+\.\d+$/);
  assert.equal(report.environment.playwrightVersion, '1.55.1');
  assert.equal(report.environment.nodeVersion, process.version);
  assert.equal(report.environment.platform, process.platform);
  for (const capture of captures) {
    const committed = acceptedBytes.get(capture.route);
    const replayed = await readFile(join(replayRoot, basename(capture.src)));
    const replayMetadata = await sharp(replayed).metadata();
    assert.deepEqual(
      { width: replayMetadata.width, height: replayMetadata.height },
      { width: capture.width, height: capture.height },
      `${capture.route} replay dimensions drifted`,
    );
    const result = report.captures.find((entry) => entry.route === capture.route);
    assert.equal(result.sha256, sha256(replayed));
    assert.equal(result.acceptedSha256, capture.provenance.outputSha256);
    assert.equal(result.byteIdenticalReplayRequired, capture.provenance.replayBoundary.byteIdenticalReplayRequired);
    if (capture.route === 'deck') {
      assert.equal(sha256(replayed), sha256(committed), 'Deck clean replay is not byte-identical');
      assert.equal(result.matchesAcceptedOutput, true);
    } else {
      assert.equal(result.byteIdenticalReplayRequired, false, `${capture.route} must not claim browser byte determinism`);
      assert.equal(capture.provenance.replayBoundary.kind, 'environment-bound-browser-render');
      assert.deepEqual(
        capture.provenance.captureEnvironment.viewport,
        { width: capture.width, height: capture.height },
      );
    }
    assert.equal(
      sha256(await readFile(resolve(websiteRoot, `public${capture.src}`))),
      sha256(committed),
      `${capture.route} replay changed the accepted public evidence`,
    );
  }
} finally {
  await rm(replayRoot, { recursive: true, force: true });
}

console.log('FIND_CAPTURE_EVIDENCE_OK');
