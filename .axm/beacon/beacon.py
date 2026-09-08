#!/usr/bin/env python3
"""AXM Organ Beacon v0.1 command line.

Each repository carries its own copy. Peer discoveries are proposal-only: this
command never applies a remote patch to canonical source.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import (  # noqa: E402
    BeaconError,
    PROTOCOL,
    DEFAULT_CONFIG,
    DEFAULT_FEED_BRANCH,
    capsule_summary,
    load_config,
    publish,
    verify_capsule,
)
from network import fetch_capsule, rank_candidates, scan  # noqa: E402


def cmd_publish(args: argparse.Namespace) -> int:
    capsule = publish(
        Path(args.repo_root).resolve(),
        args.base,
        args.head,
        Path(args.output_dir).resolve(),
        args.config,
    )
    print(json.dumps(capsule_summary(capsule), indent=2, sort_keys=True))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan(
        Path(args.repo_root).resolve(),
        args.config,
        [Path(p).resolve() for p in args.feed_dir],
        args.github,
        args.limit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    capsule = json.loads(Path(args.capsule).read_text(encoding="utf-8"))
    ok, reason = verify_capsule(capsule)
    print(reason)
    return 0 if ok else 2


def cmd_fetch(args: argparse.Namespace) -> int:
    config = load_config(Path(args.repo_root).resolve(), args.config)
    branch = args.branch or config.get("peer_discovery", {}).get("feed_branch", DEFAULT_FEED_BRANCH)
    capsule_path, patch_path = fetch_capsule(
        args.repo,
        args.capsule_id,
        str(branch),
        Path(args.dest).resolve(),
        token=os.getenv("GITHUB_TOKEN"),
    )
    print(json.dumps({"capsule": str(capsule_path), "patch": str(patch_path), "applied": False}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AXM Organ Beacon: deterministic cross-repo discovery without silent adoption"
    )
    parser.add_argument("--repo-root", default=".", help="repository root (default: current directory)")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"config path relative to repo (default: {DEFAULT_CONFIG})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pub = sub.add_parser("publish", help="package a git change as a deterministic evidence capsule + patch")
    pub.add_argument("--base", help="base git ref; invalid/zero refs fall back to the empty tree")
    pub.add_argument("--head", help="head git ref (default: HEAD)")
    pub.add_argument("--output-dir", default=".axm/beacon/feed", help="feed output directory")
    pub.set_defaults(func=cmd_publish)

    scan_p = sub.add_parser("scan", help="rank peer beacon indexes against this repo's declared interests")
    scan_p.add_argument("--feed-dir", action="append", default=[], help="local peer feed/index path; repeatable")
    scan_p.add_argument("--github", action="store_true", help="also discover public peer feeds through GitHub")
    scan_p.add_argument("--limit", type=int, default=20)
    scan_p.set_defaults(func=cmd_scan)

    verify_p = sub.add_parser("verify", help="verify a capsule's deterministic identity")
    verify_p.add_argument("capsule")
    verify_p.set_defaults(func=cmd_verify)

    fetch_p = sub.add_parser("fetch", help="fetch a candidate capsule + patch into a proposal-only inbox")
    fetch_p.add_argument("--repo", required=True, help="source repo in owner/name form")
    fetch_p.add_argument("--capsule-id", required=True)
    fetch_p.add_argument("--branch", help=f"feed branch (default from config or {DEFAULT_FEED_BRANCH})")
    fetch_p.add_argument("--dest", default=".axm/beacon/inbox")
    fetch_p.set_defaults(func=cmd_fetch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (BeaconError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"beacon error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
