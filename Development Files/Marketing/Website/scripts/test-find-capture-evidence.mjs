import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const websiteRoot = resolve(import.meta.dirname, '..');
const repoRoot = resolve(websiteRoot, '..', '..', '..');
const jobs = JSON.parse(await readFile(resolve(websiteRoot, 'src/data/jobs.json'), 'utf8'));
const find = jobs.find((job) => job.id === 'find');
const captures = find?.story?.presentation?.captures;

assert.ok(Array.isArray(captures), 'Find capture evidence must be declared in canonical jobs.json');
assert.deepEqual(
  captures.map((capture) => capture.route),
  ['deck', 'local', 'web'],
  'Deck, local Mumble Find, and Web Search captures must all lead the Find composition',
);

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const sourceSha256 = (bytes, path) => {
  const normalized = /\.(?:css|html|js|json)$/i.test(path)
    ? Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'))
    : bytes;
  return sha256(normalized);
};
const pngDimensions = (bytes) => {
  assert.equal(bytes.subarray(1, 4).toString('ascii'), 'PNG', 'capture must be a PNG');
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
};

for (const route of ['local', 'web']) {
  const capture = captures.find((entry) => entry.route === route);
  assert.ok(capture, `${route} accepted-product-source capture is missing`);
  assert.ok(capture.src?.startsWith('/product/') && capture.src.endsWith('.png'), `${route} capture path is invalid`);
  assert.ok(Number.isInteger(capture.width) && capture.width > 0, `${route} width is missing`);
  assert.ok(Number.isInteger(capture.height) && capture.height > 0, `${route} height is missing`);
  assert.match(capture.alt ?? '', route === 'local' ? /Mumble Find.*demonstration/i : /Web Search.*demonstration/i);
  assert.match(capture.label ?? '', /Genuine Mumble .*capture/i);
  assert.match(capture.truthLabel ?? '', /deterministic demonstration data.*not user data/i);

  const outputPath = resolve(websiteRoot, `public${capture.src}`);
  const outputBytes = await readFile(outputPath);
  assert.deepEqual(pngDimensions(outputBytes), { width: capture.width, height: capture.height });
  assert.equal(sha256(outputBytes), capture.provenance?.outputSha256, `${route} output hash drifted`);
  assert.match(capture.provenance?.sourceCommit ?? '', /^[0-9a-f]{40}$/);
  assert.equal(capture.provenance?.generatedBy, 'scripts/capture-find-evidence.mjs');
  assert.ok(Array.isArray(capture.provenance?.sources) && capture.provenance.sources.length >= 2);

  for (const source of capture.provenance.sources) {
    assert.match(source.path ?? '', /^(Internal|Development Files)\//);
    assert.match(source.sha256 ?? '', /^[0-9a-f]{64}$/);
    const committedBytes = execFileSync(
      'git',
      ['show', `${capture.provenance.sourceCommit}:${source.path}`],
      { cwd: repoRoot, encoding: 'buffer', maxBuffer: 20 * 1024 * 1024 },
    );
    assert.equal(sourceSha256(committedBytes, source.path), source.sha256, `${route} source provenance drifted: ${source.path}`);
    assert.equal(
      sourceSha256(await readFile(resolve(repoRoot, source.path)), source.path),
      source.sha256,
      `${route} capture no longer matches current accepted source: ${source.path}`,
    );
  }
}

console.log('FIND_CAPTURE_EVIDENCE_OK');
