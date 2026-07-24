import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const rootZip = resolve(websiteDir, '..', '..', '..', 'Internal', 'Releases', 'Mumble.zip');
const publicZip = resolve(websiteDir, 'public', 'Mumble.zip');

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

if (sha256(rootZip) !== sha256(publicZip)) {
  console.error(
    'Website release mismatch: public/Mumble.zip is not the validated Internal/Releases/Mumble.zip. ' +
    'Run Development Files/Tooling/_rebuild_zip.py before publishing.',
  );
  process.exit(1);
}

console.log('Release artifact check passed: website ZIP matches Internal/Releases/Mumble.zip.');
