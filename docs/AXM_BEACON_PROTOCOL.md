# AXM Organ Beacon Protocol v0.1

## Purpose

AXM repositories remain independent bodies. The beacon lets one repository expose a compact, deterministic evidence capsule when a change may be worth inspecting elsewhere.

This is **not** a dependency manager, shared runtime, automatic canon system, or cross-repo rewrite engine.

## Core boundary

A beacon may:

- inspect a Git diff;
- package changed paths, content hashes, symbols, knowledge headings, test-change/doc/protocol signals, and the exact allowed patch;
- publish that package to a generated `axm-beacon-feed` branch;
- let another repository rank the package against its own declared interests;
- fetch the capsule and patch into a proposal-only inbox.

A beacon may **not**:

- claim that attention/relevance means correctness, quality, or truth;
- apply the patch to a receiving repository;
- change another repository's canonical state;
- turn a discovery into AXM canon automatically;
- hide the source repository, source commit, file hashes, or patch hash;
- leak paths excluded by the publishing repository's beacon policy.

The receiving repository must inspect, test, adapt, reject, or independently reimplement the idea under its own acceptance gates.

## Why the feed is a separate branch

`axm-beacon-feed` is generated transport state. It keeps machine-generated capsule history out of the repository's canonical source branch while remaining readable through ordinary Git/GitHub. The branch can be regenerated from source history.

Each repository carries its own copy of the beacon implementation and config. No repository imports executable code from `axm-discovery-buddy` or another peer at runtime.

## Capsule identity

A capsule ID is the SHA-256 of canonical JSON containing:

- protocol version;
- source repo + base/head Git SHAs + commit subject;
- per-file change metadata and content SHA-256;
- patch SHA-256;
- deterministic signals and tags;
- transfer policy.

Timestamps are intentionally excluded from identity. Running the publisher twice over the same source state produces the same capsule ID.

## Attention score

The beacon computes a small transparent **attention score** from evidence such as changed code, changed tests, documentation, symbols, organ/capability paths, and protocols/schemas.

It is only a prioritization signal. It is deliberately named *attention*, not *quality*, *truth*, or *confidence*. A changed test is recorded as a test change; it is not treated as proof that tests passed.

## Commands

Publish a source change locally:

```bash
python .axm/beacon/beacon.py publish --base HEAD^ --head HEAD --output-dir /tmp/feed
```

Scan configured public AXM peer feeds on GitHub:

```bash
python .axm/beacon/beacon.py scan --github --limit 20
```

Fetch a candidate without applying it:

```bash
python .axm/beacon/beacon.py fetch \
  --repo mike-axiom-mir/axm-state-research \
  --capsule-id <sha256>
```

Verify a fetched capsule:

```bash
python .axm/beacon/beacon.py verify .axm/beacon/inbox/<sha256>/capsule.json
```

## Growth path

v0.1 proves the transport and truth boundary. Later versions can add optional analyzers that produce richer semantic explanations, compatibility tests, or adaptation proposals, but those outputs must remain claims/evidence and never silently become authority.
