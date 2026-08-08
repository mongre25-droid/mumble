import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const websiteRoot = resolve(import.meta.dirname, '..');
const jobsPath = resolve(websiteRoot, 'src/data/jobs.json');
const captureScript = resolve(websiteRoot, 'scripts/capture-find-evidence.mjs');
const jobs = JSON.parse(await readFile(jobsPath, 'utf8'));

function findAuthority(jobData) {
  const find = jobData.find((job) => job.id === 'find');
  assert.ok(find?.story?.presentation, 'jobs.json must define the canonical Find presentation');
  const { captures, boundaries } = find.story.presentation;
  assert.deepEqual(captures.map((capture) => capture.route), ['deck', 'local', 'web']);
  assert.deepEqual(boundaries.map((boundary) => boundary.route), ['deck', 'local', 'web']);
  return {
    find,
    local: boundaries.find((boundary) => boundary.route === 'local'),
    web: boundaries.find((boundary) => boundary.route === 'web'),
  };
}

function capturePlan(inputPath) {
  return JSON.parse(execFileSync(
    process.execPath,
    [captureScript, '--plan', '--jobs', inputPath],
    { cwd: websiteRoot, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 },
  ));
}

function assertPlanMatchesAuthority(plan, authority) {
  assert.equal(plan.schemaVersion, 1, 'capture generator must expose its structured canonical-data plan');
  assert.deepEqual(plan.captures.map((capture) => capture.route), ['deck', 'local', 'web']);
  const localPlan = plan.captures.find((capture) => capture.route === 'local');
  const webPlan = plan.captures.find((capture) => capture.route === 'web');

  assert.deepEqual(localPlan.semantic.example, authority.local.example);
  assert.deepEqual(localPlan.semantic.actions, authority.local.actions);
  assert.equal(localPlan.fixture.rows[0].name, authority.local.example.title);

  assert.deepEqual(webPlan.semantic.example, authority.web.example);
  assert.deepEqual(webPlan.semantic.facts, authority.web.facts);
  assert.deepEqual(webPlan.semantic.actions, authority.web.actions);
  assert.equal(webPlan.semantic.failure, authority.web.failure);
  assert.equal(webPlan.fixture.query, authority.web.example.quote);
  assert.equal(webPlan.fixture.provider, authority.web.facts[0].detail);
}

const authority = findAuthority(jobs);
assertPlanMatchesAuthority(capturePlan(jobsPath), authority);

const temporaryRoot = await mkdtemp(join(tmpdir(), 'mumble-find-authority-'));
try {
  const changedJobs = structuredClone(jobs);
  const changed = findAuthority(changedJobs);
  changed.local.example.title = 'Authority mutation.pdf';
  changed.local.actions[1] = 'Reveal authority mutation';
  changed.web.example.quote = 'Authority mutation selected words.';
  changed.web.facts[0].detail = 'Brave';
  changed.web.actions[1] = 'Authority mutation stays private';
  changed.web.failure = 'Authority mutation reports browser failure.';

  const changedJobsPath = join(temporaryRoot, 'jobs.json');
  await writeFile(changedJobsPath, `${JSON.stringify(changedJobs, null, 2)}\n`, 'utf8');
  assertPlanMatchesAuthority(capturePlan(changedJobsPath), changed);
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

console.log('FIND_JOB_AUTHORITY_OK');
