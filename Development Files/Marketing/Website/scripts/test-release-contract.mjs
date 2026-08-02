import assert from 'node:assert/strict';
import release from '../src/data/release.json' with { type: 'json' };
import { acceptedReleaseContract, validateReleaseAuthority } from './release-contract.mjs';

const acceptedArtifact = {
  sizeBytes: acceptedReleaseContract.windows.sizeBytes,
  sha256: acceptedReleaseContract.windows.integrity.value,
};
const evidence = {
  sourceVersion: '0.95',
  artifactFacts(location) {
    return location === '/Mumble.zip' ? acceptedArtifact : null;
  },
  canonicalWindows: acceptedArtifact,
};

function changed(mutator) {
  const candidate = structuredClone(release);
  mutator(candidate);
  return candidate;
}

let rejectionCount = 0;

function rejects(name, candidate, pattern, customEvidence = evidence) {
  assert.throws(
    () => validateReleaseAuthority(candidate, customEvidence),
    pattern,
    name,
  );
  console.log(`PASS ${name}`);
  rejectionCount += 1;
}

assert.doesNotThrow(() => validateReleaseAuthority(release, evidence));
console.log('PASS accepted release authority');

rejects(
  'missing required field fails closed',
  changed((candidate) => { candidate.channel.id = ''; }),
  /channel\.id must be a non-empty string/,
);
rejects(
  'source and displayed versions cannot drift',
  changed((candidate) => { candidate.version = '9.9'; }),
  /version must remain/,
);
rejects(
  'duplicate platform and architecture variants are rejected',
  changed((candidate) => { candidate.platforms.push(structuredClone(candidate.platforms[0])); }),
  /duplicate platform\/architecture variant windows:x86_64/,
);
rejects(
  'impossible artifact sizes are rejected',
  changed((candidate) => { candidate.platforms[0].sizeBytes = 0; }),
  /sizeBytes must be a positive integer/,
);
rejects(
  'malformed integrity hashes are rejected',
  changed((candidate) => { candidate.platforms[0].integrity.value = 'not-a-hash'; }),
  /must be a lowercase SHA-256 hash/,
);
rejects(
  'a promised candidate artifact must exist',
  release,
  /candidate artifact does not exist/,
  { ...evidence, artifactFacts: () => null },
);
rejects(
  'displayed artifact size must match packaged bytes',
  release,
  /size does not match/,
  { ...evidence, artifactFacts: () => ({ ...acceptedArtifact, sizeBytes: 12 }) },
);
rejects(
  'gated platforms cannot carry invented artifact facts',
  changed((candidate) => { candidate.platforms[1].artifactLocation = '/invented.dmg'; }),
  /gated state must not invent artifact facts/,
);
rejects(
  'publication state cannot outrun accepted release gates',
  changed((candidate) => {
    candidate.publication.state = 'published';
    candidate.publication.publishedAt = '2026-08-02';
  }),
  /publication\.state must remain/,
);
for (const [field, contradictoryValue] of [
  ['label', 'Public release is available'],
  ['statusLabel', 'Published'],
  ['dateLabel', 'Published today'],
  ['reason', 'Every release gate is complete.'],
]) {
  rejects(
    `publication ${field} cannot contradict the gated state`,
    changed((candidate) => { candidate.publication[field] = contradictoryValue; }),
    new RegExp(`publication\\.${field} must remain`),
  );
}
rejects(
  'publisher signature label cannot contradict its unaccepted state',
  changed((candidate) => { candidate.platforms[0].integrity.publisherSignature.label = 'Accepted'; }),
  /windows\.integrity\.publisherSignature\.label must remain/,
);
rejects(
  'coordinated artifact replacement cannot redefine accepted bytes',
  changed((candidate) => {
    candidate.platforms[0].sizeBytes = 12;
    candidate.platforms[0].integrity.value = 'a'.repeat(64);
  }),
  /windows\.sizeBytes must remain/,
  {
    ...evidence,
    artifactFacts: () => ({ sizeBytes: 12, sha256: 'a'.repeat(64) }),
    canonicalWindows: { sizeBytes: 12, sha256: 'a'.repeat(64) },
  },
);
for (const unsafeLocation of ['//outside.zip', '/C:/outside.zip', String.raw`\outside.zip`]) {
  rejects(
    `unsafe artifact path ${JSON.stringify(unsafeLocation)} is rejected`,
    changed((candidate) => { candidate.platforms[0].artifactLocation = unsafeLocation; }),
    /safe POSIX root-relative path/,
  );
}


console.log(`Release contract regression passed: ${rejectionCount + 1} invariant cases.`);
