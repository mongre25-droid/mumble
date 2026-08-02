import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const websiteRoot = resolve(import.meta.dirname, '..');
const helpHtml = await readFile(resolve(websiteRoot, 'dist', 'help', 'index.html'), 'utf8');
const helpScript = await readFile(resolve(websiteRoot, 'src', 'scripts', 'help.js'), 'utf8');
const canonicalJobs = JSON.parse(
  await readFile(resolve(websiteRoot, 'src', 'data', 'jobs.json'), 'utf8'),
);

const publicRepositoryUrl = 'https://github.com/mongre25-droid/mumble';
const publicIssuesUrl = `${publicRepositoryUrl}/issues`;
const licenceUrl = `${publicRepositoryUrl}/blob/main/LICENSE`;

const decodeText = (value) => value
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
  .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
  .replace(/<[^>]+>/g, ' ')
  .replace(/&amp;/g, '&')
  .replace(/&quot;/g, '"')
  .replace(/&#39;|&apos;/g, "'")
  .replace(/&rarr;|&#8594;/g, '→')
  .replace(/\s+/g, ' ')
  .trim();

function articleById(id) {
  const pattern = new RegExp(`<article\\b[^>]*\\bid="${id}"[^>]*>([\\s\\S]*?)<\\/article>`, 'i');
  const match = helpHtml.match(pattern);
  assert.ok(match, `Help omits maintained article #${id}`);
  return decodeText(match[1]);
}

function assertArticle(id, expectedPatterns) {
  const text = articleById(id);
  for (const pattern of expectedPatterns) {
    assert.match(text, pattern, `Help article #${id} omits ${pattern}`);
  }
}

assert.match(helpHtml, /<form\b[^>]*\bdata-help-search[^>]*\bhidden\b/i, 'search is not progressive enhancement');
assert.match(helpHtml, /<noscript>[\s\S]*browse all topics below[\s\S]*<\/noscript>/i, 'no-JavaScript browse guidance is missing');
assert.match(helpHtml, /data-help-result-context/i, 'search results do not preserve useful topic context');

const categoryLabels = [
  'Getting started',
  'Everyday jobs',
  'Privacy and access',
  'Troubleshooting',
  'Releases and project',
];
for (const label of categoryLabels) {
  assert.match(helpHtml, new RegExp(`>${label}<`, 'i'), `Help omits the ${label} browse category`);
}

for (const job of canonicalJobs) {
  assertArticle(`${job.id}-guide`, [
    new RegExp(`\\b${job.label}\\b`, 'i'),
    /Do this/i,
    /What stays local|Privacy boundary/i,
    /If it does not work/i,
  ]);
}

assertArticle('privacy-routes', [
  /Local transcription/i,
  /Cloud transcription/i,
  /Text shaping/i,
  /Reader speech/i,
  /Mumble Find/i,
  /Web Search/i,
  /Privacy boundaries/i,
]);

assertArticle('accessibility', [
  /keyboard/i,
  /effective command/i,
  /visible/i,
  /reduced motion/i,
  /screen reader|assistive technology/i,
  /Windows|macOS|Linux/i,
]);

assertArticle('troubleshooting', [
  /Capture or import/i,
  /Transcription route/i,
  /Provider keys and costs/i,
  /Text shaping/i,
  /Reader/i,
  /Deck recovery/i,
  /Mumble Find/i,
  /Web Search/i,
  /Update/i,
  /Uninstall and recovery/i,
]);

assertArticle('release-source', [
  /Release notes/i,
  /Previous accepted versions/i,
  /Known limitations/i,
  /Platform status/i,
  /MIT licence/i,
  /Inspect the source/i,
  /Contribute/i,
  /public GitHub issue tracker/i,
]);

for (const href of [
  '/downloads/#release-notes-title',
  '/downloads/#history-title',
  '/downloads/#platforms-title',
  '#known-limitations',
  licenceUrl,
  publicRepositoryUrl,
  publicIssuesUrl,
]) {
  assert.ok(helpHtml.includes(`href="${href}"`), `Help omits required destination ${href}`);
}

for (const id of [
  'choose-package',
  'install-windows',
  'first-launch',
  'first-dictation',
  'deck-recovery',
  'commands',
  ...canonicalJobs.map((job) => `${job.id}-guide`),
  'privacy-routes',
  'accessibility',
  'troubleshooting',
  'known-limitations',
  'release-source',
]) {
  assert.ok(helpHtml.includes(`href="#${id}"`), `no-JavaScript browse path omits #${id}`);
}

assert.match(helpScript, /data-help-result-context/i, 'static search does not expose result context');
assert.doesNotMatch(helpHtml, /mailto:|donat|sponsor|sales|analytics|content delivery network|third-party search/i);

console.log('PASS complete static Help contract');
