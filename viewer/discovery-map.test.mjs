import test from 'node:test';
import assert from 'node:assert/strict';
import { filterRepositories, parseDiscoveryIndex, summarizeIndex } from './discovery-map.mjs';

const LOCAL = {
  schema: 'axm.discovery-index/v0.1',
  visibility: 'LOCAL_ONLY',
  content_sha256: 'a'.repeat(64),
  summary: { repositories: 2, capability_records: 2, beacons: 1 },
  repositories: [
    {
      path: 'alpha', name: 'alpha',
      git: { branch: 'main', head: '1'.repeat(40), detached: false },
      readme: { present: true }, agents: { present: true },
      public_marker: { eligible: true },
      beacon: { present: true, protocol: 'axm-beacon/0.1', tags: ['game'], interests: ['state protocol'] },
      capabilities: { records: [
        { id: 'state:snapshot', status: 'IMPLEMENTED', providers: ['alpha'], consumers: ['viewer'], source: 'registry/capabilities.jsonl', line: 1 },
        { id: 'game:launch', providers: ['alpha'], consumers: [], source: 'registry/capabilities.jsonl', line: 2 },
      ] },
    },
    {
      path: 'beta', name: 'beta',
      git: { branch: 'dev', head: null, detached: false },
      readme: { present: true }, agents: { present: false },
      public_marker: { eligible: false },
      beacon: { present: false },
      capabilities: { records: [] },
    },
  ],
};

test('normalizes local discovery evidence without granting authority', () => {
  const index = parseDiscoveryIndex(LOCAL);
  assert.equal(index.repositories.length, 2);
  assert.equal(index.repositories[0].capabilities[0].id, 'state:snapshot');
  assert.equal(index.repositories[1].attention, true);
  assert.deepEqual(summarizeIndex(index).mismatches, []);
});

test('filters by human search terms and bounded evidence views', () => {
  const index = parseDiscoveryIndex(LOCAL);
  assert.deepEqual(filterRepositories(index, 'snapshot').map((repo) => repo.title), ['alpha']);
  assert.deepEqual(filterRepositories(index, '', 'beacons').map((repo) => repo.title), ['alpha']);
  assert.deepEqual(filterRepositories(index, '', 'attention').map((repo) => repo.title), ['beta']);
});

test('public-safe index never invents stripped local state', () => {
  const index = parseDiscoveryIndex({
    schema: 'axm.discovery-index/v0.1',
    visibility: 'PUBLIC_SAFE_DECLARED_ONLY',
    summary: { repositories: 1, capability_records: 0, beacons: 0 },
    repositories: [{ repo: 'example/public', display_name: 'Public', beacon: { present: false }, capabilities: { records: [] } }],
  });
  assert.equal(index.repositories[0].locator, 'example/public');
  assert.equal(index.repositories[0].branch, '');
  assert.equal(index.repositories[0].readmePresent, null);
  assert.equal(index.repositories[0].attention, false);
});

test('fails closed on unsupported index identity', () => {
  assert.throws(() => parseDiscoveryIndex({ schema: 'future', visibility: 'LOCAL_ONLY', repositories: [] }), /Unsupported schema/);
  assert.throws(() => parseDiscoveryIndex({ schema: 'axm.discovery-index\/v0.1', visibility: 'UNKNOWN', repositories: [] }), /Unsupported visibility/);
});
