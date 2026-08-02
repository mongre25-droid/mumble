import assert from 'node:assert/strict';
import release from '../src/data/release.json' with { type: 'json' };
import { acceptedReleaseContract, validateReleaseAuthority } from './release-contract.mjs';

const acceptedArtifact = {
  version: release.version,
  sizeBytes: acceptedReleaseContract.windows.sizeBytes,
  sha256: acceptedReleaseContract.windows.integrity.value,
  provenance: {
    ...acceptedReleaseContract.windows.provenance,
    platform: 'windows',
    architecture: 'x86_64',
    format: 'ZIP',
  },
};
const evidence = {
  sourceVersion: release.version,
  artifactFacts(location) {
    return location === acceptedReleaseContract.windows.artifactLocation ? acceptedArtifact : null;
  },
  canonicalWindows: acceptedArtifact,
};

function changed(mutator) {
  const candidate = structuredClone(release);
  mutator(candidate);
  return candidate;
}

function platform(candidate, id) {
  return candidate.platforms.find((entry) => entry.id === id);
}

function variant(candidate, id) {
  return candidate.platforms.flatMap((entry) => entry.variants).find((entry) => entry.id === id);
}

let passCount = 0;

function passes(name, candidate, customEvidence = evidence) {
  assert.doesNotThrow(() => validateReleaseAuthority(candidate, customEvidence), name);
  console.log(`PASS ${name}`);
  passCount += 1;
}

function rejects(name, candidate, pattern, customEvidence = evidence) {
  assert.throws(
    () => validateReleaseAuthority(candidate, customEvidence),
    pattern,
    name,
  );
  console.log(`PASS ${name}`);
  passCount += 1;
}

passes('accepted release authority', release);

assert.deepEqual(
  release.platforms.flatMap((entry) =>
    entry.variants.map((item) => `${entry.id}:${item.architecture}:${item.format}`),
  ),
  [
    'windows:x86_64:ZIP',
    'macos:arm64:ZIP',
    'macos:x86_64:ZIP',
    'linux:x86_64:TAR.GZ',
    'linux:x86_64:ZIP',
  ],
  'release authority must expose every accepted desktop package choice',
);
console.log('PASS complete desktop package matrix');
passCount += 1;

assert.deepEqual(
  release.resources.find((resource) => resource.id === 'support'),
  {
    id: 'support',
    label: 'Issue reporting',
    availability: 'gated',
    statusLabel: 'Issue reporting is not publicly available while the source repository remains private.',
  },
  'private support must remain a gated release resource without a visitor link',
);
console.log('PASS private support release resource is gated');
passCount += 1;

passes(
  'version follows source and packaged evidence instead of an assumed release literal',
  changed((candidate) => { candidate.version = '2.7'; }),
  {
    ...evidence,
    sourceVersion: '2.7',
    canonicalWindows: { ...acceptedArtifact, version: '2.7' },
  },
);

rejects(
  'missing required field fails closed',
  changed((candidate) => { candidate.channel.id = ''; }),
  /channel\.id must be a non-empty string/,
);
rejects(
  'unknown release-authority fields fail closed',
  changed((candidate) => { candidate.legacyDownload = '/old.zip'; }),
  /release authority keys must remain/,
);
rejects(
  'source and displayed versions cannot drift',
  changed((candidate) => { candidate.version = '9.9'; }),
  /website version 9\.9 does not match source version/,
);
rejects(
  'displayed and packaged versions cannot drift',
  release,
  /packaged version 0\.94 does not match website version 0\.95/,
  { ...evidence, canonicalWindows: { ...acceptedArtifact, version: '0.94' } },
);
rejects(
  'missing packaged version fails closed',
  release,
  /canonical Windows packaged version must be a non-empty string/,
  { ...evidence, canonicalWindows: { ...acceptedArtifact, version: null } },
);
rejects(
  'duplicate platform architecture and format variants are rejected',
  changed((candidate) => {
    const duplicate = structuredClone(variant(candidate, 'macos-arm64-zip'));
    duplicate.id = 'macos-arm64-zip-copy';
    platform(candidate, 'macos').variants.push(duplicate);
  }),
  /duplicate platform\/architecture\/format variant macos:arm64:ZIP/,
);
rejects(
  'duplicate variant identities are rejected',
  changed((candidate) => {
    const duplicate = structuredClone(variant(candidate, 'macos-arm64-zip'));
    duplicate.format = 'DMG';
    platform(candidate, 'macos').variants.push(duplicate);
  }),
  /duplicate variant identity macos-arm64-zip/,
);
rejects(
  'impossible artifact sizes are rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').sizeBytes = 0; }),
  /sizeBytes must be a positive integer/,
);
rejects(
  'malformed integrity hashes are rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').integrity.value = 'not-a-hash'; }),
  /must be a lowercase SHA-256 hash/,
);
rejects(
  'malformed provenance commits are rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').provenance.sourceCommit = 'short'; }),
  /must be a lowercase full Git commit/,
);
rejects(
  'malformed provenance closure digests are rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').provenance.inputClosureDigest = 'short'; }),
  /must be a lowercase SHA-256 digest/,
);
rejects(
  'impossible provenance member counts are rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').provenance.memberCount = 0; }),
  /memberCount must be a positive integer/,
);
rejects(
  'a promised candidate artifact must exist',
  release,
  /candidate artifact does not exist/,
  { ...evidence, artifactFacts: () => null },
);
rejects(
  'displayed artifact size must match served bytes',
  release,
  /size does not match/,
  { ...evidence, artifactFacts: () => ({ ...acceptedArtifact, sizeBytes: 12 }) },
);
rejects(
  'displayed artifact hash must match served bytes',
  release,
  /hash does not match/,
  { ...evidence, artifactFacts: () => ({ ...acceptedArtifact, sha256: 'a'.repeat(64) }) },
);
rejects(
  'canonical Windows artifact is required',
  release,
  /canonical Internal\/Releases\/Mumble\.zip is missing/,
  { ...evidence, canonicalWindows: null },
);
rejects(
  'canonical package provenance is required',
  release,
  /canonical Windows provenance is missing/,
  { ...evidence, canonicalWindows: { ...acceptedArtifact, provenance: null } },
);
rejects(
  'displayed provenance source must match packaged provenance',
  release,
  /packaged provenance sourceCommit must remain/,
  {
    ...evidence,
    canonicalWindows: {
      ...acceptedArtifact,
      provenance: { ...acceptedArtifact.provenance, sourceCommit: 'a'.repeat(40) },
    },
  },
);
rejects(
  'displayed package tuple must match packaged provenance',
  release,
  /packaged provenance platform must remain/,
  {
    ...evidence,
    canonicalWindows: {
      ...acceptedArtifact,
      provenance: { ...acceptedArtifact.provenance, platform: 'linux' },
    },
  },
);
rejects(
  'displayed member count must match packaged provenance',
  release,
  /packaged provenance memberCount must remain/,
  {
    ...evidence,
    canonicalWindows: {
      ...acceptedArtifact,
      provenance: { ...acceptedArtifact.provenance, memberCount: 146 },
    },
  },
);
rejects(
  'gated variants cannot carry invented artifact facts',
  changed((candidate) => { variant(candidate, 'macos-arm64-zip').sizeBytes = 12; }),
  /gated state must not invent artifact facts/,
);
rejects(
  'gated variants cannot carry invented package requirements',
  changed((candidate) => { variant(candidate, 'linux-x86_64-zip').requirements = ['Any Linux']; }),
  /gated state must not invent package requirements/,
);
rejects(
  'every accepted variant must remain represented',
  changed((candidate) => { platform(candidate, 'macos').variants.shift(); }),
  /missing macos-arm64-zip variant state/,
);
rejects(
  'variant identity drift is rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').id = 'windows-renamed'; }),
  /accepted Windows artifact must be the sole candidate/,
);
rejects(
  'maintained history cannot invent a previous accepted download',
  changed((candidate) => {
    candidate.history.acceptedVersions.push({ version: '0.94', href: '/Mumble-0.94.zip' });
  }),
  /must stay empty until a previous accepted download is maintained/,
);
rejects(
  'resource destinations cannot leave the approved project boundary',
  changed((candidate) => { candidate.resources[3].href = 'https://example.com/source'; }),
  /must be a safe Mumble destination/,
);
rejects(
  'private support cannot become an available visitor resource',
  changed((candidate) => {
    candidate.resources.find((resource) => resource.id === 'support').availability = 'available';
  }),
  /resources\[5\]\.availability must remain "gated"/,
);
rejects(
  'private support cannot acquire an active issue-tracker destination',
  changed((candidate) => {
    candidate.resources.find((resource) => resource.id === 'support').href =
      'https://github.com/mongre25-droid/mumble/issues';
  }),
  /resources\[5\] keys must remain/,
);
rejects(
  'integrity guidance cannot silently lose a verification step',
  changed((candidate) => { candidate.integrityGuide.steps.pop(); }),
  /integrityGuide\.steps must remain/,
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
  'checksum status cannot contradict the canonical-byte match',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').integrity.checksumStatus.label = 'Unchecked';
  }),
  /windows-x86_64-zip\.integrity\.checksumStatus\.label must remain/,
);
rejects(
  'checksum status explanation cannot contradict the canonical-byte match',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').integrity.checksumStatus.summary = 'No checksum was checked.';
  }),
  /windows-x86_64-zip\.integrity\.checksumStatus\.summary must remain/,
);
rejects(
  'publisher signature label cannot contradict its unaccepted state',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').integrity.publisherSignature.label = 'Accepted';
  }),
  /windows-x86_64-zip\.integrity\.publisherSignature\.label must remain/,
);
rejects(
  'publisher signature explanation cannot imply accepted signing',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').integrity.publisherSignature.summary = 'Publisher signing passed.';
  }),
  /windows-x86_64-zip\.integrity\.publisherSignature\.summary must remain/,
);
for (const [field, contradictoryValue] of [
  ['label', 'Published release notes'],
  ['summary', 'The public release is available.'],
]) {
  rejects(
    `release notes ${field} cannot contradict the unpublished state`,
    changed((candidate) => { candidate.releaseNotes[field] = contradictoryValue; }),
    new RegExp(`releaseNotes\\.${field} must remain`),
  );
}
for (const [field, contradictoryValue] of [
  ['size', '1 MB'],
  ['requirements', 'Any computer'],
  ['integrity', 'Verified'],
  ['action', 'Download now'],
]) {
  rejects(
    `unavailable ${field} copy cannot invent a release`,
    changed((candidate) => { candidate.unavailableFacts[field] = contradictoryValue; }),
    new RegExp(`unavailableFacts\\.${field} must remain`),
  );
}
for (const id of ['windows', 'macos', 'linux', 'unknown', 'mobile']) {
  rejects(
    `${id} recommendation label cannot invent a route`,
    changed((candidate) => { candidate.recommendations[id].label = 'Download released app'; }),
    new RegExp(`recommendations\\.${id}\\.label must remain`),
  );
}
for (const id of ['windows', 'macos', 'linux']) {
  for (const [field, contradictoryValue] of [
    ['label', `${id} released`],
    ['availability', 'published'],
    ['availabilityLabel', 'Public release'],
    ['statusLabel', 'Available'],
    ['gate', 'Every release gate is complete.'],
  ]) {
    rejects(
      `${id} ${field} cannot contradict its platform state`,
      changed((candidate) => { platform(candidate, id)[field] = contradictoryValue; }),
      new RegExp(`${id}\\.${field} must remain`),
    );
  }
}
for (const id of Object.keys(acceptedReleaseContract.gatedVariants)) {
  rejects(
    `${id} status cannot contradict its gated state`,
    changed((candidate) => { variant(candidate, id).statusLabel = 'Available'; }),
    new RegExp(`${id}\\.statusLabel must remain`),
  );
}
rejects(
  'Windows requirements cannot broaden the accepted package claim',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').requirements[0] = 'Any operating system';
  }),
  /windows-x86_64-zip\.requirements must remain/,
);
rejects(
  'coordinated artifact replacement cannot redefine accepted bytes',
  changed((candidate) => {
    const windows = variant(candidate, 'windows-x86_64-zip');
    windows.sizeBytes = 12;
    windows.integrity.value = 'a'.repeat(64);
  }),
  /windows-x86_64-zip\.sizeBytes must remain/,
  {
    ...evidence,
    artifactFacts: () => ({ ...acceptedArtifact, sizeBytes: 12, sha256: 'a'.repeat(64) }),
    canonicalWindows: { ...acceptedArtifact, sizeBytes: 12, sha256: 'a'.repeat(64) },
  },
);
rejects(
  'safe-looking artifact path drift is rejected',
  changed((candidate) => {
    variant(candidate, 'windows-x86_64-zip').artifactLocation = '/renamed.zip';
  }),
  /windows-x86_64-zip\.artifactLocation must remain/,
);
rejects(
  'package identity drift is rejected',
  changed((candidate) => { variant(candidate, 'windows-x86_64-zip').format = 'MSI'; }),
  /windows-x86_64-zip\.format must remain/,
);
for (const unsafeLocation of ['//outside.zip', '/C:/outside.zip', String.raw`\outside.zip`]) {
  rejects(
    `unsafe artifact path ${JSON.stringify(unsafeLocation)} is rejected`,
    changed((candidate) => {
      variant(candidate, 'windows-x86_64-zip').artifactLocation = unsafeLocation;
    }),
    /must be a safe Mumble destination/,
  );
}

console.log(`Release contract regression passed: ${passCount} invariant cases.`);
