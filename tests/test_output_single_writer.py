from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

from discovery_buddy.cli import main as cli_main


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_NAME = ".local-discovery.writer.lock"


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text("# initial\n", "utf-8")
    return repo


def start_writer_paused_before_journal(root: Path, out: Path, ready: Path, release: Path) -> subprocess.Popen[str]:
    child = r'''
from pathlib import Path
import sys
import time
import discovery_buddy.cli as cli

root = Path(sys.argv[1])
out = Path(sys.argv[2])
ready = Path(sys.argv[3])
release = Path(sys.argv[4])
real_write_transaction = cli._write_transaction

def pause_before_journal(journal_path, targets):
    ready.write_text("ready\n", "utf-8")
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for release")
        time.sleep(0.01)
    return real_write_transaction(journal_path, targets)

cli._write_transaction = pause_before_journal
raise SystemExit(cli.main(["scan", str(root), "--output-dir", str(out)]))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.Popen(
        [sys.executable, "-c", child, str(root), str(out), str(ready), str(release)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_ready(process: subprocess.Popen[str], ready: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"writer exited before ready: {process.returncode}; stdout={stdout!r}; stderr={stderr!r}")
        time.sleep(0.01)
    process.kill()
    stdout, stderr = process.communicate()
    raise AssertionError(f"writer did not reach pre-journal barrier; stdout={stdout!r}; stderr={stderr!r}")


class OutputSingleWriterTests(unittest.TestCase):
    def test_second_scan_cannot_overtake_writer_before_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            repo = make_repo(root)
            out = Path(tmp) / "out"
            self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)

            baseline_json = (out / "local-discovery.json").read_bytes()
            baseline_md = (out / "local-discovery.md").read_bytes()
            (repo / "README.md").write_text("# first writer snapshot\n", "utf-8")

            ready = Path(tmp) / "writer-ready"
            release = Path(tmp) / "writer-release"
            first = start_writer_paused_before_journal(root, out, ready, release)
            try:
                wait_ready(first, ready)
                self.assertFalse((out / ".local-discovery.transaction.json").exists())

                # The first scanner has already built its older in-memory snapshot but has not
                # journaled or published it. A second scanner now observes newer workspace state.
                (repo / "README.md").write_text("# newer workspace state\n", "utf-8")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    second_result = cli_main(["scan", str(root), "--output-dir", str(out)])

                # Let the original writer finish regardless of the assertion result so the test
                # never leaves a child behind. Without a single-writer gate, the second scan can
                # publish first and this older first scan can then overwrite it.
                release.write_text("release\n", "utf-8")
                first_stdout, first_stderr = first.communicate(timeout=10)
                self.assertEqual(first.returncode, 0, msg=f"stdout={first_stdout!r}; stderr={first_stderr!r}")

                self.assertEqual(
                    second_result,
                    2,
                    msg="a competing scan entered publication while another scan already owned an older snapshot",
                )
                self.assertIn("DISCOVERY_OUTPUT_BUSY", stderr.getvalue())

                # The older snapshot is now a legitimate completed generation. A new scan after
                # ownership is released must be able to publish the current workspace state.
                self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 1)
                self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
                self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 0)
                self.assertNotEqual((out / "local-discovery.json").read_bytes(), baseline_json)
                self.assertNotEqual((out / "local-discovery.md").read_bytes(), baseline_md)
            finally:
                release.touch(exist_ok=True)
                if first.poll() is None:
                    first.kill()
                    first.communicate()

    def test_process_death_releases_writer_ownership_without_manual_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            repo = make_repo(root)
            out = Path(tmp) / "out"
            self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
            (repo / "README.md").write_text("# changed before killed writer\n", "utf-8")

            ready = Path(tmp) / "writer-ready"
            release = Path(tmp) / "writer-release"
            first = start_writer_paused_before_journal(root, out, ready, release)
            wait_ready(first, ready)
            first.kill()
            first.communicate(timeout=10)

            self.assertFalse((out / ".local-discovery.transaction.json").exists())
            self.assertEqual(cli_main(["scan", str(root), "--output-dir", str(out)]), 0)
            self.assertEqual(cli_main(["verify", str(root), "--output-dir", str(out)]), 0)

    def test_symlinked_writer_lock_is_rejected_without_following_it(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            make_repo(root)
            out = Path(tmp) / "out"
            out.mkdir()
            outside = Path(tmp) / "outside-lock-target"
            outside.write_text("do-not-touch\n", "utf-8")
            lock = out / LOCK_NAME
            try:
                lock.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = cli_main(["scan", str(root), "--output-dir", str(out)])

            self.assertEqual(result, 2)
            self.assertEqual(outside.read_text("utf-8"), "do-not-touch\n")
            self.assertFalse((out / "local-discovery.json").exists())
            self.assertIn("output coordination failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
