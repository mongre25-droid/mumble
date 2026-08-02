function fail(message) {
  throw new Error(message);
}

function requireString(value, field) {
  if (typeof value !== 'string' || value.trim() === '') fail(`${field} must be a non-empty string`);
}

function requireExact(value, expected, field) {
  if (value !== expected) fail(`${field} must remain ${JSON.stringify(expected)}`);
}

function requireArrayExact(value, expected, field) {
  if (
    !Array.isArray(value) ||
    value.length !== expected.length ||
    value.some((item, index) => item !== expected[index])
  ) {
    fail(`${field} must remain ${JSON.stringify(expected)}`);
  }
}

function requireRecordKeys(value, expected, field) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${field} must be an object`);
  }
  const actualKeys = Object.keys(value).sort();
  const expectedKeys = [...expected].sort();
  if (actualKeys.length !== expectedKeys.length || actualKeys.some((key, index) => key !== expectedKeys[index])) {
    fail(`${field} keys must remain ${JSON.stringify(expectedKeys)}`);
  }
}

function requireSafeHref(value, field) {
  requireString(value, field);
  const safeFragment = /^#[A-Za-z][A-Za-z0-9_-]*$/.test(value);
  const safeRootPath = /^\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*\/?(?:#[A-Za-z][A-Za-z0-9_-]*)?$/.test(value);
  const safeProjectUrl = /^https:\/\/github\.com\/mongre25-droid\/mumble(?:\/(?:blob\/main\/LICENSE|issues))?$/.test(value);
  if (!safeFragment && !safeRootPath && !safeProjectUrl) {
    fail(`${field} must be a safe Mumble destination`);
  }
}

const windowsVariant = {
  id: 'windows-x86_64-zip',
  availability: 'candidate',
  availabilityLabel: 'Candidate artifact',
  statusLabel: 'Downloadable candidate',
  architecture: 'x86_64',
  format: 'ZIP',
  sizeBytes: 1207711,
  requirements: [
    'Windows 10 or 11 (64-bit)',
    'Internet access for first-time setup',
    'Keep the extracted Mumble folder in a permanent location',
  ],
  artifactLocation: '/Mumble.zip',
  gate: 'The repository candidate can be downloaded for inspection. Installation lifecycle, physical acceptance, publisher signing, deployment, and public release remain open.',
  integrity: {
    algorithm: 'SHA-256',
    value: '70794b4d13c1c38662425deb5700865728955f4fac78dc2d083436f63fb99493',
    checksumStatus: {
      state: 'matched',
      label: 'Matches canonical package bytes',
      summary: "The published value matches the repository's canonical Internal/Releases/Mumble.zip bytes.",
    },
    publisherSignature: {
      state: 'not-accepted',
      label: 'Not accepted',
      summary: 'No accepted publisher signing or clean-install trust result exists.',
    },
  },
  provenance: {
    schema: 'mumble.release-provenance.v1',
    sourceCommit: '9d583ec31432b975e3a8955d2ebecf9c3ca87080',
    inputClosureDigest: '87f3031fababeb82a39e356e975efcedd2ce329466e0654a1ae2afb2d3b0a6a6',
    memberCount: 147,
  },
};

const gatedVariants = {
  'macos-arm64-zip': {
    platformId: 'macos',
    architecture: 'arm64',
    format: 'ZIP',
    gate: 'No accepted Apple Silicon artifact is published. Physical Apple Silicon execution, installation lifecycle, signing, and notarisation remain open.',
  },
  'macos-x86_64-zip': {
    platformId: 'macos',
    architecture: 'x86_64',
    format: 'ZIP',
    gate: 'No accepted Intel Mac artifact is published. Physical Intel execution, installation lifecycle, signing, and notarisation remain open.',
  },
  'linux-x86_64-tar-gz': {
    platformId: 'linux',
    architecture: 'x86_64',
    format: 'TAR.GZ',
    gate: 'No accepted Linux TAR.GZ is published. Distribution lifecycle, physical GNOME/KDE and X11/Wayland checks, signing, and release remain open.',
  },
  'linux-x86_64-zip': {
    platformId: 'linux',
    architecture: 'x86_64',
    format: 'ZIP',
    gate: 'No accepted Linux ZIP is published. Distribution lifecycle, physical GNOME/KDE and X11/Wayland checks, signing, and release remain open.',
  },
};

export const acceptedReleaseContract = Object.freeze({
  schema: 2,
  application: 'Mumble',
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
    publishedAt: null,
    href: null,
  },
  history: {
    state: 'not-maintained',
    label: 'Previous accepted versions',
    statusLabel: 'None maintained',
    summary: 'No previous accepted website downloads are maintained for this gated candidate. No rollback package is offered here.',
  },
  unavailableFacts: {
    size: 'Not published',
    requirements: 'Not published',
    integrity: 'No artifact to verify',
    action: 'Unavailable',
  },
  recommendations: {
    windows: { label: 'Download candidate for Windows', href: '/Mumble.zip', download: true },
    macos: { label: 'View macOS status', href: '/downloads/#macos', download: false },
    linux: { label: 'View Linux status', href: '/downloads/#linux', download: false },
    unknown: { label: 'View desktop downloads', href: '/downloads/#platforms-title', download: false },
    mobile: { label: 'View desktop downloads', href: '/downloads/#platforms-title', download: false },
  },
  resources: [
    { id: 'platform-status', label: 'Platform status', href: '#platforms-title' },
    { id: 'integrity', label: 'Integrity guide', href: '#integrity-title' },
    { id: 'release-notes', label: 'Release notes', href: '#release-notes-title' },
    { id: 'source', label: 'Source record', href: 'https://github.com/mongre25-droid/mumble' },
    { id: 'licence', label: 'MIT licence', href: 'https://github.com/mongre25-droid/mumble/blob/main/LICENSE' },
    {
      id: 'support',
      label: 'Issue reporting',
      availability: 'gated',
      statusLabel: 'Issue reporting is not publicly available while the source repository remains private.',
    },
  ],
  integrityGuide: {
    title: 'Verify before you run it',
    summary: 'The byte count and SHA-256 identify the repository candidate. A matching checksum does not provide publisher-signature, installation, physical-acceptance, or public-release evidence.',
    steps: [
      'Compare the downloaded file size with the exact byte count.',
      'Compute SHA-256 and compare all 64 hexadecimal characters.',
      'Check publisher-signature and release-gate status separately; neither is implied by a matching checksum.',
    ],
  },
  platformOrder: ['windows', 'macos', 'linux'],
  platforms: {
    windows: {
      label: 'Windows',
      availability: 'candidate',
      availabilityLabel: 'Candidate artifact',
      statusLabel: 'Public release gated',
      gate: 'Validated repository candidate bytes are available here, but this is not an installed, signed, deployed, or publicly released package.',
    },
    macos: {
      label: 'macOS',
      availability: 'gated',
      availabilityLabel: 'No accepted artifact',
      statusLabel: 'Gated',
      gate: 'No current accepted macOS artifact exists. Packaging, physical checks, signing, and notarisation remain open.',
    },
    linux: {
      label: 'Linux',
      availability: 'gated',
      availabilityLabel: 'No accepted artifact',
      statusLabel: 'Gated',
      gate: 'No current accepted public Linux artifact exists. Physical desktop, package, signing, and release gates remain open.',
    },
  },
  variantOrder: [
    'windows-x86_64-zip',
    'macos-arm64-zip',
    'macos-x86_64-zip',
    'linux-x86_64-tar-gz',
    'linux-x86_64-zip',
  ],
  windows: windowsVariant,
  gatedVariants,
});

function validatePublication(authority, accepted) {
  requireRecordKeys(authority.publication, Object.keys(accepted.publication), 'publication');
  for (const [field, expected] of Object.entries(accepted.publication)) {
    if (expected !== null) requireString(authority.publication[field], `publication.${field}`);
    requireExact(authority.publication[field], expected, `publication.${field}`);
  }

  requireRecordKeys(authority.releaseNotes, Object.keys(accepted.releaseNotes), 'releaseNotes');
  for (const [field, expected] of Object.entries(accepted.releaseNotes)) {
    if (expected !== null) requireString(authority.releaseNotes[field], `releaseNotes.${field}`);
    requireExact(authority.releaseNotes[field], expected, `releaseNotes.${field}`);
  }

  requireRecordKeys(authority.history, [...Object.keys(accepted.history), 'acceptedVersions'], 'history');
  for (const [field, expected] of Object.entries(accepted.history)) {
    requireString(authority.history[field], `history.${field}`);
    requireExact(authority.history[field], expected, `history.${field}`);
  }
  if (!Array.isArray(authority.history.acceptedVersions)) fail('history.acceptedVersions must be an array');
  if (authority.history.acceptedVersions.length !== 0) {
    fail('history.acceptedVersions must stay empty until a previous accepted download is maintained');
  }
}

function validateResources(authority, accepted) {
  requireRecordKeys(authority.unavailableFacts, Object.keys(accepted.unavailableFacts), 'unavailableFacts');
  for (const [field, expected] of Object.entries(accepted.unavailableFacts)) {
    requireString(authority.unavailableFacts[field], `unavailableFacts.${field}`);
    requireExact(authority.unavailableFacts[field], expected, `unavailableFacts.${field}`);
  }

  if (!Array.isArray(authority.resources) || authority.resources.length !== accepted.resources.length) {
    fail(`resources must contain exactly ${accepted.resources.length} destinations`);
  }
  authority.resources.forEach((resource, index) => {
    const expected = accepted.resources[index];
    requireRecordKeys(resource, Object.keys(expected), `resources[${index}]`);
    requireString(resource.id, `resources[${index}].id`);
    requireString(resource.label, `resources[${index}].label`);
    if ('href' in expected) requireSafeHref(resource.href, `resources[${index}].href`);
    if ('availability' in expected) requireString(resource.availability, `resources[${index}].availability`);
    if ('statusLabel' in expected) requireString(resource.statusLabel, `resources[${index}].statusLabel`);
    for (const [field, expectedValue] of Object.entries(expected)) {
      requireExact(resource[field], expectedValue, `resources[${index}].${field}`);
    }
  });

  requireRecordKeys(authority.integrityGuide, ['title', 'summary', 'steps'], 'integrityGuide');
  requireString(authority.integrityGuide.title, 'integrityGuide.title');
  requireString(authority.integrityGuide.summary, 'integrityGuide.summary');
  requireExact(authority.integrityGuide.title, accepted.integrityGuide.title, 'integrityGuide.title');
  requireExact(authority.integrityGuide.summary, accepted.integrityGuide.summary, 'integrityGuide.summary');
  authority.integrityGuide.steps?.forEach((step, index) => requireString(step, `integrityGuide.steps[${index}]`));
  requireArrayExact(authority.integrityGuide.steps, accepted.integrityGuide.steps, 'integrityGuide.steps');
}

function validateRecommendations(authority, accepted) {
  requireRecordKeys(authority.recommendations, Object.keys(accepted.recommendations), 'recommendations');
  for (const [id, expected] of Object.entries(accepted.recommendations)) {
    const recommendation = authority.recommendations[id];
    requireRecordKeys(recommendation, ['label', 'href', 'download'], `recommendations.${id}`);
    requireString(recommendation.label, `recommendations.${id}.label`);
    requireSafeHref(recommendation.href, `recommendations.${id}.href`);
    if (typeof recommendation.download !== 'boolean') {
      fail(`recommendations.${id}.download must be boolean`);
    }
    requireExact(recommendation.label, expected.label, `recommendations.${id}.label`);
    requireExact(recommendation.href, expected.href, `recommendations.${id}.href`);
    requireExact(recommendation.download, expected.download, `recommendations.${id}.download`);
  }
  if (
    authority.recommendations.mobile.label !== authority.recommendations.unknown.label ||
    authority.recommendations.mobile.href !== authority.recommendations.unknown.href ||
    authority.recommendations.mobile.download !== false ||
    authority.recommendations.unknown.download !== false
  ) {
    fail('mobile and unknown visitors must receive the same neutral desktop-download choice');
  }
}

function validateCandidateVariant(platform, variant, releaseVersion, evidence, accepted) {
  requireString(variant.architecture, `${variant.id}.architecture`);
  requireString(variant.format, `${variant.id}.format`);
  requireString(variant.artifactLocation, `${variant.id}.artifactLocation`);
  requireSafeHref(variant.artifactLocation, `${variant.id}.artifactLocation`);
  if (!Number.isSafeInteger(variant.sizeBytes) || variant.sizeBytes <= 0) {
    fail(`${variant.id}.sizeBytes must be a positive integer`);
  }
  if (!Array.isArray(variant.requirements) || variant.requirements.length === 0) {
    fail(`${variant.id}.requirements must be a non-empty array`);
  }
  variant.requirements.forEach((requirement, index) =>
    requireString(requirement, `${variant.id}.requirements[${index}]`),
  );
  requireArrayExact(variant.requirements, accepted.windows.requirements, `${variant.id}.requirements`);

  requireRecordKeys(
    variant.integrity,
    ['algorithm', 'value', 'checksumStatus', 'publisherSignature'],
    `${variant.id}.integrity`,
  );
  requireExact(variant.integrity.algorithm, 'SHA-256', `${variant.id}.integrity.algorithm`);
  if (!/^[0-9a-f]{64}$/.test(variant.integrity.value ?? '')) {
    fail(`${variant.id}.integrity.value must be a lowercase SHA-256 hash`);
  }
  for (const statusName of ['checksumStatus', 'publisherSignature']) {
    requireRecordKeys(variant.integrity[statusName], ['state', 'label', 'summary'], `${variant.id}.integrity.${statusName}`);
    requireString(variant.integrity[statusName].state, `${variant.id}.integrity.${statusName}.state`);
    requireString(variant.integrity[statusName].label, `${variant.id}.integrity.${statusName}.label`);
    requireString(variant.integrity[statusName].summary, `${variant.id}.integrity.${statusName}.summary`);
  }

  requireRecordKeys(
    variant.provenance,
    ['schema', 'sourceCommit', 'inputClosureDigest', 'memberCount'],
    `${variant.id}.provenance`,
  );
  requireString(variant.provenance.schema, `${variant.id}.provenance.schema`);
  if (!/^[0-9a-f]{40}$/.test(variant.provenance.sourceCommit ?? '')) {
    fail(`${variant.id}.provenance.sourceCommit must be a lowercase full Git commit`);
  }
  if (!/^[0-9a-f]{64}$/.test(variant.provenance.inputClosureDigest ?? '')) {
    fail(`${variant.id}.provenance.inputClosureDigest must be a lowercase SHA-256 digest`);
  }
  if (!Number.isSafeInteger(variant.provenance.memberCount) || variant.provenance.memberCount <= 0) {
    fail(`${variant.id}.provenance.memberCount must be a positive integer`);
  }

  if (platform.id !== 'windows' || variant.id !== accepted.windows.id) {
    fail('the accepted Windows artifact must be the sole candidate');
  }
  for (const field of [
    'availability',
    'availabilityLabel',
    'statusLabel',
    'architecture',
    'format',
    'sizeBytes',
    'artifactLocation',
    'gate',
  ]) {
    requireExact(variant[field], accepted.windows[field], `${variant.id}.${field}`);
  }
  for (const [field, expected] of Object.entries(accepted.windows.integrity)) {
    if (typeof expected === 'object') {
      for (const [statusField, statusExpected] of Object.entries(expected)) {
        requireExact(
          variant.integrity[field][statusField],
          statusExpected,
          `${variant.id}.integrity.${field}.${statusField}`,
        );
      }
    } else {
      requireExact(variant.integrity[field], expected, `${variant.id}.integrity.${field}`);
    }
  }
  for (const [field, expected] of Object.entries(accepted.windows.provenance)) {
    requireExact(variant.provenance[field], expected, `${variant.id}.provenance.${field}`);
  }

  const artifact = evidence.artifactFacts(variant.artifactLocation);
  if (!artifact) fail(`${variant.id} candidate artifact does not exist`);
  if (artifact.sizeBytes !== variant.sizeBytes) {
    fail(`${variant.id} size does not match ${variant.artifactLocation}`);
  }
  if (artifact.sha256 !== variant.integrity.value) {
    fail(`${variant.id} hash does not match ${variant.artifactLocation}`);
  }

  const canonical = evidence.canonicalWindows;
  if (!canonical) fail('canonical Internal/Releases/Mumble.zip is missing');
  requireString(canonical.version, 'canonical Windows packaged version');
  if (canonical.version !== releaseVersion) {
    fail(`packaged version ${canonical.version} does not match website version ${releaseVersion}`);
  }
  if (canonical.sizeBytes !== variant.sizeBytes || canonical.sha256 !== variant.integrity.value) {
    fail('Windows authority does not match canonical Internal/Releases/Mumble.zip');
  }
  if (artifact.sizeBytes !== canonical.sizeBytes || artifact.sha256 !== canonical.sha256) {
    fail('website and canonical Windows artifacts disagree');
  }
  if (!canonical.provenance) fail('canonical Windows provenance is missing');
  const packagedIdentity = {
    schema: canonical.provenance.schema,
    sourceCommit: canonical.provenance.sourceCommit,
    inputClosureDigest: canonical.provenance.inputClosureDigest,
    memberCount: canonical.provenance.memberCount,
  };
  for (const [field, expected] of Object.entries(variant.provenance)) {
    requireExact(packagedIdentity[field], expected, `packaged provenance ${field}`);
  }
  requireExact(canonical.provenance.platform, platform.id, 'packaged provenance platform');
  requireExact(canonical.provenance.architecture, variant.architecture, 'packaged provenance architecture');
  requireExact(canonical.provenance.format, variant.format, 'packaged provenance format');
}

function validateGatedVariant(platform, variant, accepted) {
  const expected = accepted.gatedVariants[variant.id];
  if (!expected || expected.platformId !== platform.id) {
    fail(`${variant.id} is not an accepted ${platform.id} variant`);
  }
  requireExact(variant.availability, 'gated', `${variant.id}.availability`);
  requireExact(variant.availabilityLabel, 'No accepted artifact', `${variant.id}.availabilityLabel`);
  requireExact(variant.statusLabel, 'Gated', `${variant.id}.statusLabel`);
  requireExact(variant.architecture, expected.architecture, `${variant.id}.architecture`);
  requireExact(variant.format, expected.format, `${variant.id}.format`);
  requireExact(variant.gate, expected.gate, `${variant.id}.gate`);
  if (
    variant.sizeBytes !== null ||
    variant.artifactLocation !== null ||
    variant.integrity !== null ||
    variant.provenance !== null
  ) {
    fail(`${variant.id} gated state must not invent artifact facts`);
  }
  if (!Array.isArray(variant.requirements) || variant.requirements.length !== 0) {
    fail(`${variant.id} gated state must not invent package requirements`);
  }
}

export function validateReleaseAuthority(authority, evidence) {
  const accepted = acceptedReleaseContract;
  requireRecordKeys(
    authority,
    [
      'schema',
      'application',
      'version',
      'channel',
      'publication',
      'releaseNotes',
      'history',
      'unavailableFacts',
      'recommendations',
      'resources',
      'integrityGuide',
      'platforms',
    ],
    'release authority',
  );
  requireExact(authority.schema, accepted.schema, 'schema');
  requireString(authority.application, 'application');
  requireString(authority.version, 'version');
  requireExact(authority.application, accepted.application, 'application');
  requireString(evidence?.sourceVersion, 'evidence.sourceVersion');
  if (authority.version !== evidence.sourceVersion) {
    fail(`website version ${authority.version} does not match source version ${evidence.sourceVersion}`);
  }

  requireRecordKeys(authority.channel, Object.keys(accepted.channel), 'channel');
  for (const [field, expected] of Object.entries(accepted.channel)) {
    requireString(authority.channel[field], `channel.${field}`);
    requireExact(authority.channel[field], expected, `channel.${field}`);
  }
  validatePublication(authority, accepted);
  validateResources(authority, accepted);
  validateRecommendations(authority, accepted);

  if (!Array.isArray(authority.platforms) || authority.platforms.length !== accepted.platformOrder.length) {
    fail('platforms must contain exactly Windows, macOS, and Linux');
  }
  const representedPlatforms = new Set();
  const representedVariantIds = new Set();
  const variantTuples = new Set();
  let candidateArtifacts = 0;

  for (const platform of authority.platforms) {
    requireRecordKeys(
      platform,
      ['id', 'label', 'availability', 'availabilityLabel', 'statusLabel', 'gate', 'variants'],
      'platform',
    );
    requireString(platform.id, 'platform.id');
    if (representedPlatforms.has(platform.id)) fail(`duplicate platform ${platform.id}`);
    representedPlatforms.add(platform.id);
    const expectedPlatform = accepted.platforms[platform.id];
    if (!expectedPlatform) fail(`${platform.id} is not an accepted platform`);
    for (const [field, expected] of Object.entries(expectedPlatform)) {
      requireString(platform[field], `${platform.id}.${field}`);
      requireExact(platform[field], expected, `${platform.id}.${field}`);
    }
    if (!Array.isArray(platform.variants) || platform.variants.length === 0) {
      fail(`${platform.id}.variants must be a non-empty array`);
    }

    for (const variant of platform.variants) {
      requireRecordKeys(
        variant,
        [
          'id',
          'availability',
          'availabilityLabel',
          'statusLabel',
          'architecture',
          'format',
          'sizeBytes',
          'requirements',
          'artifactLocation',
          'gate',
          'integrity',
          'provenance',
        ],
        `${platform.id}.variant`,
      );
      requireString(variant.id, `${platform.id}.variant.id`);
      requireString(variant.availability, `${variant.id}.availability`);
      requireString(variant.availabilityLabel, `${variant.id}.availabilityLabel`);
      requireString(variant.statusLabel, `${variant.id}.statusLabel`);
      requireString(variant.architecture, `${variant.id}.architecture`);
      requireString(variant.format, `${variant.id}.format`);
      requireString(variant.gate, `${variant.id}.gate`);
      if (representedVariantIds.has(variant.id)) fail(`duplicate variant identity ${variant.id}`);
      representedVariantIds.add(variant.id);
      const tuple = `${platform.id}:${variant.architecture}:${variant.format}`;
      if (variantTuples.has(tuple)) fail(`duplicate platform/architecture/format variant ${tuple}`);
      variantTuples.add(tuple);

      if (variant.availability === 'candidate') {
        candidateArtifacts += 1;
        validateCandidateVariant(platform, variant, authority.version, evidence, accepted);
      } else if (variant.availability === 'gated') {
        validateGatedVariant(platform, variant, accepted);
      } else {
        fail(`${variant.id}.availability must be candidate or gated`);
      }
    }
  }

  for (const platformId of accepted.platformOrder) {
    if (!representedPlatforms.has(platformId)) fail(`missing ${platformId} platform state`);
  }
  for (const variantId of accepted.variantOrder) {
    if (!representedVariantIds.has(variantId)) fail(`missing ${variantId} variant state`);
  }
  if (representedVariantIds.size !== accepted.variantOrder.length) {
    fail(`release matrix must contain exactly ${accepted.variantOrder.length} accepted variants`);
  }
  if (candidateArtifacts !== 1) fail('exactly one candidate artifact must be represented');
  if (authority.recommendations.windows.href !== accepted.windows.artifactLocation) {
    fail('Windows recommendation must resolve to the accepted candidate artifact');
  }

  return authority;
}
