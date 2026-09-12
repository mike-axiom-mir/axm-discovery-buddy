# Portable Discovery Buddy zipapp

## Purpose

Discovery Buddy's scanner and capability-query boundary are useful outside this repository. This adapter makes the exact dependency-free Python package runnable as one local file without copying its implementation into a consumer repository and without requiring a Python package registry.

Build from a reviewed source checkout:

```bash
python tools/build_portable_discovery.py build \
  --output dist/discovery-buddy.pyz \
  --receipt dist/discovery-buddy.pyz.receipt.json
```

Verify that both files still match the current `discovery_buddy/**/*.py` source exactly:

```bash
python tools/build_portable_discovery.py verify \
  --output dist/discovery-buddy.pyz \
  --receipt dist/discovery-buddy.pyz.receipt.json
```

Then move or copy the two generated files wherever the local consumer needs them and invoke the archive with Python 3.11+:

```bash
python /path/to/discovery-buddy.pyz scan /path/to/workspace --output-dir /tmp/axm-discovery
python /path/to/discovery-buddy.pyz query /tmp/axm-discovery/local-discovery.json \
  --capability-id axm.example.capability/v1
```

No install step, account, package registry, AI model, network service, or third-party Python package is required.

## Deterministic package contract

`tools/build_portable_discovery.py` packages only Python source under `discovery_buddy/` plus a generated root `__main__.py`.

The archive uses:

- stable lexical entry ordering;
- fixed ZIP timestamps and file modes;
- stored, uncompressed entries so zlib-version differences cannot change package bytes;
- exact source-byte SHA-256 evidence;
- a sidecar `axm.discovery-portable-zipapp/v0.1` receipt bound to the final archive SHA-256;
- fail-closed rejection of symlinked package members;
- exact rebuild verification instead of trusting the sidecar receipt by itself.

Tests execute the generated archive from a directory outside the repository with `PYTHONPATH` removed, then run the real `scan -> verify -> query` path. That is the portability claim: the consumer needs the zipapp, Python, and the paths it explicitly chooses—not a Discovery Buddy checkout.

## Authority and provenance boundary

The generated receipt is deterministic integrity evidence. It is not a signature and does not authenticate who produced the archive.

Running the zipapp gives the same bounded scanner/query behavior as the packaged source. It does **not** grant a discovered capability execution, installation, automatic selection, merge, promotion, or CANON authority. A query hit remains `DISCOVERY_EVIDENCE_ONLY`; a consumer must still perform its own source, policy, provenance, compatibility, and runtime checks before using a provider.

The builder does not publish to PyPI, create a release, upload an artifact, mutate a scanned repository, or install another AXM repository. Generated files are ordinary local build outputs and are not canonical source state.
