import { readFile } from 'node:fs/promises';

function required(value, message) {
  if (value === undefined || value === null || value === '') throw new Error(message);
  return value;
}

function routeEntries(entries, label) {
  if (!Array.isArray(entries)) throw new Error(`${label} must be an array`);
  const byRoute = Object.fromEntries(entries.map((entry) => [entry.route, entry]));
  for (const route of ['deck', 'local', 'web']) required(byRoute[route], `${label} is missing ${route}`);
  return byRoute;
}

function sourceEntries(sources, label) {
  if (!Array.isArray(sources)) throw new Error(`${label} must be an array`);
  return Object.fromEntries(sources.map((source) => [
    required(source.role, `${label} source role is missing`),
    required(source.path, `${label} source path is missing`),
  ]));
}

export async function readJobs(jobsPath) {
  return JSON.parse(await readFile(jobsPath, 'utf8'));
}

export function createFindCapturePlan(jobs) {
  const find = jobs.find((job) => job.id === 'find');
  const presentation = required(find?.story?.presentation, 'canonical Find presentation is missing');
  const captures = routeEntries(presentation.captures, 'Find captures');
  const boundaries = routeEntries(presentation.boundaries, 'Find boundaries');

  const localFixture = required(captures.local.fixture, 'local capture fixture is missing');
  const localRows = localFixture.rows.map((row) => ({
    ...row,
    name: row.exampleRef === 'local' ? boundaries.local.example.title : required(row.name, `${row.id} name is missing`),
  }));
  const provider = required(
    boundaries.web.facts.find((fact) => fact.id === captures.web.fixture.providerFactId)?.detail,
    'Web Search capture provider fact is missing',
  );
  const webPrivacy = captures.web.fixture.privacy.replaceAll('{provider}', provider);

  return {
    schemaVersion: 1,
    captures: [
      {
        route: 'deck',
        output: {
          path: `public${captures.deck.src}`,
          width: captures.deck.width,
          height: captures.deck.height,
        },
        provenance: captures.deck.provenance,
        conversion: captures.deck.provenance.conversion,
      },
      {
        route: 'local',
        output: {
          path: `public${captures.local.src}`,
          width: captures.local.width,
          height: captures.local.height,
        },
        provenance: captures.local.provenance,
        sources: sourceEntries(captures.local.provenance.sources, 'local capture'),
        semantic: {
          example: boundaries.local.example,
          actions: boundaries.local.actions,
        },
        fixture: {
          query: localFixture.query,
          rows: localRows,
        },
      },
      {
        route: 'web',
        output: {
          path: `public${captures.web.src}`,
          width: captures.web.width,
          height: captures.web.height,
        },
        provenance: captures.web.provenance,
        sources: sourceEntries(captures.web.provenance.sources, 'web capture'),
        semantic: {
          example: boundaries.web.example,
          facts: boundaries.web.facts,
          actions: boundaries.web.actions,
          failure: boundaries.web.failure,
        },
        fixture: {
          requestId: captures.web.fixture.requestId,
          provider,
          query: boundaries.web.example.quote,
          privacy: webPrivacy,
          dialogName: `Search online with ${provider}?`,
        },
      },
    ],
  };
}
