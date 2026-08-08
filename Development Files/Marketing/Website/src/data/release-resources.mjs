import release from './release.json' with { type: 'json' };

export function projectReleaseResources(resources) {
  if (!Array.isArray(resources)) throw new Error('Release resources must be a list');
  const projected = Object.create(null);
  for (const resource of resources) {
    if (!resource || typeof resource.id !== 'string' || !resource.id.trim()) {
      throw new Error('Every release resource requires an id');
    }
    if (Object.hasOwn(projected, resource.id)) {
      throw new Error(`Release resource id is duplicated: ${resource.id}`);
    }
    projected[resource.id] = resource;
  }
  return Object.freeze(projected);
}

export const releaseResources = projectReleaseResources(release.resources);

export function requireLinkedReleaseResource(id, availability) {
  const resource = releaseResources[id];
  if (!resource || typeof resource.href !== 'string' || !resource.href.trim()) {
    throw new Error(`Release resource ${id} requires a destination`);
  }
  if (availability !== undefined && resource.availability !== availability) {
    throw new Error(`Release resource ${id} must remain ${availability}`);
  }
  return resource;
}
