import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from discovery_buddy import scanner
from discovery_buddy.scanner import scan_workspace


def make_repo(root: Path, rel: str) -> Path:
    repo = root / rel
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text(f"# {repo.name}\n", "utf-8")
    return repo


class SourceFileAdmissionTests(unittest.TestCase):
    def test_public_marker_symlink_outside_scan_root_cannot_opt_repo_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            repo = make_repo(root, "candidate")
            private = base / "private"
            private.mkdir()
            secret = "PRIVATE_EXTERNAL_MARKER_DO_NOT_EXPORT"
            external_marker = private / "discovery-public.json"
            external_marker.write_text(json.dumps({
                "schema": "axm.discovery-public/v1",
                "public": True,
                "repo": "private/external",
                "display_name": secret,
            }), "utf-8")
            (repo / ".axm").mkdir()
            (repo / ".axm" / "discovery-public.json").symlink_to(external_marker)

            index = scan_workspace(root, public=True)

            self.assertEqual(index["summary"]["repositories"], 0)
            self.assertNotIn(secret, json.dumps(index))
            self.assertNotIn("private/external", json.dumps(index))

    def test_symlinked_registry_directory_cannot_import_external_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            repo = make_repo(root, "candidate")
            external_registry = base / "private-registry"
            external_registry.mkdir()
            secret = "PRIVATE_EXTERNAL_CAPABILITY"
            (external_registry / "capabilities.jsonl").write_text(
                json.dumps({"id": secret, "providers": ["private-provider"]}) + "\n",
                "utf-8",
            )
            (repo / "registry").symlink_to(external_registry, target_is_directory=True)

            index = scan_workspace(root)

            record = index["repositories"][0]
            self.assertEqual(record["capabilities"]["records"], [])
            self.assertNotIn(secret, json.dumps(index))
            self.assertNotIn("private-provider", json.dumps(index))

    def test_capability_file_symlink_cannot_import_external_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            repo = make_repo(root, "candidate")
            (repo / "registry").mkdir()
            secret = "PRIVATE_EXTERNAL_FILE_CAPABILITY"
            external = base / "private-capabilities.jsonl"
            external.write_text(json.dumps({"id": secret}) + "\n", "utf-8")
            (repo / "registry" / "capabilities.jsonl").symlink_to(external)

            index = scan_workspace(root)

            capabilities = index["repositories"][0]["capabilities"]
            self.assertEqual(capabilities["records"], [])
            self.assertNotIn(secret, json.dumps(index))

    def test_git_head_symlink_cannot_leak_identity_outside_scan_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            repo = make_repo(root, "candidate")
            head = repo / ".git" / "HEAD"
            head.unlink()
            external = base / "private-head"
            secret = "PRIVATE_EXTERNAL_BRANCH"
            external.write_text(f"ref: refs/heads/{secret}\n", "ascii")
            head.symlink_to(external)

            index = scan_workspace(root)

            identity = index["repositories"][0]["git"]
            self.assertIsNone(identity["branch"])
            self.assertIsNone(identity["head"])
            self.assertNotIn(secret, json.dumps(index))

    def test_file_replaced_after_admission_is_refused_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "evidence.json"
            source.write_bytes(b'{"safe":true}\n')
            replacement = root / "replacement.json"
            replacement.write_bytes(b'{"secret":"PRIVATE_REPLACEMENT"}\n')
            real_open = os.open
            swapped = False

            def replace_before_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped and Path(path) == source:
                    os.replace(replacement, source)
                    swapped = True
                return real_open(path, flags, *args, **kwargs)

            with patch.object(scanner.os, "open", side_effect=replace_before_open):
                raw, error = scanner._read_bytes(source, 1024, root)

            self.assertTrue(swapped)
            self.assertIsNone(raw)
            self.assertEqual(error, "source_changed_before_read")


if __name__ == "__main__":
    unittest.main()
