import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import release from '../src/data/release.json' with { type: 'json' };

const websiteRoot = resolve(import.meta.dirname, '..');

function resourceById(resourceData, id) {
  const resource = resourceData.resources.find((candidate) => candidate.id === id);
  assert.ok(resource && 'href' in resource, `release resource ${id} must have a destination`);
  return resource;
}

function renderedLinkHrefs(html, text) {
  return [...html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)]
    .filter((match) => {
      const renderedText = match[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      return renderedText === text || renderedText.startsWith(`${text} `);
    })
    .map((match) => match[1].match(/\bhref="([^"]+)"/)?.[1]);
}

const temporaryRoot = await mkdtemp(join(websiteRoot, '.release-resource-authority-'));
try {
  for (const entry of ['public', 'src']) {
    await cp(resolve(websiteRoot, entry), resolve(temporaryRoot, entry), { recursive: true });
  }
  for (const entry of ['astro.config.mjs', 'package.json', 'tsconfig.json']) {
    await cp(resolve(websiteRoot, entry), resolve(temporaryRoot, entry));
  }

  const changedRelease = structuredClone(release);
  const changedSupport = resourceById(changedRelease, 'support');
  const changedLicence = resourceById(changedRelease, 'licence');
  changedSupport.href = 'https://github.com/mongre25-droid/mumble/issues?authority=mutated';
  changedLicence.href = 'https://github.com/mongre25-droid/mumble/blob/main/LICENSE?authority=mutated';
  await writeFile(
    resolve(temporaryRoot, 'src/data/release.json'),
    `${JSON.stringify(changedRelease, null, 2)}\n`,
    'utf8',
  );

  execFileSync(
    process.execPath,
    [resolve(websiteRoot, 'node_modules/astro/bin/astro.mjs'), 'build', '--root', temporaryRoot],
    { cwd: websiteRoot, encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 },
  );
  const homeHtml = await readFile(resolve(temporaryRoot, 'dist/index.html'), 'utf8');
  const helpHtml = await readFile(resolve(temporaryRoot, 'dist/help/index.html'), 'utf8');

  assert.deepEqual(
    renderedLinkHrefs(homeHtml, 'Get Help in the issue tracker'),
    [changedSupport.href],
    'Home support action must render the mutated canonical support destination',
  );
  assert.deepEqual(
    renderedLinkHrefs(homeHtml, 'MIT licence'),
    [changedLicence.href],
    'the shared footer must render the mutated canonical licence destination',
  );
  assert.deepEqual(
    renderedLinkHrefs(helpHtml, 'MIT licence'),
    [changedLicence.href, changedLicence.href],
    'Help and its shared footer must render the mutated canonical licence destination',
  );
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

console.log('RELEASE_RESOURCE_AUTHORITY_OK');
