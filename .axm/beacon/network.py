"""Optional peer discovery/transport for AXM Organ Beacon v0.1."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core import (
    BeaconError,
    DEFAULT_FEED_BRANCH,
    PROTOCOL,
    load_config,
    sha256_bytes,
    tokenize,
    verify_capsule,
)


def _request_json(url: str, token: str | None = None, timeout: float = 8.0) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "axm-organ-beacon/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_github_repos(owner: str, prefixes: list[str], token: str | None = None) -> list[str]:
    url = f"https://api.github.com/users/{owner}/repos?per_page=100&type=owner&sort=updated"
    data = _request_json(url, token=token)
    repos: list[str] = []
    for item in data:
        if item.get("archived"):
            continue
        name = item.get("name", "")
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        repos.append(f"{owner}/{name}")
    return repos


def fetch_peer_indexes(owner: str, prefixes: list[str], branch: str, token: str | None = None) -> list[dict[str, Any]]:
    indexes: list[dict[str, Any]] = []
    for repo in discover_github_repos(owner, prefixes, token=token):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/index.json"
        try:
            index = _request_json(url, token=token)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            continue
        if index.get("protocol") == PROTOCOL:
            indexes.append(index)
    return indexes


def load_local_indexes(feed_dirs: list[Path]) -> list[dict[str, Any]]:
    indexes: list[dict[str, Any]] = []
    for directory in feed_dirs:
        path = directory / "index.json" if directory.is_dir() else directory
        if not path.exists():
            continue
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if index.get("protocol") == PROTOCOL:
            indexes.append(index)
    return indexes


def rank_candidates(indexes: list[dict[str, Any]], local_repo: str, interests: list[str]) -> list[dict[str, Any]]:
    interest_tokens = {token for interest in interests for token in tokenize(interest)}
    ranked: list[dict[str, Any]] = []
    for index in indexes:
        for capsule in index.get("capsules", []):
            if capsule.get("repo") == local_repo:
                continue
            tags = set(capsule.get("tags", []))
            kinds = set(capsule.get("kinds", []))
            overlap = sorted(interest_tokens.intersection(tags.union(kinds)))
            relevance = len(overlap) * 3 + min(int(capsule.get("attention_score", 0)), 9)
            ranked.append({**capsule, "interest_overlap": overlap, "relevance_score": relevance})
    return sorted(
        ranked,
        key=lambda item: (
            item["relevance_score"],
            item.get("attention_score", 0),
            item.get("capsule_id", ""),
        ),
        reverse=True,
    )


def scan(root: Path, config_path: str, feed_dirs: list[Path], github: bool, limit: int) -> dict[str, Any]:
    config = load_config(root, config_path)
    indexes = load_local_indexes(feed_dirs)
    peer = config.get("peer_discovery", {})
    if github:
        owner = peer.get("github_owner")
        if not owner:
            repo = config.get("repo", "")
            owner = repo.split("/", 1)[0] if "/" in repo else None
        if owner:
            prefixes = [str(p) for p in peer.get("repo_prefixes", [])]
            branch = str(peer.get("feed_branch", DEFAULT_FEED_BRANCH))
            token = os.getenv("GITHUB_TOKEN")
            indexes.extend(fetch_peer_indexes(owner, prefixes, branch, token=token))
    ranked = rank_candidates(
        indexes,
        str(config.get("repo", "")),
        [str(i) for i in config.get("interests", [])],
    )
    return {
        "protocol": PROTOCOL,
        "repo": config.get("repo"),
        "candidate_count": len(ranked),
        "candidates": ranked[:limit],
    }


def fetch_capsule(repo: str, capsule_id: str, branch: str, dest: Path, token: str | None = None) -> tuple[Path, Path]:
    base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    capsule_url = f"{base}/capsules/{capsule_id}.json"
    patch_url = f"{base}/patches/{capsule_id}.patch"
    capsule = _request_json(capsule_url, token=token)
    ok, reason = verify_capsule(capsule)
    if not ok:
        raise BeaconError(reason)
    req = urllib.request.Request(patch_url, headers={"User-Agent": "axm-organ-beacon/0.1"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=12.0) as response:
        patch = response.read()
    expected_patch = capsule.get("evidence", {}).get("patch_sha256")
    if expected_patch and sha256_bytes(patch) != expected_patch:
        raise BeaconError("patch hash mismatch")
    target = dest / capsule_id
    target.mkdir(parents=True, exist_ok=True)
    capsule_path = target / "capsule.json"
    patch_path = target / "change.patch"
    capsule_path.write_text(json.dumps(capsule, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    patch_path.write_bytes(patch)
    (target / "STATUS.txt").write_text(
        "proposal-only\nnot applied\nreceiver must inspect + test before adoption\n",
        encoding="utf-8",
    )
    return capsule_path, patch_path
