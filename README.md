# AXM Discovery Buddy

[![Apache License 2.0](https://img.shields.io/badge/license-Apache--2.0-3b82f6)](LICENSE) ![Status staged](https://img.shields.io/badge/status-staged-f59e0b) ![Local first](https://img.shields.io/badge/local--first-direction-16a085)

AXM Discovery Buddy is the public staging home for a local-first repository and workspace discovery tool: bounded scanning, evidence-backed indexing, resumable work, source-integrity receipts, and a human-readable map of large project collections.

## Current truth

- The default branch currently contains a repository shell, not a materialized scanner source tree.
- No working scan, indexing, checkpoint, performance, or scale claim is made from `main`.
- [PR #2](https://github.com/mike-axiom-mir/axm-discovery-buddy/pull/2) adds the one-chat/one-PR governance rule; it does not by itself materialize the scanner.
- Related deterministic intake and external-source verification work is visible in [AXM Parallel Capability Fabric](https://github.com/mike-axiom-mir/axm-parallel-capability), but that work does not silently promote Discovery Buddy into a finished capability.

## Intended public direction

A future materialized build should make it easier to answer:

- What projects, files, branches, and artifacts exist?
- Which state is canonical, experimental, held, duplicated, or missing?
- What changed, what evidence supports it, and what still needs review?
- How can a large local workspace be mapped without uploading its private contents?

Private paths, secrets, raw memory, personal data, and unpublished continuity must remain outside public exports.

## Project status

**STAGED / NOT YET MATERIALIZED ON MAIN**

The next legitimate milestone is a source tree with reproducible tests and explicit evidence—not a stronger README claim.

Explore the wider family in the [AXM Public Project Map](https://github.com/mike-axiom-mir/axm-collaboration-platform/blob/main/docs/PUBLIC_PROJECTS.md).
