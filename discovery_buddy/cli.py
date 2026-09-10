from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from .scanner import render_markdown, scan_workspace

TRANSACTION_SCHEMA = "axm.discovery-output-transaction/v0.1"
MAX_TRANSACTION_BYTES = 64 * 1024


def _serialized(index: dict) -> tuple[str, str]:
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", render_markdown(index)


def _paths(output_dir: Path, public: bool) -> tuple[Path, Path]:
    stem = "public-discovery" if public else "local-discovery"
    return output_dir / f"{stem}.json", output_dir / f"{stem}.md"


def _transaction_path(json_path: Path) -> Path:
    return json_path.parent / f".{json_path.stem}.transaction.json"


def _temp_path(path: Path, purpose: str) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=f".{purpose}.tmp",
        dir=path.parent,
    )
    os.close(fd)
    return Path(raw_path)


def _stage_text(path: Path, text: str) -> Path:
    staged = _temp_path(path, "stage")
    try:
        staged.write_text(text, "utf-8")
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _backup_existing(path: Path) -> Path | None:
    if path.is_symlink():
        raise OSError("refusing symlinked discovery output")
    if not path.exists():
        return None
    if not path.is_file():
        raise OSError("discovery output is not a regular file")
    backup = _temp_path(path, "backup")
    try:
        backup.write_bytes(path.read_bytes())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _sha256_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise OSError("transaction artifact is not a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_artifact(parent: Path, name: Any) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise OSError("invalid transaction artifact path")
    return parent / name


def _write_transaction(
    journal_path: Path,
    targets: list[tuple[Path, Path, Path | None]],
) -> dict[str, Any]:
    transaction = {
        "schema": TRANSACTION_SCHEMA,
        "targets": [
            {
                "target": target.name,
                "stage": staged.name,
                "backup": backup.name if backup is not None else None,
                "new_sha256": _sha256_regular(staged),
                "old_sha256": _sha256_regular(backup) if backup is not None else None,
            }
            for target, staged, backup in targets
        ],
    }
    temporary = _temp_path(journal_path, "journal")
    try:
        temporary.write_text(
            json.dumps(transaction, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            "utf-8",
        )
        os.replace(temporary, journal_path)
    finally:
        temporary.unlink(missing_ok=True)
    return transaction


def _load_transaction(journal_path: Path, expected_targets: tuple[Path, Path]) -> dict[str, Any]:
    if journal_path.is_symlink() or not journal_path.is_file():
        raise OSError("transaction journal is not a regular file")
    if journal_path.stat().st_size > MAX_TRANSACTION_BYTES:
        raise OSError("transaction journal exceeds size limit")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate transaction key")
            result[key] = value
        return result

    try:
        data = json.loads(journal_path.read_text("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OSError(f"invalid transaction journal: {exc.__class__.__name__}") from exc

    if not isinstance(data, dict) or data.get("schema") != TRANSACTION_SCHEMA:
        raise OSError("invalid transaction journal schema")
    entries = data.get("targets")
    if not isinstance(entries, list) or len(entries) != 2:
        raise OSError("invalid transaction target set")

    expected_names = [path.name for path in expected_targets]
    actual_names = [entry.get("target") if isinstance(entry, dict) else None for entry in entries]
    if actual_names != expected_names:
        raise OSError("transaction target mismatch")

    for entry in entries:
        if not isinstance(entry, dict) or not _is_sha256(entry.get("new_sha256")):
            raise OSError("invalid transaction target evidence")
        old_sha = entry.get("old_sha256")
        if old_sha is not None and not _is_sha256(old_sha):
            raise OSError("invalid transaction old digest")
        _safe_artifact(journal_path.parent, entry.get("stage"))
        backup = entry.get("backup")
        if backup is not None:
            _safe_artifact(journal_path.parent, backup)
        if (old_sha is None) != (backup is None):
            raise OSError("transaction backup/digest mismatch")
    return data


def _target_digest(path: Path) -> str | None:
    if path.is_symlink():
        raise OSError("unsafe symlink at transaction target")
    if not path.exists():
        return None
    return _sha256_regular(path)


def _old_state_matches(targets: tuple[Path, Path], transaction: dict[str, Any]) -> bool:
    return all(
        _target_digest(target) == entry["old_sha256"]
        for target, entry in zip(targets, transaction["targets"])
    )


def _cleanup_transaction_artifacts(parent: Path, transaction: dict[str, Any]) -> None:
    for entry in transaction["targets"]:
        for field in ("stage", "backup"):
            name = entry.get(field)
            if name is not None:
                _safe_artifact(parent, name).unlink(missing_ok=True)


def _restore_target_from_backup(target: Path, backup: Path, expected_sha256: str) -> None:
    if _sha256_regular(backup) != expected_sha256:
        raise OSError("transaction backup integrity mismatch")
    restore = _temp_path(target, "restore")
    try:
        restore.write_bytes(backup.read_bytes())
        if _sha256_regular(restore) != expected_sha256:
            raise OSError("transaction restore staging mismatch")
        os.replace(restore, target)
    finally:
        restore.unlink(missing_ok=True)


def _recover_pair(json_path: Path, md_path: Path) -> str:
    """Resolve one interrupted two-file publication using only journal-bound evidence."""
    journal_path = _transaction_path(json_path)
    targets = (json_path, md_path)
    transaction = _load_transaction(journal_path, targets)
    parent = journal_path.parent

    finals_are_new = all(
        _target_digest(target) == entry["new_sha256"]
        for target, entry in zip(targets, transaction["targets"])
    )
    if finals_are_new:
        _cleanup_transaction_artifacts(parent, transaction)
        journal_path.unlink()
        return "FINALIZED_NEW"

    # Validate every rollback source before changing either final output.
    for entry in transaction["targets"]:
        old_sha = entry["old_sha256"]
        if old_sha is None:
            continue
        backup = _safe_artifact(parent, entry["backup"])
        if _sha256_regular(backup) != old_sha:
            raise OSError("transaction backup integrity mismatch")

    # Backups are copied rather than consumed so recovery itself can be safely retried.
    for target, entry in zip(targets, transaction["targets"]):
        old_sha = entry["old_sha256"]
        if old_sha is None:
            if target.is_symlink():
                raise OSError("unsafe symlink at transaction target")
            target.unlink(missing_ok=True)
        else:
            backup = _safe_artifact(parent, entry["backup"])
            _restore_target_from_backup(target, backup, old_sha)

    if not _old_state_matches(targets, transaction):
        raise OSError("rollback verification failed")
    _cleanup_transaction_artifacts(parent, transaction)
    journal_path.unlink()
    return "ROLLED_BACK_LAST_GOOD"


def _publish_pair(json_path: Path, json_text: str, md_path: Path, md_text: str) -> None:
    """Publish one recoverable discovery generation across the JSON/Markdown pair."""
    journal_path = _transaction_path(json_path)
    if journal_path.exists() or journal_path.is_symlink():
        raise OSError("pending discovery output transaction requires recovery")

    targets = ((json_path, json_text), (md_path, md_text))
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    published: list[Path] = []
    transaction: dict[str, Any] | None = None

    try:
        for path, text in targets:
            staged[path] = _stage_text(path, text)
        for path, _text in targets:
            backups[path] = _backup_existing(path)

        transaction = _write_transaction(
            journal_path,
            [(path, staged[path], backups[path]) for path, _text in targets],
        )

        for path, _text in targets:
            os.replace(staged[path], path)
            published.append(path)
    except OSError as publish_error:
        rollback_error: OSError | None = None
        if transaction is not None:
            for path in reversed(published):
                entry = transaction["targets"][0 if path == json_path else 1]
                old_sha = entry["old_sha256"]
                try:
                    if old_sha is None:
                        path.unlink(missing_ok=True)
                    else:
                        backup = _safe_artifact(journal_path.parent, entry["backup"])
                        _restore_target_from_backup(path, backup, old_sha)
                except OSError as exc:
                    rollback_error = exc
                    break
            if rollback_error is None and not _old_state_matches((json_path, md_path), transaction):
                rollback_error = OSError("rollback verification failed")

        if rollback_error is not None:
            raise OSError(
                f"discovery output publish failed and rollback also failed: {rollback_error.__class__.__name__}"
            ) from publish_error

        if journal_path.exists() and not journal_path.is_symlink():
            journal_path.unlink()
        raise
    else:
        # Final outputs are now a complete generation. Keep the journal until cleanup is complete;
        # if the process dies here, explicit recovery recognizes and finalizes the exact new pair.
        if transaction is not None:
            _cleanup_transaction_artifacts(journal_path.parent, transaction)
        journal_path.unlink()
    finally:
        # Before journaling, final paths were never mutated. Once journaled, remaining artifacts
        # are recovery evidence and must survive an unhandled interruption.
        if not journal_path.exists() and not journal_path.is_symlink():
            for temp_path in list(staged.values()) + [p for p in backups.values() if p is not None]:
                temp_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discovery-buddy", description="Deterministic local-first AXM repository discovery scanner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("root", nargs="?", default=".")
        cmd.add_argument("--output-dir", default=".discovery")
        cmd.add_argument("--max-depth", type=int, default=4)
        cmd.add_argument("--public", action="store_true", help="emit only explicitly marked public-safe repository metadata")
    recover = sub.add_parser("recover", help="explicitly recover an interrupted discovery output transaction")
    recover.add_argument("--output-dir", default=".discovery")
    recover.add_argument("--public", action="store_true", help="recover the public-safe output pair instead of local output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.output_dir)
    json_path, md_path = _paths(out_dir, args.public)
    journal_path = _transaction_path(json_path)

    if args.command == "recover":
        if not journal_path.exists() and not journal_path.is_symlink():
            print("discovery-buddy: no interrupted output transaction found")
            return 0
        try:
            outcome = _recover_pair(json_path, md_path)
        except OSError as exc:
            print(f"discovery-buddy: ERROR: output recovery failed: {exc}", file=sys.stderr)
            return 2
        print(f"discovery-buddy: RECOVERED: {outcome}")
        return 0

    if journal_path.exists() or journal_path.is_symlink():
        print(
            f"discovery-buddy: ERROR: interrupted output transaction at {journal_path}; "
            f"run `python -m discovery_buddy recover --output-dir {out_dir}` first",
            file=sys.stderr,
        )
        return 2

    try:
        index = scan_workspace(args.root, max_depth=args.max_depth, public=args.public)
    except (OSError, ValueError) as exc:
        print(f"discovery-buddy: ERROR: {exc}", file=sys.stderr)
        return 2
    json_text, md_text = _serialized(index)

    if args.command == "scan":
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            _publish_pair(json_path, json_text, md_path, md_text)
        except OSError as exc:
            print(f"discovery-buddy: ERROR: output publish failed: {exc.__class__.__name__}", file=sys.stderr)
            return 2
        print(f"discovery-buddy: wrote {json_path} and {md_path} ({index['summary']['repositories']} repos, digest {index['content_sha256']})")
        return 0

    mismatches = []
    for path, expected in ((json_path, json_text), (md_path, md_text)):
        actual = path.read_text("utf-8") if path.is_file() else None
        if actual != expected:
            mismatches.append(str(path))
    if mismatches:
        print("discovery-buddy: STALE: " + ", ".join(mismatches), file=sys.stderr)
        return 1
    print(f"discovery-buddy: PASS ({index['summary']['repositories']} repos, digest {index['content_sha256']})")
    return 0
