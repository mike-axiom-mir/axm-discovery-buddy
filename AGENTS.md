# AGENTS.md

## AXM lane discipline

**Hard rule: one chat = one lane = one PR.**

For any chat / AI instance that is actively changing this repository:

- Claim exactly one branch / PR lane for that chat before implementation work.
- Keep all commits, fixes, tests, follow-up polish, and review repairs from that chat inside the same lane.
- Do not spread one chat across multiple branches or pull requests.
- Do not silently open a second PR because the current lane becomes inconvenient, blocked, large, or needs repair.
- Do not write implementation changes directly to `main` when a chat lane is active.
- If the chat already has an open lane, continue that lane instead of claiming another one.
- If work must be handed to another chat, leave a clear handoff in the existing PR / branch context. The receiving chat claims its own lane rather than sharing execution authority silently.
- A lane is released when its PR is merged, closed, or explicitly abandoned. Only then may that chat claim a new PR lane.

A lane may contain many commits. The rule limits **parallel PR spread**, not useful iteration inside the claimed PR.

## Truth and evidence

- Keep observation, measurement, interpretation, hypothesis, proposal, and proof distinct.
- Preserve failures, contradictions, limitations, and unresolved seams.
- Do not call a capability verified merely because code exists or a test name sounds authoritative.
- Prefer deterministic checks and receipts where the claim is mechanically testable.

## Authority boundaries

- Reading or analysing software does not grant mutation, execution, merge, deployment, learning-promotion, or permission authority.
- Experimental work should prefer disposable branches / clones, reversible diffs, explicit receipts, and rollback.
- Do not silently broaden filesystem, network, model, or tool authority beyond the task's declared boundary.

## Source discipline

- Treat modular source as the editable source of truth.
- Generated / packaged artifacts should be reproducible from source and should not silently replace the modular implementation.
- Add or update tests for meaningful capability changes and preserve source/version lineage.