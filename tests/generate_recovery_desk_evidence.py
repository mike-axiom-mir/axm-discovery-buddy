from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import discovery_buddy.cli as cli
from discovery_buddy.cli import main as cli_main
from discovery_buddy.recovery_desk import inspect_recovery, render_html, render_text

EVIDENCE = Path("evidence/recovery-desk")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text("# last-good discovery evidence\n", "utf-8")
    return repo


def stage_interrupted_pair(root: Path, repo: Path, out: Path) -> tuple[Path, Path, Path]:
    (repo / "README.md").write_text("# newer discovery evidence before interrupted publication\n", "utf-8")
    index = cli.scan_workspace(root, max_depth=4, public=False)
    json_text, md_text = cli._serialized(index)
    json_path, md_path = cli._paths(out, False)
    journal = cli._transaction_path(json_path)
    targets = ((json_path, json_text), (md_path, md_text))
    staged = {path: cli._stage_text(path, text) for path, text in targets}
    backups = {path: cli._backup_existing(path) for path, _text in targets}
    cli._write_transaction(journal, [(path, staged[path], backups[path]) for path, _text in targets])
    os.replace(staged[json_path], json_path)
    return json_path, md_path, journal


def main() -> int:
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    workspace = EVIDENCE / "workspace"
    workspace.mkdir(parents=True)
    repo = make_repo(workspace)
    out = EVIDENCE / "out"

    if cli_main(["scan", str(workspace), "--output-dir", str(out)]) != 0:
        raise RuntimeError("baseline scan failed")
    json_path, md_path = cli._paths(out, False)
    baseline_json = json_path.read_bytes()
    baseline_md = md_path.read_bytes()

    _json_path, _md_path, journal = stage_interrupted_pair(workspace, repo, out)
    mixed_json = json_path.read_bytes()
    mixed_md = md_path.read_bytes()
    before_preview = {path.name: sha256(path.read_bytes()) for path in out.iterdir() if path.is_file()}

    report = inspect_recovery(out)
    if report["status"] != "READY_ROLLBACK_LAST_GOOD":
        raise AssertionError(report)
    (EVIDENCE / "recovery-desk.html").write_text(render_html(report), "utf-8")
    (EVIDENCE / "recovery-desk.txt").write_text(render_text(report), "utf-8")
    (EVIDENCE / "preview.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", "utf-8")

    after_preview = {path.name: sha256(path.read_bytes()) for path in out.iterdir() if path.is_file()}
    if before_preview != after_preview:
        raise AssertionError("preview mutated discovery transaction evidence")

    if cli_main(["recover", "--output-dir", str(out)]) != 0:
        raise AssertionError("recommended recovery did not succeed")
    if json_path.read_bytes() != baseline_json or md_path.read_bytes() != baseline_md:
        raise AssertionError("recommended rollback did not restore exact last-good bytes")
    if journal.exists():
        raise AssertionError("journal still exists after successful production recovery")

    receipt = {
        "schema": "axm.discovery-recovery-desk-evidence/v0.1",
        "preview_status": report["status"],
        "preview_mutated_outputs": False,
        "mixed_pair": {
            "json_sha256": sha256(mixed_json),
            "markdown_sha256": sha256(mixed_md),
        },
        "last_good_pair": {
            "json_sha256": sha256(baseline_json),
            "markdown_sha256": sha256(baseline_md),
        },
        "production_recovery_exercised": True,
        "production_recovery_outcome": "ROLLED_BACK_LAST_GOOD",
        "restored_exact_last_good_bytes": True,
        "journal_cleared_after_recovery": True,
    }
    (EVIDENCE / "evidence-receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", "utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
