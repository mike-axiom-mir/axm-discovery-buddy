from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
    def test_second_output_write_failure_preserves_last_good_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            repo = make_repo(root)
            out = Path(tmp) / "out"

            self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
            json_path = out / "local-discovery.json"
            md_path = out / "local-discovery.md"
            baseline_json = json_path.read_bytes()
            baseline_md = md_path.read_bytes()

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


if __name__ == "__main__":
    unittest.main()
