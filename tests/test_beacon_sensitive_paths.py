import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / ".axm" / "beacon" / "beacon.py"
spec = importlib.util.spec_from_file_location("axm_beacon_sensitive", MODULE_PATH)
beacon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = beacon
assert spec.loader is not None
spec.loader.exec_module(beacon)
core = sys.modules["core"]


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return proc.stdout.strip()


class BeaconSensitivePathTests(unittest.TestCase):
    def make_repo(self, publish=None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="axm-beacon-sensitive-"))
        git(root, "init", "-q")
        git(root, "config", "user.email", "beacon-test@example.invalid")
        git(root, "config", "user.name", "Beacon Test")
        (root / ".axm").mkdir()
        if publish is not None:
            (root / ".axm" / "beacon.json").write_text(
                json.dumps({
                    "protocol": beacon.PROTOCOL,
                    "repo": "example/source",
                    "publish": publish,
                }),
                encoding="utf-8",
            )
        (root / "README.md").write_text("seed\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-qm", "seed")
        return root

    def publish_change(self, root: Path) -> tuple[dict, str]:
        base = git(root, "rev-parse", "HEAD")
        git(root, "add", ".")
        git(root, "commit", "-qm", "candidate change")
        head = git(root, "rev-parse", "HEAD")
        capsule = beacon.publish(
            root, base, head, root / "feed", ".axm/beacon.json"
        )
        patch = (
            root / "feed" / "patches" / f"{capsule['capsule_id']}.patch"
        ).read_text(encoding="utf-8")
        return capsule, patch

    def test_default_policy_keeps_environment_secret_out_of_feed(self):
        root = self.make_repo()
        (root / ".env").write_text(
            "AXM_PRIVATE_TOKEN=DO-NOT-PUBLISH\n", encoding="utf-8"
        )
        (root / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")

        capsule, patch = self.publish_change(root)

        self.assertEqual(
            [entry["path"] for entry in capsule["evidence"]["files"]],
            ["safe.py"],
        )
        self.assertNotIn(".env", patch)
        self.assertNotIn("DO-NOT-PUBLISH", patch)
        self.assertIn("safe.py", patch)

    def test_config_cannot_opt_sensitive_path_back_into_feed(self):
        root = self.make_repo({
            "include": [".env"],
            "exclude": [],
        })
        (root / ".env").write_text(
            "AXM_PRIVATE_TOKEN=STILL-PRIVATE\n", encoding="utf-8"
        )

        capsule, patch = self.publish_change(root)

        self.assertEqual(capsule["evidence"]["files"], [])
        self.assertEqual(patch, "")

    def test_explicit_public_environment_template_remains_shareable(self):
        root = self.make_repo()
        (root / ".env.example").write_text(
            "AXM_PRIVATE_TOKEN=replace-me\n", encoding="utf-8"
        )

        capsule, patch = self.publish_change(root)

        self.assertEqual(
            [entry["path"] for entry in capsule["evidence"]["files"]],
            [".env.example"],
        )
        self.assertIn(".env.example", patch)

    def test_sensitive_path_families_are_blocked_without_hiding_templates(self):
        config = {"publish": {"include": ["**/*"], "exclude": []}}
        blocked = [
            ".env",
            "nested/.env.production",
            ".npmrc",
            "home/.netrc",
            ".pypirc",
            ".aws/credentials",
            "home/.aws/credentials",
            ".ssh/id_rsa",
            "nested/id_ed25519",
            "bridge-token.txt",
            "config/credentials.json",
            "config/secrets.yaml",
        ]
        for path in blocked:
            with self.subTest(path=path):
                self.assertFalse(core.path_allowed(path, config))

        for path in [
            ".env.example",
            "config/credentials.json.sample",
            ".ssh/id_rsa.template",
            ".ssh/id_rsa.pub",
        ]:
            with self.subTest(path=path):
                self.assertTrue(core.path_allowed(path, config))


if __name__ == "__main__":
    unittest.main()
