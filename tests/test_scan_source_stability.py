from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discovery_buddy.cli as cli
from discovery_buddy.scanner import scan_workspace as real_scan_workspace


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text("# baseline\n", "utf-8")
    return repo


class ScanSourceStabilityTests(unittest.TestCase):
    def test_scan_refuses_to_publish_snapshot_when_source_changes_after_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            repo = make_repo(root)
            out = Path(tmp) / "out"

            self.assertEqual(cli.main(["scan", str(root), "--output-dir", str(out)]), 0)
            baseline_json = (out / "local-discovery.json").read_bytes()
            baseline_md = (out / "local-discovery.md").read_bytes()

            (repo / "README.md").write_text("# observed by first pass\n", "utf-8")
            calls = 0

            def mutate_after_first_scan(*args, **kwargs):
                nonlocal calls
                calls += 1
                index = real_scan_workspace(*args, **kwargs)
                if calls == 1:
                    # Mutation happens after the scanner has built a complete snapshot but before
                    # the CLI can serialize/publish it. A stable-source admission step must see it.
                    (repo / "README.md").write_text("# changed before publication\n", "utf-8")
                return index

            stderr = io.StringIO()
            with patch.object(cli, "scan_workspace", side_effect=mutate_after_first_scan):
                with redirect_stderr(stderr):
                    result = cli.main(["scan", str(root), "--output-dir", str(out)])

            self.assertEqual(
                result,
                2,
                msg="scan published evidence captured before a source mutation instead of holding",
            )
            self.assertGreaterEqual(calls, 2, msg="scan did not re-observe the source before publication")
            self.assertIn("DISCOVERY_SOURCE_CHANGED", stderr.getvalue())
            self.assertEqual((out / "local-discovery.json").read_bytes(), baseline_json)
            self.assertEqual((out / "local-discovery.md").read_bytes(), baseline_md)
            self.assertFalse((out / ".local-discovery.transaction.json").exists())

            # The retained pair is still last-good evidence and therefore stale relative to the
            # now-mutated workspace until a later clean scan is explicitly requested.
            self.assertEqual(cli.main(["verify", str(root), "--output-dir", str(out)]), 1)


if __name__ == "__main__":
    unittest.main()
