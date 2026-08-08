import assert from 'node:assert/strict';
import jobs from '../src/data/jobs.json' with { type: 'json' };

const expectedJobIds = ['write', 'capture', 'shape', 'listen', 'find'];

function requireText(value, path) {
  assert.equal(typeof value, 'string', `${path} must be text`);
  assert.ok(value.trim(), `${path} must not be empty`);
  return value;
}

function resolveMedia(job, reference) {
  assert.equal(typeof reference, 'object', `${job.id}.homeMontage.media must be a canonical reference`);
  assert.ok(reference && !Array.isArray(reference), `${job.id}.homeMontage.media must be a canonical reference`);
  for (const duplicatedField of ['src', 'width', 'height', 'alt']) {
    assert.equal(
      Object.hasOwn(reference, duplicatedField),
      false,
      `${job.id}.homeMontage.media must reference canonical media instead of copying ${duplicatedField}`,
    );
  }

  if (reference.kind === 'demonstration-presentation') {
    return job.story?.demonstration?.presentation;
  }
  if (reference.kind === 'demonstration-state') {
    return job.story?.demonstration?.states?.find((state) => state.id === reference.stateId);
  }
  if (reference.kind === 'job-demonstration-presentation') {
    return jobs.find((candidate) => candidate.id === reference.jobId)?.story?.demonstration?.presentation;
  }
  if (reference.kind === 'presentation-capture') {
    return job.story?.presentation?.captures?.find((capture) => capture.route === reference.route);
  }
  assert.fail(`${job.id}.homeMontage.media.kind is unsupported: ${String(reference.kind)}`);
}

assert.deepEqual(jobs.map((job) => job.id), expectedJobIds, 'canonical five-job order changed');

for (const job of jobs) {
  requireText(job.label, `${job.id}.label`);
  const montage = job.homeMontage;
  assert.equal(typeof montage, 'object', `${job.id}.homeMontage must own the Home projection`);
  assert.ok(montage && !Array.isArray(montage), `${job.id}.homeMontage must own the Home projection`);
  for (const field of ['action', 'title', 'body', 'evidence']) {
    requireText(montage[field], `${job.id}.homeMontage.${field}`);
  }

  const media = resolveMedia(job, montage.media);
  assert.ok(media, `${job.id}.homeMontage.media does not resolve inside canonical jobs.json`);
  const source = media.src ?? media.image;
  requireText(source, `${job.id}.resolvedMedia.src`);
  requireText(montage.mediaAlt ?? media.alt, `${job.id}.resolvedMedia.alt`);
  assert.ok(Number.isInteger(media.width) && media.width > 0, `${job.id}.resolvedMedia.width must be a positive integer`);
  assert.ok(Number.isInteger(media.height) && media.height > 0, `${job.id}.resolvedMedia.height must be a positive integer`);

  if (montage.route !== undefined) {
    assert.ok(Array.isArray(montage.route) && montage.route.length > 0, `${job.id}.homeMontage.route must be a non-empty list`);
    requireText(montage.routeLabel, `${job.id}.homeMontage.routeLabel`);
    montage.route.forEach((item, index) => requireText(item, `${job.id}.homeMontage.route[${index}]`));
  }
}

console.log('HOME_MONTAGE_AUTHORITY_OK');
