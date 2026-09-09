from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .query import load_index, query_capabilities
from .scanner import render_markdown, scan_workspace


def _serialized(index: dict) -> tuple[str, str]:
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", render_markdown(index)


def _paths(output_dir: Path, public: bool) -> tuple[Path, Path]:
    stem = "public-discovery" if public else "local-discovery"
    return output_dir / f"{stem}.json", output_dir / f"{stem}.md"


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
    if not path.exists():
        return None
    backup = _temp_path(path, "backup")
    try:
        backup.write_bytes(path.read_bytes())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _publish_pair(json_path: Path, json_text: str, md_path: Path, md_text: str) -> None:
    """Publish the two discovery outputs without exposing a half-written pair on handled I/O failure."""
    targets = ((json_path, json_text), (md_path, md_text))
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    published: list[Path] = []

    try:
        # Complete every potentially partial file write before mutating either final path.
        for path, text in targets:
            staged[path] = _stage_text(path, text)
        for path, _text in targets:
            backups[path] = _backup_existing(path)

        for path, _text in targets:
            os.replace(staged[path], path)
            published.append(path)
    except OSError as publish_error:
        rollback_error: OSError | None = None
        for path in reversed(published):
            backup = backups.get(path)
            try:
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    os.replace(backup, path)
                    backups[path] = None
            except OSError as exc:
                rollback_error = exc
                break
        if rollback_error is not None:
            raise OSError(
                f"discovery output publish failed and rollback also failed: {rollback_error.__class__.__name__}"
            ) from publish_error
        raise
    finally:
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

    query = sub.add_parser("query", help="query a saved discovery index without executing discovered capabilities")
    query.add_argument("index", help="path to a saved local-discovery.json or public-discovery.json")
    query.add_argument("--capability-id", help="exact capability id")
    query.add_argument("--provider", help="exact provider value")
    query.add_argument("--consumer", help="exact consumer value")
    query.add_argument("--status", help="exact non-null status")
    query.add_argument("--repo", help="exact public repo id or local workspace-relative repo path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "query":
        try:
            index = load_index(args.index)
            result = query_capabilities(
                index,
                capability_id=args.capability_id,
                provider=args.provider,
                consumer=args.consumer,
                status=args.status,
                repo=args.repo,
            )
        except (OSError, ValueError) as exc:
            print(f"discovery-buddy: ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["summary"]["matches"] else 1

    try:
        index = scan_workspace(args.root, max_depth=args.max_depth, public=args.public)
    except (OSError, ValueError) as exc:
        print(f"discovery-buddy: ERROR: {exc}", file=sys.stderr)
        return 2
    json_text, md_text = _serialized(index)
    out_dir = Path(args.output_dir)
    json_path, md_path = _paths(out_dir, args.public)

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
