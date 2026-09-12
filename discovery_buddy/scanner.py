from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
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
MAX_GIT_TEXT_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _read_bytes(path: Path, max_bytes: int, allowed_root: Path) -> tuple[bytes | None, str | None]:
    """Read one regular source file without following it outside the admitted root."""
    try:
        root = allowed_root.resolve(strict=True)
        admitted = os.lstat(path)
        if not stat.S_ISREG(admitted.st_mode):
            return None, "unsafe_source_type"
        if admitted.st_size > max_bytes:
            return None, f"too_large:{admitted.st_size}>{max_bytes}"

        resolved = path.resolve(strict=True)
        if not _inside(resolved, root):
            return None, "source_outside_root"

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                return None, "unsafe_source_type"
            if _file_identity(opened) != _file_identity(admitted):
                return None, "source_changed_before_read"
            if opened.st_size > max_bytes:
                return None, f"too_large:{opened.st_size}>{max_bytes}"

            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                chunk = os.read(descriptor, min(READ_CHUNK_BYTES, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > max_bytes:
                return None, f"too_large:>{max_bytes}"

            finished = os.fstat(descriptor)
            if (
                _file_identity(finished) != _file_identity(opened)
                or finished.st_size != opened.st_size
                or finished.st_mtime_ns != opened.st_mtime_ns
                or finished.st_ctime_ns != opened.st_ctime_ns
                or total != finished.st_size
            ):
                return None, "source_changed_during_read"
            return b"".join(chunks), None
        finally:
            os.close(descriptor)
    except OSError as exc:
        return None, f"read_error:{exc.__class__.__name__}"


def _read_json(
    path: Path,
    allowed_root: Path,
    max_bytes: int = MAX_JSON_BYTES,
) -> tuple[Any | None, str | None, str | None]:
    raw, error = _read_bytes(path, max_bytes, allowed_root)
    if raw is None:
        return None, error, None
    digest = _sha256_bytes(raw)
    try:
        return json.loads(raw.decode("utf-8-sig")), None, digest
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid_json:{exc.__class__.__name__}", digest


def _git_dir(repo_root: Path, scan_root: Path) -> tuple[Path | None, str | None]:
    marker = repo_root / ".git"
    if not (marker.exists() or marker.is_symlink()):
        return None, None
    try:
        resolved_marker = marker.resolve(strict=True)
    except OSError:
        return None, "git_dir_unreadable"
    scan_root = scan_root.resolve()
    if not _inside(resolved_marker, scan_root):
        return None, "git_dir_outside_scan_root"
    if resolved_marker.is_dir():
        return resolved_marker, None
    if resolved_marker.is_file():
        raw, read_error = _read_bytes(resolved_marker, MAX_GIT_TEXT_BYTES, scan_root)
        if raw is None:
            return None, "git_dir_unreadable"
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None, "git_dir_unreadable"
        prefix = "gitdir:"
        if text.lower().startswith(prefix):
            target = text[len(prefix):].strip()
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = (repo_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if not _inside(candidate, scan_root):
                return None, "git_dir_outside_scan_root"
            if not candidate.is_dir():
                return None, "git_dir_unreadable"
            return candidate, None
    return None, "git_dir_unreadable"


def _read_git_text(path: Path, git_dir: Path, *, errors: str = "strict") -> str | None:
    raw, error = _read_bytes(path, MAX_GIT_TEXT_BYTES, git_dir)
    if raw is None or error is not None:
        return None
    try:
        return raw.decode("ascii", errors=errors)
    except UnicodeDecodeError:
        return None


def _resolve_ref(git_dir: Path, ref: str) -> str | None:
    loose = git_dir / ref
    if loose.exists() or loose.is_symlink():
        value = _read_git_text(loose, git_dir)
        if value is not None:
            value = value.strip()
            return value or None
    packed = git_dir / "packed-refs"
    if packed.exists() or packed.is_symlink():
        text = _read_git_text(packed, git_dir, errors="replace")
        if text is not None:
            for line in text.splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name == ref:
                    return sha
    return None


def read_git_identity(repo_root: Path, scan_root: Path) -> dict[str, Any]:
    marker = repo_root / ".git"
    present = marker.exists() or marker.is_symlink()
    git_dir, error = _git_dir(repo_root, scan_root)
    result: dict[str, Any] = {
        "present": present,
        "branch": None,
        "head": None,
        "detached": False,
    }
    if error:
        result["error"] = error
    if git_dir is None:
        return result
    head_text = _read_git_text(git_dir / "HEAD", git_dir)
    if head_text is None:
        result["error"] = "head_unreadable"
        return result
    head_text = head_text.strip()
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
    if not (path.exists() or path.is_symlink()):
        return {"present": False, "sha256": None}
    raw, error = _read_bytes(path, MAX_JSON_BYTES, repo_root)
    return {
        "present": True,
        "sha256": _sha256_bytes(raw) if raw is not None else None,
        "error": error,
    }


def read_beacon(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".axm" / "beacon.json"
    if not (path.exists() or path.is_symlink()):
        return {"present": False}
    data, error, digest = _read_json(path, repo_root)
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
    if not (path.exists() or path.is_symlink()):
        return {"present": False, "eligible": False}
    data, error, digest = _read_json(path, repo_root)
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
    registry = repo_root / "registry"
    if registry.is_symlink():
        return
    preferred = [
        registry / "capabilities.jsonl",
        registry / "capabilities.v0.1.jsonl",
    ]
    seen: set[Path] = set()
    for path in preferred:
        if (path.exists() or path.is_symlink()) and path not in seen:
            seen.add(path)
            yield path
    if registry.is_dir():
        for path in sorted(registry.glob("*capabilit*.jsonl")):
            if path not in seen:
                seen.add(path)
                yield path


def read_capabilities(repo_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    registry = repo_root / "registry"
    if registry.is_symlink():
        return {"sources": [{"path": "registry", "error": "unsafe_source_type"}], "records": []}
    for path in _registry_candidates(repo_root):
        raw, error = _read_bytes(path, MAX_JSONL_BYTES, repo_root)
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
                "git": read_git_identity(repo_root, root_path),
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
