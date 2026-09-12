from __future__ import annotations

import argparse
import json
import sys

from . import _cli_core as _core
from .query import load_index, query_capabilities

# Preserve the hardened CLI surface (including private regression hooks) while
# extending it with the read-only query command. Tests and downstream callers
# that intentionally patch a CLI-level core hook continue to affect execution.
_CORE_EXPORTS = tuple(
    name for name in dir(_core)
    if not name.startswith("__") and name != "main"
)
for _name in _CORE_EXPORTS:
    globals()[_name] = getattr(_core, _name)


def _sync_core_hooks() -> None:
    for name in _CORE_EXPORTS:
        setattr(_core, name, globals()[name])


def _query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discovery-buddy query",
        description="Query an admitted saved discovery index without executing discovered capabilities.",
    )
    parser.add_argument("index", help="path to a saved local-discovery.json or public-discovery.json")
    parser.add_argument("--capability-id", help="exact capability id")
    parser.add_argument("--provider", help="exact provider value")
    parser.add_argument("--consumer", help="exact consumer value")
    parser.add_argument("--status", help="exact non-null status")
    parser.add_argument("--repo", help="exact public repo id or local workspace-relative repo path")
    return parser


def _query_main(argv: list[str]) -> int:
    args = _query_parser().parse_args(argv)
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "query":
        return _query_main(args[1:])
    _sync_core_hooks()
    return _core.main(args)
