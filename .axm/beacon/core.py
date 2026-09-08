"""Deterministic local core for AXM Organ Beacon v0.1."""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROTOCOL = "axm-beacon/0.1"
DEFAULT_CONFIG = ".axm/beacon.json"
DEFAULT_FEED_BRANCH = "axm-beacon-feed"
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ZERO_SHA_RE = re.compile(r"^0+$")
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".java", ".kt", ".kts",
    ".cs", ".rb", ".php", ".swift", ".sh", ".bash", ".ps1", ".lua",
}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml"}
TEST_MARKERS = ("test", "tests", "spec", "specs", "__tests__")
ORGAN_MARKERS = (
    "organ", "organs", "capability", "capabilities", "module", "modules",
    "adapter", "adapters", "engine", "engines", "runtime", "runtimes",
    "fabric", "node", "nodes", "observer", "sentinel",
)
PROTOCOL_MARKERS = (
    "protocol", "schema", "contract", "manifest", "receipt", "provenance",
    "state", "interface", "api",
)
TOKEN_STOPWORDS = {
    "src", "lib", "app", "apps", "core", "main", "index", "test", "tests",
    "spec", "specs", "docs", "doc", "config", "configs", "data", "assets",
    "public", "private", "new", "old", "file", "files", "the", "and", "for",
}
JS_SYMBOL_RE = re.compile(
    r"\b(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)"
)
GENERIC_SYMBOL_RE = re.compile(
    r"\b(?:class|struct|enum|interface|trait|fn|func|function)\s+([A-Za-z_][\w]*)"
)


class BeaconError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiffFile:
    path: str
    status: str
    additions: int | None
    deletions: int | None
    binary: bool
    changed_lines: frozenset[int]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_git(root: Path, *args: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.strip() if text else proc.stderr.decode("utf-8", "replace").strip()
        raise BeaconError(f"git {' '.join(args)} failed: {stderr}")
    return proc


def ensure_git_repo(root: Path) -> None:
    proc = run_git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise BeaconError(f"not a git repository: {root}")


def resolve_ref(root: Path, ref: str | None, fallback: str) -> str:
    candidate = (ref or fallback).strip()
    if not candidate or ZERO_SHA_RE.match(candidate):
        return EMPTY_TREE_SHA
    proc = run_git(root, "rev-parse", "--verify", candidate, check=False)
    if proc.returncode == 0:
        return proc.stdout.strip()
    if candidate == fallback:
        raise BeaconError(f"unable to resolve git ref: {candidate}")
    return EMPTY_TREE_SHA


def load_config(root: Path, config_path: str = DEFAULT_CONFIG) -> dict[str, Any]:
    path = root / config_path
    if not path.exists():
        return {
            "protocol": PROTOCOL,
            "repo": infer_repo_slug(root),
            "interests": [],
            "publish": {"include": ["**/*"], "exclude": [".git/**", ".axm/beacon/feed/**"]},
            "peer_discovery": {"feed_branch": DEFAULT_FEED_BRANCH},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BeaconError(f"invalid beacon config {path}: {exc}") from exc
    if data.get("protocol", PROTOCOL) != PROTOCOL:
        raise BeaconError(f"unsupported beacon protocol in {path}: {data.get('protocol')}")
    data.setdefault("repo", infer_repo_slug(root))
    data.setdefault("interests", [])
    data.setdefault("publish", {})
    data["publish"].setdefault("include", ["**/*"])
    data["publish"].setdefault("exclude", [".git/**", ".axm/beacon/feed/**"])
    data.setdefault("peer_discovery", {})
    data["peer_discovery"].setdefault("feed_branch", DEFAULT_FEED_BRANCH)
    return data


def infer_repo_slug(root: Path) -> str:
    proc = run_git(root, "remote", "get-url", "origin", check=False)
    if proc.returncode != 0:
        return root.resolve().name
    url = proc.stdout.strip().rstrip("/")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else root.resolve().name


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, p) or fnmatch.fnmatch(f"./{normalized}", p) for p in patterns)


def path_allowed(path: str, config: dict[str, Any]) -> bool:
    publish = config.get("publish", {})
    include = publish.get("include") or ["**/*"]
    exclude = publish.get("exclude") or []
    included = "**/*" in include or _matches_any(path, include)
    return included and not _matches_any(path, exclude)


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            result.setdefault(current, set())
            continue
        if line.startswith("+++ /dev/null"):
            current = None
            continue
        if current and line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                length = int(match.group(2) or "1")
                if length > 0:
                    result[current].update(range(start, start + length))
    return result


def collect_diff_files(root: Path, base: str, head: str, config: dict[str, Any]) -> tuple[list[DiffFile], str]:
    name_status = run_git(root, "diff", "--name-status", "--find-renames", base, head, "--").stdout
    parsed_status: list[tuple[str, str]] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1] if status.startswith(("R", "C")) else parts[1]
        if path_allowed(path, config):
            parsed_status.append((status, path))

    allowed_paths = [path for _status, path in parsed_status]
    if allowed_paths:
        patch = run_git(root, "diff", "--binary", "--no-ext-diff", base, head, "--", *allowed_paths).stdout
        numstat = run_git(root, "diff", "--numstat", base, head, "--", *allowed_paths).stdout
    else:
        patch = ""
        numstat = ""
    changed_lines = parse_changed_lines(patch)

    stats: dict[str, tuple[int | None, int | None, bool]] = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s, path = parts[0], parts[1], parts[-1]
        binary = add_s == "-" or del_s == "-"
        stats[path] = (None if binary else int(add_s), None if binary else int(del_s), binary)

    files: list[DiffFile] = []
    for status, path in parsed_status:
        additions, deletions, binary = stats.get(path, (None, None, False))
        files.append(
            DiffFile(
                path=path,
                status=status,
                additions=additions,
                deletions=deletions,
                binary=binary,
                changed_lines=frozenset(changed_lines.get(path, set())),
            )
        )
    return files, patch


def read_file_at_ref(root: Path, ref: str, path: str) -> bytes | None:
    proc = run_git(root, "show", f"{ref}:{path}", check=False, text=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def intersects_changed(start: int, end: int, changed: frozenset[int]) -> bool:
    if not changed:
        return True
    return any(start <= line <= end for line in changed)


def extract_symbols(path: str, content: bytes | None, changed: frozenset[int]) -> list[str]:
    if content is None:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    suffix = Path(path).suffix.lower()
    symbols: list[str] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = getattr(node, "lineno", 0)
                end = getattr(node, "end_lineno", start)
                if intersects_changed(start, end, changed):
                    symbols.append(node.name)
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        for number, line in enumerate(text.splitlines(), start=1):
            if changed and number not in changed:
                continue
            for match in JS_SYMBOL_RE.finditer(line):
                symbols.append(match.group(1))
    elif suffix in CODE_EXTENSIONS:
        for number, line in enumerate(text.splitlines(), start=1):
            if changed and number not in changed:
                continue
            for match in GENERIC_SYMBOL_RE.finditer(line):
                symbols.append(match.group(1))
    seen: set[str] = set()
    return [s for s in symbols if not (s in seen or seen.add(s))][:64]


def extract_knowledge_topics(path: str, content: bytes | None, changed: frozenset[int]) -> list[str]:
    if content is None or Path(path).suffix.lower() not in {".md", ".mdx"}:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    topics: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if changed and number not in changed:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            topic = re.sub(r"\s+#+$", "", match.group(1)).strip()
            if topic:
                topics.append(topic[:160])
    seen: set[str] = set()
    return [t for t in topics if not (t in seen or seen.add(t))][:32]


def tokenize(value: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    parts = re.split(r"[^A-Za-z0-9]+", normalized.lower())
    return [p for p in parts if len(p) >= 3 and p not in TOKEN_STOPWORDS and not p.isdigit()]


def classify_changes(files: list[dict[str, Any]]) -> tuple[list[str], list[str], int, dict[str, bool]]:
    kinds: set[str] = set()
    tags: set[str] = set()
    flags = {
        "code": False,
        "tests": False,
        "docs": False,
        "organ": False,
        "protocol": False,
        "symbols": False,
    }
    for item in files:
        path = item["path"]
        lower = path.lower()
        suffix = Path(path).suffix.lower()
        components = [p.lower() for p in Path(path).parts]
        if suffix in CODE_EXTENSIONS:
            kinds.add("code")
            flags["code"] = True
        if suffix in DOC_EXTENSIONS:
            kinds.add("knowledge")
            flags["docs"] = True
        if suffix in CONFIG_EXTENSIONS:
            kinds.add("configuration")
        if any(marker in components or marker in lower for marker in TEST_MARKERS):
            kinds.add("test-change")
            flags["tests"] = True
        if any(marker in components or marker in lower for marker in ORGAN_MARKERS):
            kinds.add("organ-capability")
            flags["organ"] = True
        if any(marker in components or marker in lower for marker in PROTOCOL_MARKERS):
            kinds.add("protocol-state")
            flags["protocol"] = True
        if item.get("symbols"):
            flags["symbols"] = True
        for token in tokenize(path):
            tags.add(token)
        for symbol in item.get("symbols", []):
            for token in tokenize(symbol):
                tags.add(token)
        for topic in item.get("topics", []):
            for token in tokenize(topic):
                tags.add(token)

    score = 0
    score += 2 if flags["code"] else 0
    score += 2 if flags["tests"] else 0
    score += 1 if flags["docs"] else 0
    score += 2 if flags["organ"] else 0
    score += 1 if flags["protocol"] else 0
    score += 1 if flags["symbols"] else 0
    return sorted(kinds), sorted(tags)[:64], score, flags


def build_capsule(root: Path, base: str, head: str, config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    files, patch = collect_diff_files(root, base, head, config)
    changed: list[dict[str, Any]] = []
    for file in files:
        content = None if file.status.startswith("D") else read_file_at_ref(root, head, file.path)
        symbols = extract_symbols(file.path, content, file.changed_lines)
        topics = extract_knowledge_topics(file.path, content, file.changed_lines)
        changed.append(
            {
                "path": file.path,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "binary": file.binary,
                "content_sha256": sha256_bytes(content) if content is not None else None,
                "symbols": symbols,
                "topics": topics,
            }
        )

    kinds, tags, attention_score, _flags = classify_changes(changed)
    declared_tags = [str(t).strip().lower() for t in config.get("publish", {}).get("tags", []) if str(t).strip()]
    tags = sorted(set(tags).union(declared_tags))[:64]
    commit_subject = run_git(root, "log", "-1", "--format=%s", head).stdout.strip()
    for token in tokenize(commit_subject):
        tags.append(token)
    tags = sorted(set(tags))[:64]
    patch_hash = sha256_text(patch)
    core = {
        "protocol": PROTOCOL,
        "source": {
            "repo": config.get("repo") or infer_repo_slug(root),
            "base": base,
            "head": head,
            "commit_subject": commit_subject,
        },
        "evidence": {"files": changed, "patch_sha256": patch_hash},
        "signals": {
            "kinds": kinds,
            "tags": tags,
            "attention_score": attention_score,
            "meaning": "candidate-for-inspection-not-truth",
        },
        "transfer": {
            "mode": "proposal-only",
            "standalone_receiver": True,
            "auto_apply": False,
        },
    }
    capsule_id = sha256_text(canonical_json(core))
    capsule = {"capsule_id": capsule_id, **core}
    return capsule, patch


def capsule_summary(capsule: dict[str, Any]) -> dict[str, Any]:
    source = capsule["source"]
    evidence = capsule["evidence"]
    return {
        "capsule_id": capsule["capsule_id"],
        "repo": source["repo"],
        "base": source["base"],
        "head": source["head"],
        "kinds": capsule["signals"]["kinds"],
        "tags": capsule["signals"]["tags"],
        "attention_score": capsule["signals"]["attention_score"],
        "file_count": len(evidence["files"]),
        "capsule_path": f"capsules/{capsule['capsule_id']}.json",
        "patch_path": f"patches/{capsule['capsule_id']}.patch",
    }


def verify_capsule(capsule: dict[str, Any]) -> tuple[bool, str]:
    capsule_id = capsule.get("capsule_id")
    if not isinstance(capsule_id, str):
        return False, "missing capsule_id"
    core = {k: v for k, v in capsule.items() if k != "capsule_id"}
    expected = sha256_text(canonical_json(core))
    if expected != capsule_id:
        return False, f"capsule hash mismatch: expected {expected}, got {capsule_id}"
    if capsule.get("protocol") != PROTOCOL:
        return False, f"unsupported protocol: {capsule.get('protocol')}"
    return True, "ok"


def publish(root: Path, base_ref: str | None, head_ref: str | None, output_dir: Path, config_path: str) -> dict[str, Any]:
    ensure_git_repo(root)
    config = load_config(root, config_path)
    head = resolve_ref(root, head_ref, "HEAD")
    base = resolve_ref(root, base_ref, f"{head}^")
    capsule, patch = build_capsule(root, base, head, config)

    output_dir.mkdir(parents=True, exist_ok=True)
    capsules_dir = output_dir / "capsules"
    patches_dir = output_dir / "patches"
    capsules_dir.mkdir(exist_ok=True)
    patches_dir.mkdir(exist_ok=True)
    capsule_path = capsules_dir / f"{capsule['capsule_id']}.json"
    patch_path = patches_dir / f"{capsule['capsule_id']}.patch"
    capsule_path.write_text(json.dumps(capsule, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    patch_path.write_text(patch, encoding="utf-8")

    index_path = output_dir / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}
    else:
        index = {}
    entries = {entry["capsule_id"]: entry for entry in index.get("capsules", []) if isinstance(entry, dict) and entry.get("capsule_id")}
    entries[capsule["capsule_id"]] = capsule_summary(capsule)
    max_capsules = int(config.get("publish", {}).get("max_capsules", 200))
    ordered = sorted(entries.values(), key=lambda e: (e.get("head", ""), e.get("capsule_id", "")), reverse=True)[:max_capsules]
    index = {
        "protocol": PROTOCOL,
        "repo": config.get("repo") or infer_repo_slug(root),
        "capsules": ordered,
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return capsule
