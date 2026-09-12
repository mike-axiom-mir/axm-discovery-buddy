export const INDEX_SCHEMA = 'axm.discovery-index/v0.1';

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asString(value) {
  return typeof value === 'string' ? value : '';
}

function uniqStrings(values) {
  return [...new Set(asArray(values).filter((value) => typeof value === 'string' && value.trim()).map((value) => value.trim()))].sort();
}

function capabilityRecord(record) {
  return {
    id: asString(record?.id),
    status: asString(record?.status),
    providers: uniqStrings(record?.providers),
    consumers: uniqStrings(record?.consumers),
    source: asString(record?.source),
    line: Number.isInteger(record?.line) ? record.line : null,
  };
}

function normalizeRepo(repo, visibility, index) {
  const isPublic = visibility === 'PUBLIC_SAFE_DECLARED_ONLY';
  const capabilities = asArray(repo?.capabilities?.records)
    .map(capabilityRecord)
    .filter((record) => record.id);
  const beacon = repo?.beacon && typeof repo.beacon === 'object' ? repo.beacon : {};
  const git = repo?.git && typeof repo.git === 'object' ? repo.git : {};
  const title = isPublic
    ? asString(repo?.display_name) || asString(repo?.repo) || 'Unnamed repository'
    : asString(repo?.name) || asString(repo?.path) || 'Unnamed repository';
  const locator = isPublic ? asString(repo?.repo) : asString(repo?.path);
  const identity = `${isPublic ? 'public' : 'local'}:${locator || title}:${index}`;
  const readmePresent = isPublic ? null : repo?.readme?.present === true;
  const agentsPresent = isPublic ? null : repo?.agents?.present === true;
  const publicMarkerEligible = isPublic ? true : repo?.public_marker?.eligible === true;
  const attention = !isPublic && (!readmePresent || !agentsPresent || !git?.head);
  const searchText = [
    title,
    locator,
    asString(git?.branch),
    ...uniqStrings(beacon?.tags),
    ...uniqStrings(beacon?.interests),
    ...capabilities.flatMap((record) => [record.id, record.status, ...record.providers, ...record.consumers]),
  ].join(' ').toLowerCase();

  return {
    identity,
    title,
    locator,
    isPublic,
    branch: asString(git?.branch),
    head: asString(git?.head),
    detached: git?.detached === true,
    readmePresent,
    agentsPresent,
    publicMarkerEligible,
    beaconPresent: beacon?.present === true,
    beaconProtocol: asString(beacon?.protocol),
    beaconRepo: asString(beacon?.repo),
    beaconTags: uniqStrings(beacon?.tags),
    beaconInterests: uniqStrings(beacon?.interests),
    capabilities,
    attention,
    searchText,
  };
}

export function parseDiscoveryIndex(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Index must be one JSON object.');
  }
  if (input.schema !== INDEX_SCHEMA) {
    throw new Error(`Unsupported schema: ${asString(input.schema) || 'missing'}. Expected ${INDEX_SCHEMA}.`);
  }
  const visibility = asString(input.visibility);
  if (!['LOCAL_ONLY', 'PUBLIC_SAFE_DECLARED_ONLY'].includes(visibility)) {
    throw new Error(`Unsupported visibility: ${visibility || 'missing'}.`);
  }
  if (!Array.isArray(input.repositories)) {
    throw new Error('Index repositories must be an array.');
  }
  const repositories = input.repositories.map((repo, index) => normalizeRepo(repo, visibility, index));
  const declaredSummary = input.summary && typeof input.summary === 'object' ? input.summary : {};
  const derivedSummary = {
    repositories: repositories.length,
    capability_records: repositories.reduce((sum, repo) => sum + repo.capabilities.length, 0),
    beacons: repositories.filter((repo) => repo.beaconPresent).length,
  };

  return {
    schema: INDEX_SCHEMA,
    visibility,
    contentSha256: asString(input.content_sha256),
    policy: input.policy && typeof input.policy === 'object' ? input.policy : {},
    repositories,
    declaredSummary: {
      repositories: Number.isInteger(declaredSummary.repositories) ? declaredSummary.repositories : null,
      capability_records: Number.isInteger(declaredSummary.capability_records) ? declaredSummary.capability_records : null,
      beacons: Number.isInteger(declaredSummary.beacons) ? declaredSummary.beacons : null,
    },
    derivedSummary,
  };
}

export function filterRepositories(index, query = '', filter = 'all') {
  const needle = query.trim().toLowerCase();
  return index.repositories.filter((repo) => {
    if (needle && !repo.searchText.includes(needle)) return false;
    if (filter === 'capabilities' && repo.capabilities.length === 0) return false;
    if (filter === 'beacons' && !repo.beaconPresent) return false;
    if (filter === 'attention' && !repo.attention) return false;
    return true;
  });
}

export function summarizeIndex(index) {
  const derived = index.derivedSummary;
  const declared = index.declaredSummary;
  const mismatches = [];
  for (const key of ['repositories', 'capability_records', 'beacons']) {
    if (declared[key] !== null && declared[key] !== derived[key]) {
      mismatches.push({ field: key, declared: declared[key], derived: derived[key] });
    }
  }
  return {
    ...derived,
    mismatchCount: mismatches.length,
    mismatches,
  };
}
