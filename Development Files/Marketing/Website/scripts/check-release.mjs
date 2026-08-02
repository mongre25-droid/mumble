import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateReleaseAuthority } from './release-contract.mjs';

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const publicRoot = resolve(websiteDir, 'public');
const repositoryRoot = resolve(websiteDir, '..', '..', '..');
const authority = JSON.parse(readFileSync(resolve(websiteDir, 'src', 'data', 'release.json'), 'utf8'));
const branding = readFileSync(resolve(repositoryRoot, 'Internal', 'app', 'branding.py'), 'utf8');
const sourceVersion = branding.match(/^VERSION\s*=\s*["']([^"']+)["']/m)?.[1];
const canonicalPath = resolve(repositoryRoot, 'Internal', 'Releases', 'Mumble.zip');

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function facts(path) {
  if (!existsSync(path)) return null;
  return { sizeBytes: statSync(path).size, sha256: sha256(path) };
}

function publicArtifactPath(location) {
  const path = resolve(publicRoot, `.${location}`);
  const relativePath = relative(publicRoot, path);
  if (
    relativePath === '' ||
    relativePath === '..' ||
    relativePath.startsWith(`..${sep}`) ||
    isAbsolute(relativePath)
  ) {
    throw new Error(`Artifact location escapes website public root: ${location}`);
  }
  return path;
}

try {
  validateReleaseAuthority(authority, {
    sourceVersion,
    artifactFacts(location) {
      return facts(publicArtifactPath(location));
    },
    canonicalWindows: facts(canonicalPath),
  });
} catch (error) {
  console.error(`Website release authority rejected: ${error.message}`);
  process.exit(1);
}

console.log(
  `Release authority passed: Mumble ${authority.version}, one hash-bound Windows candidate, macOS/Linux gated, publication ${authority.publication.state}.`,
);
