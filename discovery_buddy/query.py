from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .scanner import CAPABILITY_SCHEMA, SCHEMA_VERSION

QUERY_SCHEMA = "axm.discovery-query/v0.1"
MAX_INDEX_BYTES = 16 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in _HEX for ch in value)


def _repo_identity(record: dict[str, Any], visibility: str) -> tuple[str, str]:
    if visibility == "PUBLIC_SAFE_DECLARED_ONLY":
        repo = record.get("repo")
        display = record.get("display_name")
        if not isinstance(repo, str) or not repo:
            raise ValueError("public repository record is missing repo identity")
        return repo, display if isinstance(display, str) and display else repo
    path = record.get("path")
    name = record.get("name")
    if not isinstance(path, str) or not path:
        raise ValueError("local repository record is missing workspace-relative path")
    return path, name if isinstance(name, str) and name else path


def _validate_capability(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("schema") != CAPABILITY_SCHEMA:
        raise ValueError("invalid capability record schema")
    capability_id = record.get("id")
    source = record.get("source")
    line = record.get("line")
    providers = record.get("providers")
    consumers = record.get("consumers")
    status = record.get("status")
    if not isinstance(capability_id, str) or not capability_id.strip():
        raise ValueError("capability record is missing id")
    if not isinstance(source, str) or not source:
        raise ValueError(f"capability {capability_id!r} is missing source")
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise ValueError(f"capability {capability_id!r} has invalid source line")
    if not isinstance(providers, list) or any(not isinstance(value, str) for value in providers):
        raise ValueError(f"capability {capability_id!r} has invalid providers")
    if not isinstance(consumers, list) or any(not isinstance(value, str) for value in consumers):
        raise ValueError(f"capability {capability_id!r} has invalid consumers")
    if status is not None and not isinstance(status, str):
        raise ValueError(f"capability {capability_id!r} has invalid status")
    return record


def validate_index(index: Any) -> dict[str, Any]:
    """Validate a saved Discovery Buddy index before another tool consumes it."""
    if not isinstance(index, dict) or index.get("schema") != SCHEMA_VERSION:
        raise ValueError("unsupported discovery index schema")
    visibility = index.get("visibility")
    if visibility not in {"LOCAL_ONLY", "PUBLIC_SAFE_DECLARED_ONLY"}:
        raise ValueError("unsupported discovery index visibility")
    policy = index.get("policy")
    repositories = index.get("repositories")
    summary = index.get("summary")
    claimed_digest = index.get("content_sha256")
    if not isinstance(policy, dict) or not isinstance(repositories, list) or not isinstance(summary, dict):
        raise ValueError("discovery index is missing policy, repositories, or summary")
    if not _is_sha256(claimed_digest):
        raise ValueError("discovery index has invalid content_sha256")

    digest_payload = {"schema": SCHEMA_VERSION, "policy": policy, "repositories": repositories}
    actual_digest = _sha256(digest_payload)
    if claimed_digest != actual_digest:
        raise ValueError("discovery index content digest mismatch")

    capability_count = 0
    beacon_count = 0
    identities: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, dict):
            raise ValueError("invalid repository record")
        identity, _display = _repo_identity(repository, visibility)
        if identity in identities:
            raise ValueError(f"duplicate repository identity: {identity}")
        identities.add(identity)
        capabilities = repository.get("capabilities")
        beacon = repository.get("beacon")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("records"), list):
            raise ValueError(f"repository {identity!r} is missing capability records")
        if not isinstance(beacon, dict):
            raise ValueError(f"repository {identity!r} is missing beacon evidence")
        for record in capabilities["records"]:
            _validate_capability(record)
            capability_count += 1
        if beacon.get("present") is True:
            beacon_count += 1

    expected_summary = {
        "repositories": len(repositories),
        "capability_records": capability_count,
        "beacons": beacon_count,
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise ValueError(f"discovery index summary mismatch for {key}")
    return index


def load_index(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink():
        raise ValueError("discovery index path must not be a symlink")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ValueError(f"discovery index is unreadable: {exc.__class__.__name__}") from exc
    if size > MAX_INDEX_BYTES:
        raise ValueError(f"discovery index exceeds {MAX_INDEX_BYTES} bytes")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"discovery index is unreadable: {exc.__class__.__name__}") from exc
    try:
        index = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"discovery index is invalid JSON: {exc.__class__.__name__}") from exc
    return validate_index(index)


def query_capabilities(
    index: dict[str, Any],
    *,
    capability_id: str | None = None,
    provider: str | None = None,
    consumer: str | None = None,
    status: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return deterministic capability candidates without selecting or executing one."""
    validate_index(index)
    filters = {
        "capability_id": capability_id,
        "provider": provider,
        "consumer": consumer,
        "status": status,
        "repo": repo,
    }
    visibility = index["visibility"]
    candidates: list[dict[str, Any]] = []
    for repository in index["repositories"]:
        identity, display_name = _repo_identity(repository, visibility)
        if repo is not None and identity != repo:
            continue
        for record in repository["capabilities"]["records"]:
            record = _validate_capability(record)
            if capability_id is not None and record["id"] != capability_id:
                continue
            if provider is not None and provider not in record["providers"]:
                continue
            if consumer is not None and consumer not in record["consumers"]:
                continue
            if status is not None and record["status"] != status:
                continue
            candidates.append({
                "repository": identity,
                "display_name": display_name,
                "capability": {
                    "schema": record["schema"],
                    "id": record["id"],
                    "providers": list(record["providers"]),
                    "consumers": list(record["consumers"]),
                    "status": record["status"],
                    "source": record["source"],
                    "line": record["line"],
                },
            })
    candidates.sort(key=lambda row: (
        row["capability"]["id"],
        row["repository"],
        row["capability"]["source"],
        row["capability"]["line"],
    ))
    body = {
        "schema": QUERY_SCHEMA,
        "source_schema": index["schema"],
        "source_visibility": visibility,
        "source_content_sha256": index["content_sha256"],
        "filters": filters,
        "summary": {"matches": len(candidates)},
        "candidates": candidates,
        "authority": {
            "classification": "DISCOVERY_EVIDENCE_ONLY",
            "execute": False,
            "install": False,
            "select": False,
            "merge": False,
            "canon": False,
        },
    }
    return {**body, "query_sha256": _sha256(body)}
