function fail(message) {
  throw new Error(message);
}

function requireString(value, field) {
  if (typeof value !== 'string' || value.trim() === '') fail(`${field} must be a non-empty string`);
}
export const acceptedReleaseContract = Object.freeze({
  application: 'Mumble',
  version: '0.95',
  channel: {
    id: 'pre-release',
    label: 'Pre-release candidate',
  },
  publication: {
    state: 'gated',
    publishedAt: null,
    label: 'Public release remains gated',
    statusLabel: 'Gated',
    dateLabel: 'Not published',
    reason: 'Installation lifecycle, physical acceptance, signing, deployment, release, and owner acceptance are not complete.',
  },
  releaseNotes: {
    state: 'not-published',
    label: 'Not published for this candidate',
    summary: 'Public release notes will follow an accepted public release; this candidate has no published release notes.',
  },
  windows: {
    id: 'windows',
    availability: 'candidate',
    architecture: 'x86_64',
    format: 'ZIP',
    artifactLocation: '/Mumble.zip',
    sizeBytes: 1207711,
    integrity: {
      algorithm: 'SHA-256',
      value: '70794b4d13c1c38662425deb5700865728955f4fac78dc2d083436f63fb99493',
      publisherSignature: {
        state: 'not-accepted',
        label: 'Not accepted',
      },
    },
  },
  gatedPlatformIds: ['macos', 'linux'],
});

function requireExact(value, expected, field) {
  if (value !== expected) fail(`${field} must remain ${JSON.stringify(expected)}`);
}


export function validateReleaseAuthority(authority, evidence) {
  const accepted = acceptedReleaseContract;
  if (authority.schema !== 1) fail('schema must be 1');
  requireString(authority.application, 'application');
  requireString(authority.version, 'version');
  requireExact(authority.application, accepted.application, 'application');
  requireExact(authority.version, accepted.version, 'version');

  requireString(authority.channel?.id, 'channel.id');
  requireString(authority.channel?.label, 'channel.label');
  requireExact(authority.channel.id, accepted.channel.id, 'channel.id');
  requireExact(authority.channel.label, accepted.channel.label, 'channel.label');

  requireString(authority.publication?.state, 'publication.state');
  requireString(authority.publication?.label, 'publication.label');
  requireString(authority.publication?.statusLabel, 'publication.statusLabel');
  requireString(authority.publication?.dateLabel, 'publication.dateLabel');
  requireString(authority.publication?.reason, 'publication.reason');
  requireExact(authority.publication.state, accepted.publication.state, 'publication.state');
  requireExact(authority.publication.publishedAt, accepted.publication.publishedAt, 'publication.publishedAt');
  requireExact(authority.publication.label, accepted.publication.label, 'publication.label');
  requireExact(authority.publication.statusLabel, accepted.publication.statusLabel, 'publication.statusLabel');
  requireExact(authority.publication.dateLabel, accepted.publication.dateLabel, 'publication.dateLabel');
  requireExact(authority.publication.reason, accepted.publication.reason, 'publication.reason');

  requireString(authority.releaseNotes?.state, 'releaseNotes.state');
  requireString(authority.releaseNotes?.label, 'releaseNotes.label');
  requireString(authority.releaseNotes?.summary, 'releaseNotes.summary');
  requireExact(authority.releaseNotes.state, accepted.releaseNotes.state, 'releaseNotes.state');
  requireExact(authority.releaseNotes.label, accepted.releaseNotes.label, 'releaseNotes.label');
  requireExact(authority.releaseNotes.summary, accepted.releaseNotes.summary, 'releaseNotes.summary');
  requireString(authority.unavailableFacts?.artifactMetadata, 'unavailableFacts.artifactMetadata');
  requireString(authority.unavailableFacts?.integrity, 'unavailableFacts.integrity');
  requireString(authority.unavailableFacts?.download, 'unavailableFacts.download');

  requireString(evidence?.sourceVersion, 'evidence.sourceVersion');
  if (authority.version !== evidence.sourceVersion) {
    fail(`website version ${authority.version} does not match source version ${evidence.sourceVersion}`);
  }

  const recommendations = authority.recommendations;
  const recommendationContract = {
    windows: { href: accepted.windows.artifactLocation, download: true },
    macos: { href: '/downloads/#macos', download: false },
    linux: { href: '/downloads/#linux', download: false },
    unknown: { href: '/downloads/#platforms-title', download: false },
    mobile: { href: '/downloads/#platforms-title', download: false },
  };
  for (const [id, expected] of Object.entries(recommendationContract)) {
    requireString(recommendations?.[id]?.label, `recommendations.${id}.label`);
    requireString(recommendations?.[id]?.href, `recommendations.${id}.href`);
    if (typeof recommendations[id].download !== 'boolean') {
      fail(`recommendations.${id}.download must be boolean`);
    }
    requireExact(recommendations[id].href, expected.href, `recommendations.${id}.href`);
    requireExact(recommendations[id].download, expected.download, `recommendations.${id}.download`);
  }
  if (recommendations.mobile.label !== recommendations.unknown.label) {
    fail('mobile and unknown visitors must receive the same neutral desktop-download choice');
  }

  if (!Array.isArray(authority.platforms) || authority.platforms.length === 0) {
    fail('platforms must be a non-empty array');
  }

  const representedPlatforms = new Set();
  const variants = new Set();
  let candidateArtifacts = 0;
  for (const platform of authority.platforms) {
    requireString(platform.id, 'platform.id');
    requireString(platform.label, `${platform.id}.label`);
    requireString(platform.availability, `${platform.id}.availability`);
    requireString(platform.availabilityLabel, `${platform.id}.availabilityLabel`);
    requireString(platform.gate, `${platform.id}.gate`);
    representedPlatforms.add(platform.id);

    const variantKey = `${platform.id}:${platform.architecture ?? 'none'}`;
    if (variants.has(variantKey)) fail(`duplicate platform/architecture variant ${variantKey}`);
    variants.add(variantKey);

    if (platform.availability === 'candidate') {
      candidateArtifacts += 1;
      requireString(platform.architecture, `${platform.id}.architecture`);
      requireString(platform.format, `${platform.id}.format`);
      requireString(platform.artifactLocation, `${platform.id}.artifactLocation`);
      if (!Number.isSafeInteger(platform.sizeBytes) || platform.sizeBytes <= 0) {
        fail(`${platform.id}.sizeBytes must be a positive integer`);
      }
      if (!Array.isArray(platform.requirements) || platform.requirements.length === 0) {
        fail(`${platform.id}.requirements must be a non-empty array`);
      }
      platform.requirements.forEach((value, index) => requireString(value, `${platform.id}.requirements[${index}]`));
      if (platform.integrity?.algorithm !== 'SHA-256') fail(`${platform.id} must use SHA-256 integrity`);
      if (!/^[0-9a-f]{64}$/.test(platform.integrity?.value ?? '')) {
        fail(`${platform.id}.integrity.value must be a lowercase SHA-256 hash`);
      }
      requireString(platform.integrity?.publisherSignature?.state, `${platform.id}.integrity.publisherSignature.state`);
      requireString(platform.integrity?.publisherSignature?.label, `${platform.id}.integrity.publisherSignature.label`);

      const pathSegments = platform.artifactLocation.slice(1).split('/');
      if (
        !/^\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*$/.test(platform.artifactLocation) ||
        pathSegments.some((segment) => segment === '.' || segment === '..')
      ) {
        fail(`${platform.id}.artifactLocation must be a safe POSIX root-relative path`);
      }

      if (platform.id !== accepted.windows.id) fail('the accepted Windows artifact must be the sole candidate');
      requireExact(platform.availability, accepted.windows.availability, 'windows.availability');
      requireExact(platform.architecture, accepted.windows.architecture, 'windows.architecture');
      requireExact(platform.format, accepted.windows.format, 'windows.format');
      requireExact(platform.artifactLocation, accepted.windows.artifactLocation, 'windows.artifactLocation');
      requireExact(platform.sizeBytes, accepted.windows.sizeBytes, 'windows.sizeBytes');
      requireExact(platform.integrity.algorithm, accepted.windows.integrity.algorithm, 'windows.integrity.algorithm');
      requireExact(platform.integrity.value, accepted.windows.integrity.value, 'windows.integrity.value');
      requireExact(
        platform.integrity.publisherSignature.state,
        accepted.windows.integrity.publisherSignature.state,
        'windows.integrity.publisherSignature.state',
      );
      requireExact(
        platform.integrity.publisherSignature.label,
        accepted.windows.integrity.publisherSignature.label,
        'windows.integrity.publisherSignature.label',
      );

      const artifact = evidence.artifactFacts(platform.artifactLocation);
      if (!artifact) fail(`${platform.id} candidate artifact does not exist`);
      if (artifact.sizeBytes !== platform.sizeBytes) {
        fail(`${platform.id} size does not match ${platform.artifactLocation}`);
      }
      if (artifact.sha256 !== platform.integrity.value) {
        fail(`${platform.id} hash does not match ${platform.artifactLocation}`);
      }
      if (!evidence.canonicalWindows) fail('canonical Internal/Releases/Mumble.zip is missing');
      if (evidence.canonicalWindows.sha256 !== platform.integrity.value) {
        fail('Windows authority does not match canonical Internal/Releases/Mumble.zip');
      }
      if (evidence.canonicalWindows.sizeBytes !== platform.sizeBytes) {
        fail('Windows authority size does not match canonical Internal/Releases/Mumble.zip');
      }
    } else if (platform.availability === 'gated') {
      requireString(platform.statusLabel, `${platform.id}.statusLabel`);
      if (!accepted.gatedPlatformIds.includes(platform.id)) {
        fail(`${platform.id} is not an accepted gated platform`);
      }
      if (
        platform.architecture !== null ||
        platform.format !== null ||
        platform.sizeBytes !== null ||
        platform.integrity !== null ||
        platform.artifactLocation !== null
      ) {
        fail(`${platform.id} gated state must not invent artifact facts`);
      }
      if (!Array.isArray(platform.requirements) || platform.requirements.length !== 0) {
        fail(`${platform.id} gated state must not invent package requirements`);
      }
    } else {
      fail(`${platform.id}.availability must be candidate or gated`);
    }
  }

  for (const requiredPlatform of [accepted.windows.id, ...accepted.gatedPlatformIds]) {
    if (!representedPlatforms.has(requiredPlatform)) fail(`missing ${requiredPlatform} platform state`);
  }
  if (authority.platforms.length !== 3) fail('platforms must contain exactly Windows, macOS, and Linux');
  if (candidateArtifacts !== 1) fail('exactly one candidate artifact must be represented');

  return authority;
}
