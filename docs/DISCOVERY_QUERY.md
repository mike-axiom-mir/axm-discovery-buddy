# Discovery capability query

`discovery-buddy query` is a read-only bridge from a saved `axm.discovery-index/v0.1` file to deterministic capability candidates.

It exists so a consumer does not need to reimplement Discovery Buddy's index parsing merely to answer questions such as “which declared provider exposes this exact capability id?”. It does **not** execute, install, select, rank, merge, or promote a discovered capability.

## Usage

```bash
python -m discovery_buddy query /tmp/axm-discovery/public-discovery.json \
  --capability-id axm.example.capability/v1 \
  --provider example.cli
```

Exact filters are available for capability id, provider, consumer, status, and repository. Filters are combined with logical AND. Repository means the public `owner/repo` identity in a public-safe index and the workspace-relative repository path in a local-only index.

Exit codes are intentionally gate-friendly:

- `0`: the input index is valid and at least one candidate matched;
- `1`: the input index is valid and no candidate matched;
- `2`: the input index is invalid or unreadable.

## Admission before query

The query boundary does not trust a JSON file merely because it has the right filename. Before returning candidates it requires:

- exact discovery-index schema and supported visibility;
- a valid 64-character lowercase content digest;
- recomputation of the scanner's canonical `schema + policy + repositories` digest;
- repository identity uniqueness;
- valid normalized capability record shapes;
- exact repository, capability-record, and beacon summary counts;
- a regular non-symlink input path within the bounded file-size limit.

The summary check is separate on purpose: `summary` is outside the scanner's existing `content_sha256` payload, so query admission independently derives it instead of trusting it.

## Output

The result schema is `axm.discovery-query/v0.1`. It carries:

- the source index schema, visibility, and exact `content_sha256`;
- the exact filters that were applied;
- deterministic candidate ordering;
- repository identity plus the normalized capability record and its source line;
- a deterministic `query_sha256` over the complete unsigned query body;
- an explicit `DISCOVERY_EVIDENCE_ONLY` authority block.

That authority block is always false for execution, installation, selection, merge, and CANON authority.

## Truth boundary

A match proves only that a capability declaration matching the exact filters exists in the admitted saved discovery index. It does not prove that the provider still exists on disk, that its runtime works, that the declaration is semantically correct, that the source author is authenticated, or that a consumer should adopt it.

A real consumer should treat the result as a provider candidate and apply its own source/provenance, runtime, policy, and acceptance gates before use.
