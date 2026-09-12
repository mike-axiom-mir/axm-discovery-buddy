from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from discovery_buddy.cli import main
from discovery_buddy.query import QUERY_SCHEMA, load_index, query_capabilities, validate_index
from discovery_buddy.scanner import scan_workspace


class CapabilityQueryTests(unittest.TestCase):
    def _repo(self, root: Path, name: str, public_repo: str, capability_id: str, *, provider: str, status: str = "TEST") -> Path:
        repo = root / name
        (repo / ".git" / "refs" / "heads").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
        (repo / ".git" / "refs" / "heads" / "main").write_text("1" * 40 + "\n", "ascii")
        (repo / ".axm").mkdir()
        (repo / ".axm" / "discovery-public.json").write_text(json.dumps({
            "schema": "axm.discovery-public/v1",
            "public": True,
            "repo": public_repo,
            "display_name": name,
        }) + "\n", "utf-8")
        (repo / "registry").mkdir()
        (repo / "registry" / "capabilities.jsonl").write_text(json.dumps({
            "id": capability_id,
            "providers": [provider],
            "consumers": ["explicit-consumer"],
            "status": status,
        }, sort_keys=True) + "\n", "utf-8")
        return repo

    def _saved_public_index(self, root: Path) -> tuple[dict, Path]:
        self._repo(root, "alpha", "example/alpha", "axm.demo.render/v1", provider="alpha.cli")
        self._repo(root, "beta", "example/beta", "axm.demo.render/v1", provider="beta.api", status="WORKING")
        index = scan_workspace(root, public=True)
        path = root / "public-discovery.json"
        path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
        return index, path

    def test_query_preserves_lineage_and_grants_no_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index, _path = self._saved_public_index(Path(tmp))
            result = query_capabilities(index, capability_id="axm.demo.render/v1", provider="beta.api")

        self.assertEqual(QUERY_SCHEMA, result["schema"])
        self.assertEqual(index["content_sha256"], result["source_content_sha256"])
        self.assertEqual(1, result["summary"]["matches"])
        self.assertEqual("example/beta", result["candidates"][0]["repository"])
        self.assertEqual("registry/capabilities.jsonl", result["candidates"][0]["capability"]["source"])
        self.assertEqual("DISCOVERY_EVIDENCE_ONLY", result["authority"]["classification"])
        self.assertFalse(any(result["authority"][key] for key in ("execute", "install", "select", "merge", "canon")))
        self.assertEqual(64, len(result["query_sha256"]))

    def test_cli_returns_one_for_valid_no_match_and_two_for_invalid_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _index, path = self._saved_public_index(Path(tmp))
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["query", str(path), "--capability-id", "missing.capability"])
            self.assertEqual(1, code)
            self.assertEqual(0, json.loads(out.getvalue())["summary"]["matches"])

            tampered = json.loads(path.read_text("utf-8"))
            tampered["repositories"][0]["capabilities"]["records"][0]["id"] = "forged.capability"
            path.write_text(json.dumps(tampered), "utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["query", str(path)])
            self.assertEqual(2, code)
            self.assertIn("content digest mismatch", err.getvalue())

    def test_summary_drift_is_rejected_even_though_summary_is_outside_content_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index, _path = self._saved_public_index(Path(tmp))
            index["summary"]["capability_records"] += 1
            with self.assertRaisesRegex(ValueError, "summary mismatch"):
                validate_index(index)

    def test_invalid_capability_shape_fails_closed_after_resealing_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index, _path = self._saved_public_index(Path(tmp))
            record = index["repositories"][0]["capabilities"]["records"][0]
            record["providers"] = "not-a-list"
            payload = {
                "schema": index["schema"],
                "policy": index["policy"],
                "repositories": index["repositories"],
            }
            import hashlib
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            index["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            with self.assertRaisesRegex(ValueError, "invalid providers"):
                validate_index(index)

    def test_saved_index_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _index, path = self._saved_public_index(root)
            link = root / "linked-index.json"
            try:
                link.symlink_to(path.name)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                load_index(link)

    def test_exact_filters_are_anded_and_results_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index, _path = self._saved_public_index(Path(tmp))
            first = query_capabilities(index, capability_id="axm.demo.render/v1", consumer="explicit-consumer", status="TEST")
            second = query_capabilities(index, capability_id="axm.demo.render/v1", consumer="explicit-consumer", status="TEST")
        self.assertEqual(first, second)
        self.assertEqual(["example/alpha"], [row["repository"] for row in first["candidates"]])


if __name__ == "__main__":
    unittest.main()
