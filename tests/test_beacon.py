import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / ".axm" / "beacon" / "beacon.py"
spec = importlib.util.spec_from_file_location("axm_beacon", MODULE_PATH)
beacon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = beacon
assert spec.loader is not None
spec.loader.exec_module(beacon)
core = sys.modules["core"]
network = sys.modules["network"]


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, check=True, text=True, stdout=subprocess.PIPE)
    return proc.stdout.strip()


class BeaconTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="axm-beacon-test-"))
        git(root, "init", "-q")
        git(root, "config", "user.email", "beacon-test@example.invalid")
        git(root, "config", "user.name", "Beacon Test")
        (root / ".axm").mkdir()
        (root / ".axm" / "beacon.json").write_text(
            json.dumps(
                {
                    "protocol": beacon.PROTOCOL,
                    "repo": "example/receiver",
                    "interests": ["cache organ deterministic receipts"],
                    "publish": {"include": ["**/*"], "exclude": [".axm/beacon/feed/**"]},
                }
            ),
            encoding="utf-8",
        )
        (root / "README.md").write_text("seed\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "seed")
        return root

    def add_reusable_change(self, root: Path) -> tuple[str, str]:
        base = git(root, "rev-parse", "HEAD")
        (root / "src" / "organs").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "docs").mkdir()
        (root / "src" / "organs" / "cache.py").write_text(
            "def stable_cache_key(parts):\n    return '::'.join(sorted(parts))\n",
            encoding="utf-8",
        )
        (root / "tests" / "test_cache.py").write_text(
            "from src.organs.cache import stable_cache_key\n\ndef test_key():\n    assert stable_cache_key(['b','a']) == 'a::b'\n",
            encoding="utf-8",
        )
        (root / "docs" / "cache-receipt.md").write_text("# Deterministic cache receipt\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "add deterministic cache organ")
        head = git(root, "rev-parse", "HEAD")
        return base, head

    def test_publish_is_deterministic_and_evidence_backed(self):
        root = self.make_repo()
        base, head = self.add_reusable_change(root)
        out1 = root / "feed1"
        out2 = root / "feed2"
        cap1 = beacon.publish(root, base, head, out1, ".axm/beacon.json")
        cap2 = beacon.publish(root, base, head, out2, ".axm/beacon.json")
        self.assertEqual(cap1["capsule_id"], cap2["capsule_id"])
        self.assertFalse(cap1["transfer"]["auto_apply"])
        self.assertEqual(cap1["signals"]["meaning"], "candidate-for-inspection-not-truth")
        self.assertIn("organ-capability", cap1["signals"]["kinds"])
        self.assertIn("test-change", cap1["signals"]["kinds"])
        paths = {entry["path"] for entry in cap1["evidence"]["files"]}
        self.assertIn("src/organs/cache.py", paths)
        cache = next(entry for entry in cap1["evidence"]["files"] if entry["path"] == "src/organs/cache.py")
        self.assertIn("stable_cache_key", cache["symbols"])
        self.assertIn("deterministic", cap1["signals"]["tags"])
        ok, reason = beacon.verify_capsule(cap1)
        self.assertTrue(ok, reason)

    def test_tampering_breaks_capsule_identity(self):
        root = self.make_repo()
        base, head = self.add_reusable_change(root)
        capsule = beacon.publish(root, base, head, root / "feed", ".axm/beacon.json")
        capsule["signals"]["attention_score"] += 100
        ok, _ = beacon.verify_capsule(capsule)
        self.assertFalse(ok)

    def test_excluded_paths_never_leak_into_patch(self):
        root = self.make_repo()
        config_path = root / ".axm" / "beacon.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["publish"]["exclude"].append("private/**")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        base = git(root, "rev-parse", "HEAD")
        (root / "src").mkdir()
        (root / "private").mkdir()
        (root / "src" / "safe.py").write_text("def share_me():\n    return 1\n", encoding="utf-8")
        (root / "private" / "secret.txt").write_text("DO-NOT-LEAK-123\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "safe plus excluded secret")
        head = git(root, "rev-parse", "HEAD")
        capsule = beacon.publish(root, base, head, root / "feed", ".axm/beacon.json")
        patch = (root / "feed" / "patches" / f"{capsule['capsule_id']}.patch").read_text(encoding="utf-8")
        self.assertIn("share_me", patch)
        self.assertNotIn("DO-NOT-LEAK-123", patch)
        self.assertNotIn("private/secret.txt", patch)

    def test_receiver_interest_changes_ranking_without_changing_truth(self):
        indexes = [
            {
                "protocol": beacon.PROTOCOL,
                "repo": "example/source",
                "capsules": [
                    {
                        "capsule_id": "a",
                        "repo": "example/source",
                        "tags": ["cache", "deterministic"],
                        "kinds": ["organ-capability"],
                        "attention_score": 5,
                    },
                    {
                        "capsule_id": "b",
                        "repo": "example/source",
                        "tags": ["shader", "visual"],
                        "kinds": ["code"],
                        "attention_score": 8,
                    },
                ],
            }
        ]
        ranked = beacon.rank_candidates(indexes, "example/receiver", ["deterministic cache"])
        self.assertEqual(ranked[0]["capsule_id"], "a")
        self.assertEqual(ranked[0]["interest_overlap"], ["cache", "deterministic"])

    def test_fetch_identity_binds_requested_id_and_source_repo(self):
        root = self.make_repo()
        base, head = self.add_reusable_change(root)
        capsule = beacon.publish(root, base, head, root / "feed", ".axm/beacon.json")
        patch_hash = network._validate_fetch_identity(
            capsule,
            "example/receiver",
            capsule["capsule_id"],
        )
        self.assertEqual(patch_hash, capsule["evidence"]["patch_sha256"])

        with self.assertRaisesRegex(beacon.BeaconError, "requested capsule id"):
            network._validate_fetch_identity(capsule, "example/receiver", "0" * 64)
        with self.assertRaisesRegex(beacon.BeaconError, "source repo mismatch"):
            network._validate_fetch_identity(capsule, "example/other", capsule["capsule_id"])

    def test_fetch_identity_requires_patch_hash_even_for_self_consistent_capsule(self):
        root = self.make_repo()
        base, head = self.add_reusable_change(root)
        capsule = beacon.publish(root, base, head, root / "feed", ".axm/beacon.json")
        forged = json.loads(json.dumps(capsule))
        del forged["evidence"]["patch_sha256"]
        forged_core = {key: value for key, value in forged.items() if key != "capsule_id"}
        forged["capsule_id"] = core.sha256_text(core.canonical_json(forged_core))
        ok, reason = beacon.verify_capsule(forged)
        self.assertTrue(ok, reason)

        with self.assertRaisesRegex(beacon.BeaconError, "patch_sha256"):
            network._validate_fetch_identity(forged, "example/receiver", forged["capsule_id"])

    def test_fetch_rejects_unsafe_capsule_id_before_network_or_disk(self):
        root = self.make_repo()
        dest = root / "inbox"
        with mock.patch.object(network, "_request_json") as request_json:
            with self.assertRaisesRegex(beacon.BeaconError, "invalid capsule id"):
                network.fetch_capsule(
                    "example/source",
                    "../escape",
                    "axm-beacon-feed",
                    dest,
                )
            request_json.assert_not_called()
        self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
