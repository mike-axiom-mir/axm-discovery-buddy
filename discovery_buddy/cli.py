from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .scanner import render_markdown, scan_workspace


def _serialized(index: dict) -> tuple[str, str]:
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", render_markdown(index)


def _paths(output_dir: Path, public: bool) -> tuple[Path, Path]:
    stem = "public-discovery" if public else "local-discovery"
    return output_dir / f"{stem}.json", output_dir / f"{stem}.md"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discovery-buddy", description="Deterministic local-first AXM repository discovery scanner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("root", nargs="?", default=".")
        cmd.add_argument("--output-dir", default=".discovery")
        cmd.add_argument("--max-depth", type=int, default=4)
        cmd.add_argument("--public", action="store_true", help="emit only explicitly marked public-safe repository metadata")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        index = scan_workspace(args.root, max_depth=args.max_depth, public=args.public)
    except (OSError, ValueError) as exc:
        print(f"discovery-buddy: ERROR: {exc}", file=sys.stderr)
        return 2
    json_text, md_text = _serialized(index)
    out_dir = Path(args.output_dir)
    json_path, md_path = _paths(out_dir, args.public)

    if args.command == "scan":
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_text, "utf-8")
        md_path.write_text(md_text, "utf-8")
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
