# Discovery Recovery Desk

The Recovery Desk is a **local, read-only preview** for Discovery Buddy's existing explicit transaction recovery.

Use it after `scan` or `verify` reports that an interrupted output transaction requires recovery and you want to understand the exact evidence before choosing the mutating `recover` command.

```bash
python -m discovery_buddy.recovery_desk --output-dir /tmp/axm-discovery
```

For the public-safe output pair, keep the same mode explicit:

```bash
python -m discovery_buddy.recovery_desk --output-dir /tmp/axm-discovery --public
```

The terminal view distinguishes the useful states:

- `NO RECOVERY NEEDED` — no transaction journal is visible;
- `NEW GENERATION COMPLETE` — both final files already match the journal-bound new generation, so production recovery is expected to finalize/clean transaction artifacts without changing final output bytes;
- `ROLLBACK EVIDENCE READY` — the pair is incomplete/mixed and every required last-good backup matches its journal-bound SHA-256, so production recovery is expected to restore the exact last-good pair;
- `RECOVERY EVIDENCE HELD` / `STATE CHANGED DURING PREVIEW` — the desk cannot prove a safe outcome and deliberately does not present recovery as a safe next action.

The preview uses the recovery implementation's existing journal/path/digest admission helpers instead of inventing a second transaction format. It does **not** call `_recover_pair`, does not clear the journal, and does not rewrite either discovery output.

## Local HTML realization

Add `--html` when a visual comparison is easier to review:

```bash
python -m discovery_buddy.recovery_desk \
  --output-dir /tmp/axm-discovery \
  --html /tmp/axm-discovery-recovery.html
```

Open the resulting HTML locally. It is self-contained: no fonts, scripts, styles, accounts, cloud calls, or network resources are fetched. It shows the current/new/last-good identity of both output files, backup admission, the expected production recovery effect, and—only when the preview can prove a ready state—the existing explicit recovery command as the actionable next step.

A copy button copies that command when browser clipboard permission is available; otherwise it selects the command for manual copy and says so visibly. The button never executes recovery.

## Truth boundary

`DISPLAY ≠ RECOVERY AUTHORITY` is part of the surface, not decorative copy.

The desk is a snapshot. It intentionally takes no writer ownership because taking the existing output lock can create/retain its inert coordination file and would make a “read-only preview” claim false on a previously untouched output directory. A cooperating writer can therefore change evidence immediately before, during, or after inspection. The desk double-checks the journal and final-target digests during its read and holds the preview if those values change while it is inspecting, but this is **not** a lock or race-proof guarantee.

The production command remains authoritative for mutation:

```bash
python -m discovery_buddy recover --output-dir /tmp/axm-discovery
```

That command independently acquires the repository's host-local writer lock and re-validates the journal and required rollback evidence before changing outputs. A preview that says `ROLLBACK EVIDENCE READY` or `NEW GENERATION COMPLETE` is therefore an explanation of the inspected evidence, not a promise that a later recovery invocation must still see the same state.

The Recovery Desk does not establish authorship, cryptographic authentication, distributed locking, sudden-power-loss durability, cross-host exclusion, merge/release authority, or CANON. It does not make public-safe output proof of licensing or release readiness. It adds no AI, account, cloud, relay, or model dependency.
