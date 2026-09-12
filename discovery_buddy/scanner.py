from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "axm.discovery-index/v0.1"
PUBLIC_MARKER_SCHEMA = "axm.discovery-public/v1"
CAPABILITY_SCHEMA = "axm.discovery-capability/v0.1"
DEFAULT_EXCLUDES = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
})
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSONL_BYTES = 4 * 1024 * 1024


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_bytes(path: Path, max_bytes: int) -> tuple[bytes | None, str | None]:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None, f"too_large:{size}>{max_bytes}"
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"read_error:{exc.__class__.__name__}"


def _read_json(path: Path, max_bytes: int = MAX_JSON_BYTES) -> tuple[Any | None, str | None, str | None]:
    raw, error = _read_bytes(path, max_bytes)
    if raw is None:
        return None, error, None
    digest = _sha256_bytes(raw)
    try:
        return json.loads(raw.decode("utf-8-sig")), None, digest
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid_json:{exc.__class__.__name__}", digest


def _git_dir(repo_root: Path) -> Path | None:
    marker = repo_root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        try:
            text = marker.read_text("utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if text.lower().startswith(prefix):
            target = text[len(prefix):].strip()
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = (repo_root / candidate).resolve()
            return candidate
    return None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    loose = git_dir / ref
    if loose.is_file():
        try:
            value = loose.read_text("ascii").strip()
        except OSError:
            return None
        return value or None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text("ascii", errors="replace").splitlines():
                if not line or line.startswith(('#', '^')):
                    continue
                sha, _, name = line.partition(" ")
                if name == ref:
                    return sha
        except OSError:
            return None
    return None


def read_git_identity(repo_root: Path) -> dict[str, Any]:
    git_dir = _git_dir(repo_root)
    result: dict[str, Any] = {
        "present": git_dir is not None,
        "branch": None,
        "head": None,
        "detached": False,
    }
    if git_dir is None:
        return result
    try:
        head_text = (git_dir / "HEAD").read_text("ascii").strip()
    except OSError:
        result["error"] = "head_unreadable"
        return result
    if head_text.startswith("ref: "):
        ref = head_text[5:].strip()
        result["branch"] = ref.removeprefix("refs/heads/")
        result["head"] = _resolve_ref(git_dir, ref)
    else:
        result["detached"] = True
        result["head"] = head_text or None
    return result


def _safe_text_digest(repo_root: Path, relative: str) -> dict[str, Any]:
    path = repo_root / relative
    if not path.is_file():
        return {"present": False, "sha256": None}
    raw, error = _read_bytes(path, MAX_JSON_BYTES)
    return {
        "present": True,
        "sha256": _sha256_bytes(raw) if raw is not None else None,
        "error": error,
    }


def read_beacon(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".axm" / "beacon.json"
    if not path.is_file():
        return {"present": False}
    data, error, digest = _read_json(path)
    result: dict[str, Any] = {"present": True, "sha256": digest}
    if error:
        result["error"] = error
        return result
    if not isinstance(data, dict):
        result["error"] = "invalid_shape"
        return result
    interests = data.get("interests") if isinstance(data.get("interests"), list) else []
    publish = data.get("publish") if isinstance(data.get("publish"), dict) else {}
    tags = publish.get("tags") if isinstance(publish.get("tags"), list) else []
    result.update({
        "protocol": data.get("protocol") if isinstance(data.get("protocol"), str) else None,
        "repo": data.get("repo") if isinstance(data.get("repo"), str) else None,
        "interests": sorted({str(item) for item in interests if isinstance(item, str)}),
        "tags": sorted({str(item) for item in tags if isinstance(item, str)}),
    })
    return result


def read_public_marker(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".axm" / "discovery-public.json"
    if not path.is_file():
        return {"present": False, "eligible": False}
    data, error, digest = _read_json(path)
    result: dict[str, Any] = {"present": True, "eligible": False, "sha256": digest}
    if error:
        result["error"] = error
        return result
    if not isinstance(data, dict) or data.get("schema") != PUBLIC_MARKER_SCHEMA:
        result["error"] = "invalid_schema"
        return result
    repo = data.get("repo")
    public = data.get("public")
    if public is not True or not isinstance(repo, str) or "/" not in repo:
        result["error"] = "not_explicitly_public"
        return result
    result.update({
        "eligible": True,
        "repo": repo,
        "display_name": data.get("display_name") if isinstance(data.get("display_name"), str) else repo,
    })
    return result


def _registry_candidates(repo_root: Path) -> Iterable[Path]:
    preferred = [
        repo_root / "registry" / "capabilities.jsonl",
        repo_root / "registry" / "capabilities.v0.1.jsonl",
    ]
    seen: set[Path] = set()
    for path in preferred:
        if path.is_file() and path not in seen:
            seen.add(path)
            yield path
    registry = repo_root / "registry"
    if registry.is_dir():
        for path in sorted(registry.glob("*capabilit*.jsonl")):
            if path not in seen:
                seen.add(path)
                yield path


def read_capabilities(repo_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for path in _registry_candidates(repo_root):
        raw, error = _read_bytes(path, MAX_JSONL_BYTES)
        relative = path.relative_to(repo_root).as_posix()
        if raw is None:
            sources.append({"path": relative, "error": error})
            continue
        digest = _sha256_bytes(raw)
        source = {"path": relative, "sha256": digest, "records": 0, "errors": 0}
        for line_number, line in enumerate(raw.decode("utf-8-sig", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                source["errors"] += 1
                continue
            if not isinstance(item, dict):
                source["errors"] += 1
                continue
            capability_id = item.get("id")
            if not isinstance(capability_id, str) or not capability_id.strip():
                source["errors"] += 1
                continue
            providers = item.get("providers") if isinstance(item.get("providers"), list) else []
            consumers = item.get("consumers") if isinstance(item.get("consumers"), list) else []
            status = item.get("status") if isinstance(item.get("status"), str) else None
            rows.append({
                "schema": CAPABILITY_SCHEMA,
                "id": capability_id,
                "providers": sorted({str(v) for v in providers if isinstance(v, str)}),
                "consumers": sorted({str(v) for v in consumers if isinstance(v, str)}),
                "status": status,
                "source": relative,
                "line": line_number,
            })
            source["records"] += 1
        sources.append(source)
    rows.sort(key=lambda row: (row["id"], row["source"], row["line"]))
    return {"sources": sources, "records": rows}


def _walk_for_repositories(root: Path, max_depth: int, excludes: frozenset[str]) -> list[Path]:
    root = root.resolve()
    repos: list[Path] = []
    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue
        has_git_dir = ".git" in dirs
        has_git_file = (current_path / ".git").is_file()
        if has_git_dir or has_git_file:
            repos.append(current_path)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in excludes)
        if depth >= max_depth:
            dirs[:] = []
    return sorted(repos, key=lambda p: p.relative_to(root).as_posix())


def scan_workspace(root: Path | str, max_depth: int = 4, public: bool = False) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"scan root is not a directory: {root_path}")
    if max_depth < 0 or max_depth > 32:
        raise ValueError("max_depth must be between 0 and 32")

    repositories: list[dict[str, Any]] = []
    for repo_root in _walk_for_repositories(root_path, max_depth, DEFAULT_EXCLUDES):
        rel = repo_root.relative_to(root_path).as_posix() or "."
        beacon = read_beacon(repo_root)
        public_marker = read_public_marker(repo_root)
        capabilities = read_capabilities(repo_root)
        if public and not public_marker.get("eligible"):
            continue

        if public:
            repo_record: dict[str, Any] = {
                "repo": public_marker["repo"],
                "display_name": public_marker["display_name"],
                "public_marker_sha256": public_marker.get("sha256"),
                "beacon": beacon,
                "capabilities": capabilities,
            }
        else:
            repo_record = {
                "path": rel,
                "name": repo_root.name,
                "git": read_git_identity(repo_root),
                "readme": _safe_text_digest(repo_root, "README.md"),
                "agents": _safe_text_digest(repo_root, "AGENTS.md"),
                "beacon": beacon,
                "public_marker": public_marker,
                "capabilities": capabilities,
            }
        repositories.append(repo_record)

    repositories.sort(key=lambda row: row.get("repo") or row.get("path") or "")
    policy = {
        "max_depth": max_depth,
        "public": public,
        "excluded_directory_names": sorted(DEFAULT_EXCLUDES),
        "absolute_paths_exported": False,
        "file_contents_exported": False,
        "public_requires_explicit_marker": True,
    }
    digest_payload = {"schema": SCHEMA_VERSION, "policy": policy, "repositories": repositories}
    return {
        "schema": SCHEMA_VERSION,
        "visibility": "PUBLIC_SAFE_DECLARED_ONLY" if public else "LOCAL_ONLY",
        "root": ".",
        "policy": policy,
        "summary": {
            "repositories": len(repositories),
            "capability_records": sum(len(r["capabilities"]["records"]) for r in repositories),
            "beacons": sum(1 for r in repositories if r["beacon"].get("present")),
        },
        "repositories": repositories,
        "content_sha256": _sha256_bytes(_canonical_json(digest_payload).encode("utf-8")),
    }


def render_markdown(index: dict[str, Any]) -> str:
    public = index.get("visibility") == "PUBLIC_SAFE_DECLARED_ONLY"
    lines = [
        "# AXM Discovery Map",
        "",
        f"Schema: `{index.get('schema')}`  ",
        f"Visibility: **{index.get('visibility')}**  ",
        f"Content SHA-256: `{index.get('content_sha256')}`",
        "",
        f"Repositories: **{index.get('summary', {}).get('repositories', 0)}** · "
        f"Capability records: **{index.get('summary', {}).get('capability_records', 0)}** · "
        f"Beacons: **{index.get('summary', {}).get('beacons', 0)}**",
        "",
    ]
    for repo in index.get("repositories", []):
        if public:
            title = repo.get("display_name") or repo.get("repo")
            lines.extend([f"## {title}", "", f"Repository: `{repo.get('repo')}`"])
        else:
            lines.extend([
                f"## {repo.get('name')}",
                "",
                f"Path: `{repo.get('path')}`",
                f"Branch: `{repo.get('git', {}).get('branch') or 'UNKNOWN'}`",
                f"Head: `{repo.get('git', {}).get('head') or 'UNKNOWN'}`",
            ])
        beacon = repo.get("beacon", {})
        if beacon.get("present"):
            lines.append(f"Beacon: `{beacon.get('protocol') or 'UNKNOWN'}`")
            if beacon.get("tags"):
                lines.append("Tags: " + ", ".join(f"`{tag}`" for tag in beacon["tags"]))
            if beacon.get("interests"):
                lines.append("Interests: " + ", ".join(beacon["interests"]))
        capability_rows = repo.get("capabilities", {}).get("records", [])
        if capability_rows:
            lines.append(f"Declared capability records: **{len(capability_rows)}**")
            for row in capability_rows[:20]:
                role_bits = []
                if row.get("providers"):
                    role_bits.append(f"providers={len(row['providers'])}")
                if row.get("consumers"):
                    role_bits.append(f"consumers={len(row['consumers'])}")
                suffix = f" ({', '.join(role_bits)})" if role_bits else ""
                lines.append(f"- `{row['id']}`{suffix}")
            if len(capability_rows) > 20:
                lines.append(f"- … {len(capability_rows) - 20} more in machine-readable index")
        lines.append("")
    lines.extend([
        "## Truth boundary",
        "",
        "This map reports deterministic local discovery evidence. A declared capability is not runtime proof, authority, quality, or CANON. Public output includes only repositories carrying an explicit `.axm/discovery-public.json` marker and still exports only bounded whitelisted fields.",
        "",
    ])
    return "\n".join(lines)
