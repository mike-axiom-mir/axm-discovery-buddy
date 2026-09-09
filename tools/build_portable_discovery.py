from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

SCHEMA = "axm.discovery-portable-zipapp/v0.1"
ROOT_MAIN = (
    "from discovery_buddy.cli import main\n\n"
    "raise SystemExit(main())\n"
)
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SourceEntry:
    path: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _is_regular_unlinked(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def _collect_sources(repo_root: Path) -> list[SourceEntry]:
    repo_root = repo_root.resolve()
    package = repo_root / "discovery_buddy"
    if not package.is_dir() or package.is_symlink():
        raise ValueError("discovery_buddy package must be a real directory")

    entries: list[SourceEntry] = []
    for root, dirs, files in os.walk(package, topdown=True, followlinks=False):
        root_path = Path(root)
        rejected_dirs = [name for name in dirs if (root_path / name).is_symlink()]
        if rejected_dirs:
            raise ValueError(f"source package contains symlink directory: {rejected_dirs[0]}")
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        for name in sorted(files):
            path = root_path / name
            if path.is_symlink():
                raise ValueError(f"source package contains symlink file: {path.relative_to(repo_root)}")
            if path.suffix != ".py":
                continue
            if not _is_regular_unlinked(path):
                raise ValueError(f"source package member is not a regular file: {path.relative_to(repo_root)}")
            rel = path.relative_to(repo_root).as_posix()
            entries.append(SourceEntry(rel, path.read_bytes()))

    entries.sort(key=lambda entry: entry.path)
    required = {
        "discovery_buddy/__init__.py",
        "discovery_buddy/__main__.py",
        "discovery_buddy/cli.py",
        "discovery_buddy/scanner.py",
        "discovery_buddy/query.py",
    }
    present = {entry.path for entry in entries}
    missing = sorted(required - present)
    if missing:
        raise ValueError("portable source is incomplete: " + ", ".join(missing))
    return entries


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, FIXED_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _archive_bytes(entries: list[SourceEntry]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(_zip_info("__main__.py"), ROOT_MAIN.encode("utf-8"))
        for entry in entries:
            archive.writestr(_zip_info(entry.path), entry.data)
    return stream.getvalue()


def _source_digest(entries: list[SourceEntry]) -> str:
    body = [
        {"path": entry.path, "bytes": len(entry.data), "sha256": entry.sha256}
        for entry in entries
    ]
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _receipt(entries: list[SourceEntry], artifact: bytes) -> dict:
    return {
        "schema": SCHEMA,
        "artifact": {
            "format": "python-zipapp",
            "entrypoint": "__main__.py",
            "bytes": len(artifact),
            "sha256": hashlib.sha256(artifact).hexdigest(),
            "python_requires": ">=3.11",
            "runtime_dependencies": [],
            "network_required": False,
        },
        "source": {
            "scope": "discovery_buddy/**/*.py",
            "sha256": _source_digest(entries),
            "files": [
                {"path": entry.path, "bytes": len(entry.data), "sha256": entry.sha256}
                for entry in entries
            ],
        },
        "authority": {
            "classification": "PORTABLE_TOOL_ONLY",
            "discovered_capability_execution": False,
            "automatic_selection": False,
            "discovered_capability_installation": False,
            "merge": False,
            "canon": False,
        },
        "truth_boundary": (
            "The receipt binds exact packaged source bytes to this local zipapp. "
            "It is integrity evidence, not authorship authentication, runtime compatibility proof, "
            "provider approval, merge authority, or CANON."
        ),
    }


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_output(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"output must not be a symlink: {path}")
    path = path.expanduser().absolute()
    if path.exists() and not _is_regular_unlinked(path):
        raise ValueError(f"output must be a regular non-symlink file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build(repo_root: Path, output: Path, receipt_path: Path) -> dict:
    entries = _collect_sources(repo_root)
    artifact = _archive_bytes(entries)
    receipt = _receipt(entries, artifact)

    output = _safe_output(output)
    receipt_path = _safe_output(receipt_path)
    if output == receipt_path:
        raise ValueError("artifact and receipt paths must differ")

    output.write_bytes(artifact)
    receipt_path.write_bytes(_json_bytes(receipt))
    return receipt


def verify(repo_root: Path, output: Path, receipt_path: Path) -> dict:
    entries = _collect_sources(repo_root)
    expected_artifact = _archive_bytes(entries)
    expected_receipt = _json_bytes(_receipt(entries, expected_artifact))

    if not _is_regular_unlinked(output):
        raise ValueError("portable artifact is missing or not a regular non-symlink file")
    if not _is_regular_unlinked(receipt_path):
        raise ValueError("portable receipt is missing or not a regular non-symlink file")
    if output.read_bytes() != expected_artifact:
        raise ValueError("portable artifact drifted from current source")
    if receipt_path.read_bytes() != expected_receipt:
        raise ValueError("portable receipt drifted from current source/artifact")
    return json.loads(expected_receipt)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-portable-discovery",
        description="Build or verify a deterministic dependency-free Discovery Buddy Python zipapp.",
    )
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--output", default="dist/discovery-buddy.pyz")
    parser.add_argument("--receipt", default="dist/discovery-buddy.pyz.receipt.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    output = Path(args.output)
    receipt_path = Path(args.receipt)
    try:
        result = (
            build(repo_root, output, receipt_path)
            if args.command == "build"
            else verify(repo_root, output, receipt_path)
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"portable-discovery: HOLD: {exc}", file=sys.stderr)
        return 2

    digest = result["artifact"]["sha256"]
    action = "wrote" if args.command == "build" else "PASS"
    print(f"portable-discovery: {action} {output} ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
