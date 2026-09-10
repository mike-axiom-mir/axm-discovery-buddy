from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import discovery_buddy.cli as cli_module
from discovery_buddy.cli import main as cli_main


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text("# initial\n", "utf-8")
    return repo


class OutputDurabilityTests(unittest.TestCase):
    def _last_good_fixture(self, tmp: str) -> tuple[Path, Path, Path, bytes, bytes]:
        root = Path(tmp) / "workspace"
        root.mkdir()
        repo = make_repo(root)
        out = Path(tmp) / "out"
        self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
        json_path = out / "local-discovery.json"
        md_path = out / "local-discovery.md"
        return root, repo, out, json_path.read_bytes(), md_path.read_bytes()

    def test_second_output_write_failure_preserves_last_good_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, baseline_json, baseline_md = self._last_good_fixture(tmp)
            json_path = out / "local-discovery.json"
            md_path = out / "local-discovery.md"

            (repo / "README.md").write_text("# changed after last-good snapshot\n", "utf-8")
            real_write_text = Path.write_text

            def fail_markdown_write(path_self, data, *args, **kwargs):
                if isinstance(data, str) and data.startswith("# AXM Discovery Map"):
                    raise OSError("simulated second-output write failure")
                return real_write_text(path_self, data, *args, **kwargs)

            with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail_markdown_write):
                result = cli_main(["scan", str(root), "--output-dir", str(out)])

            self.assertEqual(result, 2)
            self.assertEqual(json_path.read_bytes(), baseline_json)
            self.assertEqual(md_path.read_bytes(), baseline_md)
            self.assertEqual(list(out.glob(".*.tmp")), [])

    def test_second_publish_replace_failure_rolls_back_first_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, baseline_json, baseline_md = self._last_good_fixture(tmp)
            json_path = out / "local-discovery.json"
            md_path = out / "local-discovery.md"

            (repo / "README.md").write_text("# changed before replace failure\n", "utf-8")
            real_replace = cli_module.os.replace

            def fail_markdown_replace(source, destination):
                if Path(destination) == md_path:
                    raise OSError("simulated second-output replace failure")
                return real_replace(source, destination)

            with mock.patch.object(cli_module.os, "replace", side_effect=fail_markdown_replace):
                result = cli_main(["scan", str(root), "--output-dir", str(out)])

            self.assertEqual(result, 2)
            self.assertEqual(json_path.read_bytes(), baseline_json)
            self.assertEqual(md_path.read_bytes(), baseline_md)
            self.assertEqual(list(out.glob(".*.tmp")), [])

    def test_process_crash_after_first_publish_is_explicitly_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, baseline_json, baseline_md = self._last_good_fixture(tmp)
            json_path = out / "local-discovery.json"
            md_path = out / "local-discovery.md"
            journal = out / ".local-discovery.transaction.json"

            (repo / "README.md").write_text("# changed before process crash\n", "utf-8")
            child = """
import os
from pathlib import Path
import sys
import discovery_buddy.cli as cli
root = Path(sys.argv[1])
out = Path(sys.argv[2])
target = out / "local-discovery.json"
real_replace = cli.os.replace
def crash_after_json(source, destination):
    real_replace(source, destination)
    if Path(destination) == target:
        os._exit(91)
cli.os.replace = crash_after_json
cli.main(["scan", str(root), "--output-dir", str(out)])
raise SystemExit(99)
"""
            crashed = subprocess.run([sys.executable, "-c", child, str(root), str(out)], check=False)

            self.assertEqual(crashed.returncode, 91)
            self.assertTrue(journal.is_file())
            self.assertNotEqual(json_path.read_bytes(), baseline_json)
            self.assertEqual(md_path.read_bytes(), baseline_md)
            self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 2)
            self.assertEqual(cli_main(["recover", "--output-dir", str(out)]), 0)
            self.assertEqual(json_path.read_bytes(), baseline_json)
            self.assertEqual(md_path.read_bytes(), baseline_md)
            self.assertFalse(journal.exists())
            self.assertEqual(list(out.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
