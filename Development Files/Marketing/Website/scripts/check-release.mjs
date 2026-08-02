import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { crc32, inflateRawSync } from 'node:zlib';
import { acceptedReleaseContract, validateReleaseAuthority } from './release-contract.mjs';

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

function readRequiredZipEntries(archive, requiredEntryLimits) {
  const endSignature = 0x06054b50;
  const centralSignature = 0x02014b50;
  const localSignature = 0x04034b50;
  const minimumOffset = Math.max(0, archive.length - 22 - 0xffff);
  let endOffset = -1;
  for (let offset = archive.length - 22; offset >= minimumOffset; offset -= 1) {
    if (archive.readUInt32LE(offset) === endSignature) {
      endOffset = offset;
      break;
    }
  }
  if (endOffset < 0) throw new Error('canonical Windows ZIP has no end-of-central-directory record');

  const diskNumber = archive.readUInt16LE(endOffset + 4);
  const centralDisk = archive.readUInt16LE(endOffset + 6);
  const diskEntryCount = archive.readUInt16LE(endOffset + 8);
  const entryCount = archive.readUInt16LE(endOffset + 10);
  const centralSize = archive.readUInt32LE(endOffset + 12);
  const centralStart = archive.readUInt32LE(endOffset + 16);
  const commentLength = archive.readUInt16LE(endOffset + 20);
  if (diskNumber !== 0 || centralDisk !== 0 || diskEntryCount !== entryCount || entryCount === 0) {
    throw new Error('canonical Windows ZIP must use one non-empty central directory');
  }
  if (endOffset + 22 + commentLength !== archive.length || centralStart + centralSize !== endOffset) {
    throw new Error('canonical Windows ZIP central directory boundary is inconsistent');
  }

  const seenNames = new Set();
  const localRanges = [];
  const requiredEntries = new Map();
  let centralOffset = centralStart;
  for (let index = 0; index < entryCount; index += 1) {
    if (centralOffset + 46 > endOffset || archive.readUInt32LE(centralOffset) !== centralSignature) {
      throw new Error('canonical Windows ZIP central directory is malformed');
    }
    const flags = archive.readUInt16LE(centralOffset + 8);
    const method = archive.readUInt16LE(centralOffset + 10);
    const expectedCrc = archive.readUInt32LE(centralOffset + 16);
    const compressedSize = archive.readUInt32LE(centralOffset + 20);
    const uncompressedSize = archive.readUInt32LE(centralOffset + 24);
    const nameLength = archive.readUInt16LE(centralOffset + 28);
    const extraLength = archive.readUInt16LE(centralOffset + 30);
    const entryCommentLength = archive.readUInt16LE(centralOffset + 32);
    const localOffset = archive.readUInt32LE(centralOffset + 42);
    const nameStart = centralOffset + 46;
    const nextOffset = nameStart + nameLength + extraLength + entryCommentLength;
    if (nameLength === 0 || nextOffset > endOffset) {
      throw new Error('canonical Windows ZIP entry metadata is out of bounds');
    }
    const name = archive.subarray(nameStart, nameStart + nameLength).toString('utf8');
    if (seenNames.has(name)) throw new Error(`canonical Windows ZIP contains duplicate entry ${name}`);
    seenNames.add(name);
    centralOffset = nextOffset;

    if (flags & 1) throw new Error(`${name} must not be encrypted`);
    if (flags & 8) throw new Error(`${name} must not use a data descriptor`);
    if (localOffset + 30 > centralStart || archive.readUInt32LE(localOffset) !== localSignature) {
      throw new Error(`${name} local ZIP header is malformed`);
    }
    const localFlags = archive.readUInt16LE(localOffset + 6);
    const localMethod = archive.readUInt16LE(localOffset + 8);
    const localCrc = archive.readUInt32LE(localOffset + 14);
    const localCompressedSize = archive.readUInt32LE(localOffset + 18);
    const localUncompressedSize = archive.readUInt32LE(localOffset + 22);
    const localNameLength = archive.readUInt16LE(localOffset + 26);
    const localExtraLength = archive.readUInt16LE(localOffset + 28);
    const localNameStart = localOffset + 30;
    const dataStart = localNameStart + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > centralStart) throw new Error(`${name} ZIP payload is out of bounds`);
    const localName = archive.subarray(localNameStart, localNameStart + localNameLength).toString('utf8');
    if (
      localName !== name ||
      localFlags !== flags ||
      localMethod !== method ||
      localCrc !== expectedCrc ||
      localCompressedSize !== compressedSize ||
      localUncompressedSize !== uncompressedSize
    ) {
      throw new Error(`${name} local and central ZIP metadata disagree`);
    }
    localRanges.push({ start: localOffset, end: dataEnd, name });

    const maxOutputLength = requiredEntryLimits.get(name);
    if (maxOutputLength === undefined) continue;
    if (uncompressedSize > maxOutputLength) {
      throw new Error(`${name} exceeds its ${maxOutputLength}-byte verification limit`);
    }
    const compressed = archive.subarray(dataStart, dataEnd);
    const bytes = method === 0
      ? compressed
      : method === 8
        ? inflateRawSync(compressed, { maxOutputLength })
        : null;
    if (!bytes) throw new Error(`${name} uses unsupported ZIP compression method ${method}`);
    if (bytes.length !== uncompressedSize) throw new Error(`${name} ZIP size is inconsistent`);
    if ((crc32(bytes) >>> 0) !== expectedCrc) throw new Error(`${name} ZIP CRC is inconsistent`);
    requiredEntries.set(name, bytes);
  }
  if (centralOffset !== endOffset) throw new Error('canonical Windows ZIP central directory size is inconsistent');

  localRanges.sort((left, right) => left.start - right.start);
  for (let index = 1; index < localRanges.length; index += 1) {
    if (localRanges[index].start < localRanges[index - 1].end) {
      throw new Error(
        `canonical Windows ZIP entries ${localRanges[index - 1].name} and ${localRanges[index].name} overlap`,
      );
    }
  }
  for (const entryName of requiredEntryLimits.keys()) {
    if (!requiredEntries.has(entryName)) throw new Error(`${entryName} is missing from canonical Windows ZIP`);
  }
  return { requiredEntries, entryCount };
}

function archiveFacts(path, expected) {
  if (!existsSync(path)) return null;
  const archive = readFileSync(path);
  const artifact = {
    sizeBytes: archive.length,
    sha256: createHash('sha256').update(archive).digest('hex'),
  };
  if (artifact.sizeBytes !== expected.sizeBytes || artifact.sha256 !== expected.integrity.value) {
    throw new Error('canonical Windows ZIP bytes do not match the accepted release contract');
  }
  const provenanceName = 'Mumble/RELEASE-PROVENANCE.json';
  const brandingName = 'Mumble/Internal/app/branding.py';
  const { requiredEntries, entryCount } = readRequiredZipEntries(
    archive,
    new Map([
      [provenanceName, 1_048_576],
      [brandingName, 65_536],
    ]),
  );
  const provenance = JSON.parse(requiredEntries.get(provenanceName).toString('utf8'));
  const packagedVersion = requiredEntries
    .get(brandingName)
    .toString('utf8')
    .match(/^VERSION\s*=\s*["']([^"']+)["']/m)?.[1];
  if (!packagedVersion) throw new Error(`${brandingName} has no VERSION identity`);
  return {
    ...artifact,
    version: packagedVersion,
    provenance: {
      schema: provenance.schema,
      sourceCommit: provenance.source?.commit,
      inputClosureDigest: provenance.input_closure?.digest,
      memberCount: entryCount,
      platform: provenance.package?.platform,
      architecture: provenance.package?.architecture,
      format: provenance.package?.format?.toUpperCase(),
    },
  };
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
    canonicalWindows: archiveFacts(canonicalPath, acceptedReleaseContract.windows),
  });
} catch (error) {
  console.error(`Website release authority rejected: ${error.message}`);
  process.exit(1);
}

console.log(
  `Release authority passed: Mumble ${authority.version}, one hash-bound Windows candidate, macOS/Linux gated, publication ${authority.publication.state}.`,
);
