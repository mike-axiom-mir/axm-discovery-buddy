from pathlib import Path
import json
import os
import tempfile
import unittest

import discovery_buddy.cli as cli
from discovery_buddy.cli import main as cli_main
from discovery_buddy.recovery_desk import inspect_recovery, render_html, render_text


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
    (repo / "README.md").write_text("# initial\n", "utf-8")
    marker = repo / ".axm" / "discovery-public.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"schema":"axm.discovery-public/v1","public":true}\n', "utf-8")
    return repo


def stage_interrupted_generation(root: Path, repo: Path, out: Path, *, public: bool, published: int) -> tuple[Path, Path, Path]:
    (repo / "README.md").write_text("# changed before interrupted publication\n", "utf-8")
    index = cli.scan_workspace(root, max_depth=4, public=public)
    json_text, md_text = cli._serialized(index)
    json_path, md_path = cli._paths(out, public)
    journal = cli._transaction_path(json_path)
    targets = ((json_path, json_text), (md_path, md_text))
    staged = {path: cli._stage_text(path, text) for path, text in targets}
    backups = {path: cli._backup_existing(path) for path, _text in targets}
    cli._write_transaction(journal, [(path, staged[path], backups[path]) for path, _text in targets])
    for path, _text in targets[:published]:
        os.replace(staged[path], path)
    return json_path, md_path, journal


class RecoveryDeskTests(unittest.TestCase):
    def _baseline(self, tmp: str, *, public: bool = False):
        root = Path(tmp) / "workspace"
        root.mkdir()
        repo = make_repo(root)
        out = Path(tmp) / "out"
        argv = ["scan", str(root), "--output-dir", str(out)]
        if public:
            argv.append("--public")
        self.assertEqual(cli_main(argv), 0)
        json_path, md_path = cli._paths(out, public)
        return root, repo, out, json_path.read_bytes(), md_path.read_bytes()

    def test_preview_explains_mixed_pair_before_explicit_rollback_without_mutating_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, baseline_json, baseline_md = self._baseline(tmp)
            json_path, md_path, journal = stage_interrupted_generation(root, repo, out, public=False, published=1)
            before_json = json_path.read_bytes()
            before_md = md_path.read_bytes()
            before_journal = journal.read_bytes()

            report = inspect_recovery(out)

            self.assertEqual(report["status"], "READY_ROLLBACK_LAST_GOOD")
            self.assertEqual([row["current_state"] for row in report["targets"]], ["MATCHES_NEW", "MATCHES_LAST_GOOD"])
            self.assertTrue(all(row["backup_state"] == "MATCHES_LAST_GOOD" for row in report["targets"]))
            self.assertEqual(json_path.read_bytes(), before_json)
            self.assertEqual(md_path.read_bytes(), before_md)
            self.assertEqual(journal.read_bytes(), before_journal)
            self.assertIn("Recovery stays explicit", render_html(report))
            self.assertIn("ROLLED_BACK_LAST_GOOD", render_text(report))

            self.assertEqual(cli_main(["recover", "--output-dir", str(out)]), 0)
            self.assertEqual(json_path.read_bytes(), baseline_json)
            self.assertEqual(md_path.read_bytes(), baseline_md)
            self.assertFalse(journal.exists())

    def test_preview_distinguishes_complete_new_generation_from_rollback_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, baseline_json, baseline_md = self._baseline(tmp)
            json_path, md_path, journal = stage_interrupted_generation(root, repo, out, public=False, published=2)
            new_json = json_path.read_bytes()
            new_md = md_path.read_bytes()
            self.assertNotEqual(new_json, baseline_json)
            self.assertNotEqual(new_md, baseline_md)

            report = inspect_recovery(out)

            self.assertEqual(report["status"], "READY_FINALIZE_NEW")
            self.assertTrue(all(row["current_state"] == "MATCHES_NEW" for row in report["targets"]))
            self.assertEqual(cli_main(["recover", "--output-dir", str(out)]), 0)
            self.assertEqual(json_path.read_bytes(), new_json)
            self.assertEqual(md_path.read_bytes(), new_md)
            self.assertFalse(journal.exists())

    def test_corrupt_rollback_evidence_is_visibly_held_and_offers_no_recovery_button(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, _baseline_json, _baseline_md = self._baseline(tmp)
            json_path, md_path, journal = stage_interrupted_generation(root, repo, out, public=False, published=1)
            transaction = json.loads(journal.read_text("utf-8"))
            backup = out / transaction["targets"][0]["backup"]
            backup.write_bytes(b"corrupted-backup\n")
            before = (json_path.read_bytes(), md_path.read_bytes(), journal.read_bytes())

            report = inspect_recovery(out)

            self.assertEqual(report["status"], "HELD_INVALID_EVIDENCE")
            self.assertIn("does not match", report["summary"])
            self.assertNotIn('id="copy-command"', render_html(report))
            self.assertEqual((json_path.read_bytes(), md_path.read_bytes(), journal.read_bytes()), before)

    def test_no_transaction_is_clear_without_inventing_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            _root, _repo, out, _baseline_json, _baseline_md = self._baseline(tmp)
            report = inspect_recovery(out)
            self.assertEqual(report["status"], "CLEAR")
            self.assertEqual(report["targets"], [])
            self.assertNotIn('id="copy-command"', render_html(report))

    def test_public_pair_keeps_public_flag_in_explicit_next_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, repo, out, _baseline_json, _baseline_md = self._baseline(tmp, public=True)
            stage_interrupted_generation(root, repo, out, public=True, published=1)
            report = inspect_recovery(out, public=True)
            self.assertEqual(report["mode"], "PUBLIC_SAFE")
            self.assertEqual(report["output_pair"], ["public-discovery.json", "public-discovery.md"])
            self.assertEqual(report["recovery_command_argv"][-1], "--public")

    def test_html_escapes_local_evidence_instead_of_executing_it(self):
        report = {
            "schema": "axm.discovery-recovery-preview/v0.1",
            "mode": "LOCAL_ONLY",
            "status": "HELD_INVALID_EVIDENCE",
            "status_title": "RECOVERY EVIDENCE HELD",
            "summary": "unsafe <script>alert(1)</script>",
            "output_pair": ["local-discovery.json", "local-discovery.md"],
            "journal": ".local-discovery.transaction.json",
            "targets": [],
            "recovery_command_argv": [],
            "recovery_command_posix": "",
            "recovery_effect": "No action",
            "error": "OSError: <img src=x onerror=alert(2)>",
            "authority": {"display_only": True},
            "truth_ceiling": "Display only",
        }
        rendered = render_html(report)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertNotIn("<img src=x onerror=alert(2)>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)


if __name__ == "__main__":
    unittest.main()
