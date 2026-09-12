from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "build_portable_discovery.py"


def _run(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


class PortableZipappTests(unittest.TestCase):
    def test_build_is_byte_reproducible_and_inventory_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first = tmp_path / "first.pyz"
            first_receipt = tmp_path / "first.receipt.json"
            second = tmp_path / "second.pyz"
            second_receipt = tmp_path / "second.receipt.json"

            _run([sys.executable, str(BUILDER), "build", "--output", str(first), "--receipt", str(first_receipt)])
            _run([sys.executable, str(BUILDER), "build", "--output", str(second), "--receipt", str(second_receipt)])
            self.assertEqual(first.read_bytes(), second.read_bytes())

            receipt = json.loads(first_receipt.read_text("utf-8"))
            self.assertEqual(receipt["schema"], "axm.discovery-portable-zipapp/v0.1")
            self.assertEqual(receipt["authority"]["classification"], "PORTABLE_TOOL_ONLY")
            self.assertFalse(receipt["authority"]["discovered_capability_execution"])
            self.assertFalse(receipt["authority"]["automatic_selection"])
            self.assertFalse(receipt["authority"]["discovered_capability_installation"])
            self.assertFalse(receipt["authority"]["merge"])
            self.assertFalse(receipt["authority"]["canon"])

            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
            expected_sources = sorted(
                path.relative_to(ROOT).as_posix()
                for path in (ROOT / "discovery_buddy").glob("*.py")
            )
            self.assertEqual(names, ["__main__.py", *expected_sources])
            self.assertTrue(all(not name.startswith(("tests/", "docs/", ".github/")) for name in names))

            verified = _run(
                [sys.executable, str(BUILDER), "verify", "--output", str(first), "--receipt", str(first_receipt)]
            )
            self.assertIn("portable-discovery: PASS", verified.stdout)

    def test_zipapp_runs_scan_verify_and_query_from_unrelated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = tmp_path / "discovery-buddy.pyz"
            receipt = tmp_path / "discovery-buddy.pyz.receipt.json"
            _run([sys.executable, str(BUILDER), "build", "--output", str(artifact), "--receipt", str(receipt)])

            workspace = tmp_path / "workspace"
            provider = workspace / "provider"
            (provider / ".git" / "refs" / "heads").mkdir(parents=True)
            (provider / ".git" / "HEAD").write_text("ref: refs/heads/main\n", "ascii")
            (provider / ".git" / "refs" / "heads" / "main").write_text("a" * 40 + "\n", "ascii")
            (provider / ".axm").mkdir()
            (provider / ".axm" / "discovery-public.json").write_text(
                json.dumps(
                    {
                        "schema": "axm.discovery-public/v1",
                        "public": True,
                        "repo": "smoke/provider",
                        "display_name": "Smoke Provider",
                    }
                )
                + "\n",
                "utf-8",
            )
            (provider / "registry").mkdir()
            (provider / "registry" / "capabilities.jsonl").write_text(
                json.dumps(
                    {
                        "id": "axm.smoke.capability/v1",
                        "providers": ["smoke.cli"],
                        "consumers": [],
                        "status": "TEST",
                    },
                    sort_keys=True,
                )
                + "\n",
                "utf-8",
            )

            out = tmp_path / "out"
            unrelated = tmp_path / "consumer"
            unrelated.mkdir()
            _run(
                [
                    sys.executable,
                    str(artifact),
                    "scan",
                    str(workspace),
                    "--output-dir",
                    str(out),
                    "--public",
                ],
                cwd=unrelated,
            )
            _run(
                [
                    sys.executable,
                    str(artifact),
                    "verify",
                    str(workspace),
                    "--output-dir",
                    str(out),
                    "--public",
                ],
                cwd=unrelated,
            )
            result = _run(
                [
                    sys.executable,
                    str(artifact),
                    "query",
                    str(out / "public-discovery.json"),
                    "--capability-id",
                    "axm.smoke.capability/v1",
                    "--provider",
                    "smoke.cli",
                ],
                cwd=unrelated,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["summary"], {"matches": 1})
            self.assertEqual(payload["candidates"][0]["repository"], "smoke/provider")
            self.assertEqual(payload["authority"]["classification"], "DISCOVERY_EVIDENCE_ONLY")
            self.assertTrue(
                all(
                    payload["authority"][key] is False
                    for key in ("execute", "install", "select", "merge", "canon")
                )
            )

    def test_verify_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifact = tmp_path / "discovery-buddy.pyz"
            receipt = tmp_path / "receipt.json"
            _run([sys.executable, str(BUILDER), "build", "--output", str(artifact), "--receipt", str(receipt)])
            artifact.write_bytes(artifact.read_bytes() + b"tamper")
            result = _run(
                [sys.executable, str(BUILDER), "verify", "--output", str(artifact), "--receipt", str(receipt)],
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("HOLD", result.stderr)

    def test_source_symlink_is_rejected(self) -> None:
        spec = importlib.util.spec_from_file_location("portable_builder", BUILDER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "discovery_buddy"
            package.mkdir()
            for name in ("__init__.py", "__main__.py", "cli.py", "scanner.py", "query.py"):
                (package / name).write_text("# fixture\n", "utf-8")
            target = root / "outside.py"
            target.write_text("# outside\n", "utf-8")
            try:
                (package / "linked.py").symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "symlink"):
                module._collect_sources(root)


if __name__ == "__main__":
    unittest.main()
