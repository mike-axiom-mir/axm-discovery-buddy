import json
from pathlib import Path
import tempfile
import unittest

from discovery_buddy.cli import main as cli_main
from discovery_buddy.scanner import scan_workspace


def make_repo(root: Path, rel: str, branch: str = "main", sha: str = "a" * 40) -> Path:
    repo = root / rel
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", "ascii")
    (git / "refs" / "heads" / branch).write_text(sha + "\n", "ascii")
    (repo / "README.md").write_text(f"# {repo.name}\n", "utf-8")
    return repo


class ScannerTests(unittest.TestCase):
    def test_scan_is_deterministic_and_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, "zeta")
            alpha = make_repo(root, "alpha", branch="dev", sha="b" * 40)
            (alpha / "AGENTS.md").write_text("rules\n", "utf-8")
            first = scan_workspace(root)
            second = scan_workspace(root)
            self.assertEqual(first, second)
            self.assertEqual([row["path"] for row in first["repositories"]], ["alpha", "zeta"])
            self.assertEqual(first["repositories"][0]["git"]["branch"], "dev")
            self.assertEqual(first["repositories"][0]["git"]["head"], "b" * 40)
            self.assertEqual(first["visibility"], "LOCAL_ONLY")
            self.assertFalse(first["policy"]["absolute_paths_exported"])

    def test_root_repository_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git = root / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
            (git / "refs" / "heads" / "main").write_text("c" * 40 + "\n", "ascii")
            (root / "README.md").write_text("# root\n", "utf-8")
            index = scan_workspace(root)
            self.assertEqual(index["summary"]["repositories"], 1)
            self.assertEqual(index["repositories"][0]["path"], ".")
            self.assertEqual(index["repositories"][0]["git"]["head"], "c" * 40)

    def test_capability_registry_and_beacon_are_discovered_without_file_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repo(root, "provider")
            (repo / ".axm").mkdir()
            (repo / ".axm" / "beacon.json").write_text(json.dumps({
                "protocol": "axm-beacon/0.1",
                "repo": "example/provider",
                "interests": ["state protocol", "state protocol"],
                "publish": {"tags": ["axm", "provider"]},
            }), "utf-8")
            (repo / "registry").mkdir()
            (repo / "registry" / "capabilities.jsonl").write_text(
                json.dumps({"id": "state:snapshot", "providers": ["provider"], "consumers": ["viewer"]}) + "\n",
                "utf-8",
            )
            (repo / "secret.txt").write_text("DO_NOT_EXPORT_THIS_SECRET", "utf-8")
            index = scan_workspace(root)
            record = index["repositories"][0]
            self.assertEqual(record["beacon"]["protocol"], "axm-beacon/0.1")
            self.assertEqual(record["beacon"]["interests"], ["state protocol"])
            self.assertEqual(record["capabilities"]["records"][0]["id"], "state:snapshot")
            self.assertNotIn("DO_NOT_EXPORT_THIS_SECRET", json.dumps(index))

    def test_public_mode_requires_explicit_marker_and_redacts_local_path_and_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hidden = make_repo(root, "private-work")
            public = make_repo(root, "publish-me")
            (public / ".axm").mkdir()
            (public / ".axm" / "discovery-public.json").write_text(json.dumps({
                "schema": "axm.discovery-public/v1",
                "public": True,
                "repo": "example/publish-me",
                "display_name": "Publish Me",
            }), "utf-8")
            index = scan_workspace(root, public=True)
            self.assertEqual(index["summary"]["repositories"], 1)
            record = index["repositories"][0]
            self.assertEqual(record["repo"], "example/publish-me")
            self.assertNotIn("path", record)
            self.assertNotIn("git", record)
            self.assertNotIn(str(hidden), json.dumps(index))

    def test_max_depth_is_a_hard_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, "one/two/three/repo")
            shallow = scan_workspace(root, max_depth=2)
            deep = scan_workspace(root, max_depth=4)
            self.assertEqual(shallow["summary"]["repositories"], 0)
            self.assertEqual(deep["summary"]["repositories"], 1)

    def test_verify_fails_when_generated_map_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(root, "repo")
            out = root / "out"
            self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
            self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 0)
            (root / "repo" / "README.md").write_text("changed\n", "utf-8")
            self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 1)


if __name__ == "__main__":
    unittest.main()
