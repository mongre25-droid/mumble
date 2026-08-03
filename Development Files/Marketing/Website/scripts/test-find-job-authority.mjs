import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const websiteRoot = resolve(import.meta.dirname, '..');
const jobs = JSON.parse(await readFile(resolve(websiteRoot, 'src/data/jobs.json'), 'utf8'));
const componentSource = await readFile(resolve(websiteRoot, 'src/components/JobStory.astro'), 'utf8');
const find = jobs.find((job) => job.id === 'find');

assert.ok(find, 'jobs.json must define the canonical Find job');
assert.ok(find.story?.presentation, 'Find presentation authority must live in jobs.json');
assert.ok(Array.isArray(find.story.presentation.captures), 'Find capture data must live in jobs.json');
assert.equal(find.story.presentation.captures.length, 3, 'Find must define Deck, local, and web captures');
assert.deepEqual(
  find.story.presentation.captures.map((capture) => capture.route),
  ['deck', 'local', 'web'],
  'Find captures must keep Deck, Mumble Find, and Web Search separate',
);
assert.ok(Array.isArray(find.story.presentation.boundaries), 'Find boundary cards must live in jobs.json');
assert.deepEqual(
  find.story.presentation.boundaries.map((boundary) => boundary.route),
  ['deck', 'local', 'web'],
  'Find boundary data must keep Deck, Mumble Find, and Web Search separate',
);

for (const boundary of find.story.presentation.boundaries) {
  assert.ok(boundary.label && boundary.title && boundary.body, `${boundary.route} boundary copy is incomplete`);
  assert.ok(boundary.actionsLabel, `${boundary.route} boundary action label is missing`);
  assert.ok(Array.isArray(boundary.actions) && boundary.actions.length > 0, `${boundary.route} boundary actions are missing`);
}

const local = find.story.presentation.boundaries.find((boundary) => boundary.route === 'local');
const web = find.story.presentation.boundaries.find((boundary) => boundary.route === 'web');
assert.ok(local?.example?.label && local.example.title && local.example.detail, 'local Find example authority is incomplete');
assert.ok(Array.isArray(web?.facts) && web.facts.length >= 3, 'Web Search provider and consent facts are incomplete');
assert.ok(web?.example?.label && web.example.quote, 'Web Search selected-text example authority is incomplete');
assert.ok(web?.failure, 'Web Search failure label must live in jobs.json');

for (const task of find.story.tasks) {
  if (['local-app-file', 'web-search'].includes(task.id)) {
    assert.ok(task.example?.label && task.example.detail, `${task.id} example authority must live beside the task in jobs.json`);
  }
}

assert.doesNotMatch(
  componentSource,
  /job\.id\s*===\s*['"]find['"]|job\.id\s*!==\s*['"]find['"]|['"]find['"]\s*===\s*job\.id/,
  'JobStory.astro must render Find from the supplied schema without a Find-only authority branch',
);

const copyKeys = new Set([
  'label', 'title', 'summary', 'body', 'note', 'alt', 'caption', 'truthLabel',
  'ariaLabel', 'actionsLabel', 'failure', 'quote', 'term', 'detail', 'actionLabel',
]);
const canonicalCopy = [];
function collectCopy(value, key = '') {
  if (Array.isArray(value)) {
    for (const item of value) collectCopy(item, key);
    return;
  }
  if (!value || typeof value !== 'object') {
    if (typeof value === 'string' && copyKeys.has(key) && value.length >= 8) canonicalCopy.push(value);
    return;
  }
  for (const [childKey, childValue] of Object.entries(value)) collectCopy(childValue, childKey);
}
collectCopy(find.story.presentation);
for (const task of find.story.tasks) collectCopy(task.example);

const otherJobData = JSON.stringify(jobs.filter((job) => job.id !== 'find'));
const duplicated = canonicalCopy.filter((copy) => componentSource.includes(copy) && !otherJobData.includes(copy));
assert.deepEqual(
  duplicated,
  [],
  `JobStory.astro duplicates canonical Find copy:\n${duplicated.map((copy) => `- ${copy}`).join('\n')}`,
);

console.log('FIND_JOB_AUTHORITY_OK');
