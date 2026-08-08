const expectedJobIds = ['write', 'capture', 'shape', 'listen', 'find'];

function requireRecord(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${path} must be an object`);
  }
  return value;
}

function requireText(value, path) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new TypeError(`${path} must be non-empty text`);
  }
  return value;
}

function requireDimension(value, path) {
  if (!Number.isInteger(value) || value <= 0) {
    throw new TypeError(`${path} must be a positive integer`);
  }
  return value;
}

function resolveMedia(jobs, job, montage) {
  const reference = montage.media;
  requireRecord(reference, `${job.id}.homeMontage.media`);
  let media;
  if (reference.kind === 'demonstration-presentation') {
    media = job.story?.demonstration?.presentation;
  } else if (reference.kind === 'demonstration-state') {
    media = job.story?.demonstration?.states?.find((state) => state.id === reference.stateId);
  } else if (reference.kind === 'job-demonstration-presentation') {
    media = jobs.find((candidate) => candidate.id === reference.jobId)?.story?.demonstration?.presentation;
  } else if (reference.kind === 'presentation-capture') {
    media = job.story?.presentation?.captures?.find((capture) => capture.route === reference.route);
  } else {
    throw new TypeError(`${job.id}.homeMontage.media.kind is unsupported`);
  }

  requireRecord(media, `${job.id}.homeMontage.resolvedMedia`);
  return {
    src: requireText(media.src ?? media.image, `${job.id}.homeMontage.resolvedMedia.src`),
    width: requireDimension(media.width, `${job.id}.homeMontage.resolvedMedia.width`),
    height: requireDimension(media.height, `${job.id}.homeMontage.resolvedMedia.height`),
    alt: requireText(montage.mediaAlt ?? media.alt, `${job.id}.homeMontage.resolvedMedia.alt`),
  };
}

export function projectHomeMontage(jobs) {
  if (!Array.isArray(jobs) || jobs.map((job) => job.id).join(',') !== expectedJobIds.join(',')) {
    throw new TypeError('Home montage requires canonical Write, Capture, Shape, Listen, and Find order');
  }

  return jobs.map((job) => {
    const montage = requireRecord(job.homeMontage, `${job.id}.homeMontage`);
    const route = montage.route === undefined
      ? undefined
      : montage.route.map((item, index) => requireText(item, `${job.id}.homeMontage.route[${index}]`));
    if (route && route.length === 0) {
      throw new TypeError(`${job.id}.homeMontage.route must not be empty`);
    }

    return {
      id: requireText(job.id, 'job.id'),
      label: requireText(job.label, `${job.id}.label`),
      action: requireText(montage.action, `${job.id}.homeMontage.action`),
      title: requireText(montage.title, `${job.id}.homeMontage.title`),
      body: requireText(montage.body, `${job.id}.homeMontage.body`),
      evidence: requireText(montage.evidence, `${job.id}.homeMontage.evidence`),
      media: resolveMedia(jobs, job, montage),
      routeLabel: route
        ? requireText(montage.routeLabel, `${job.id}.homeMontage.routeLabel`)
        : undefined,
      route,
    };
  });
}
