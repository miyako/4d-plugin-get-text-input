"""Exercise the actual CLI with real Git and a local-only fake gh API."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import source_version as source


FAKE_GH = r'''
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
method = args[args.index("--method") + 1]
endpoint = args[args.index("--method") + 2]
payload = None
if "--input" in args and args[args.index("--input") + 1] == "-":
    payload = json.load(sys.stdin)
with open(os.environ["API_LOG"], "a") as log:
    log.write(json.dumps([method, endpoint, payload]) + "\n")
tag = os.environ["TEST_TAG"]
sha = os.environ["TEST_SHA"]
scenario = os.environ.get("API_SCENARIO", "")
def entry(name):
    return {"id": 9, "tag_name": name, "draft": True, "prerelease": False}
if method == "GET":
    page = int(endpoint.split("page=")[-1])
    if scenario in ("draft", "published"):
        data = entry(tag)
        data["draft"] = scenario == "draft"
        result = [data]
    elif scenario.startswith("page2"):
        if page == 1:
            result = [entry("v9.9.9")] * 100
        elif scenario == "page2error":
            sys.exit(1)
        elif scenario == "page2malformed":
            result = {"message": "bad page"}
        else:
            result = [entry(tag)] if scenario == "page2draft" else []
    elif scenario in ("auth404", "network"):
        sys.exit(1)
    elif scenario == "malformed":
        print("{")
        sys.exit(0)
    else:
        result = []
elif endpoint.endswith("/git/refs"):
    if scenario == "race":
        sys.exit(1)
    subprocess.run([os.environ["TEST_GIT"], "--git-dir", os.environ["TEST_ORIGIN"],
                    "update-ref", "refs/tags/" + tag, sha], check=True)
    result = {"ref": "refs/tags/" + tag, "object": {"sha": sha}}
elif endpoint.endswith("/releases"):
    if scenario == "release-error":
        sys.exit(1)
    result = {"id": 73, "tag_name": tag, "draft": False, "prerelease": False}
else:
    asset = pathlib.Path(args[args.index("--input") + 1])
    if scenario == "second-upload-error" and asset.suffix == ".dmg":
        sys.exit(1)
    result = {"id": 91, "name": asset.name, "size": asset.stat().st_size, "state": "uploaded"}
print(json.dumps(result))
'''


class PublicationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="publication-integration-")
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.root = self.area / "checkout"
        self.root.mkdir()
        self.origin = self.area / "origin.git"
        self.git_executable = shutil.which("git")
        for path in source.SOURCE_PATHS:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        shutil.copytree(ROOT / ".github/scripts", self.root / ".github/scripts")
        for path in ("4DPlugin-GET-TEXT-INPUT.cpp", "GET TEXT INPUT.vcxproj"):
            shutil.copy2(ROOT / path, self.root / path)
        self.generated = [
            Path("test/Plugins/GET TEXT INPUT.bundle/Contents") / path
            for path in ("MacOS/GET TEXT INPUT", "Info.plist",
                         "Resources/English.lproj/InfoPlist.strings",
                         "_CodeSignature/CodeResources")
        ]
        for path in self.generated:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, self.root / path)
        self.git("init", "-q", "-b", "selected")
        self.git("config", "user.name", "Offline fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "original tracked source and bundle")
        self.sha = self.git("rev-parse", "HEAD")
        subprocess.run([self.git_executable, "init", "--bare", "-q", str(self.origin)], check=True)
        self.git("remote", "add", "origin", str(self.origin))
        self.git("-c", "push.followTags=false", "push", "--no-follow-tags",
                 "-q", "origin", "HEAD:refs/heads/selected")
        self.version = source.read(self.root)
        self.assets = [f"GET TEXT INPUT-{self.version}.{ext}" for ext in ("zip", "dmg")]
        self.bin = self.area / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        fake.chmod(0o755)
        self.log = self.area / "api.jsonl"
        self.env = {
            **os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "offline/fixture", "GITHUB_OUTPUT": str(self.area / "output"),
            "API_LOG": str(self.log), "TEST_TAG": f"v{self.version}", "TEST_SHA": self.sha,
            "TEST_GIT": self.git_executable, "TEST_ORIGIN": str(self.origin),
        }

    def git(self, *args):
        return subprocess.run(
            [self.git_executable, "-C", str(self.root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def cli(self, command, scenario="", success=True):
        assets = self.assets if command == "publish" else []
        result = subprocess.run(
            [sys.executable, ".github/scripts/release.py", command, "--sha", self.sha, *assets],
            cwd=self.root, env={**self.env, "API_SCENARIO": scenario},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []

    def dirty_native_outputs(self):
        for index, path in enumerate(self.generated):
            with (self.root / path).open("ab") as stream:
                stream.write(f"\nnative-generated-{index}".encode())
        return {path: (self.root / path).read_bytes() for path in self.generated}

    def prepare_assets(self):
        for asset in self.assets:
            (self.root / asset).write_bytes(b"offline signed archive")

    def test_cli_allows_tracked_native_dirt_and_retains_every_byte(self):
        self.cli("preflight")
        before = self.dirty_native_outputs()
        self.prepare_assets()
        self.cli("check")
        self.cli("publish")
        self.assertEqual(before, {path: (self.root / path).read_bytes() for path in before})
        calls = self.calls()
        posts = [call for call in calls if call[0] == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertEqual(posts[0][2], {"ref": f"refs/tags/v{self.version}", "sha": self.sha})
        self.assertEqual(posts[1][2], {
            "tag_name": f"v{self.version}", "target_commitish": self.sha,
            "name": f"v{self.version}", "draft": False, "prerelease": False, "body": "",
            "generate_release_notes": False,
        })
        self.assertTrue(all("/releases/73/assets?name=" in call[1] for call in posts[2:]))
        self.assertTrue(all(call[0] in ("GET", "POST") for call in calls))
        self.assertTrue(self.git("status", "--porcelain"))

    def test_cli_rejects_staged_unstaged_and_hidden_index_source_changes_before_api(self):
        path = self.root / source.PROJECT
        original = path.read_bytes()
        for mutation in ("unstaged", "staged", "staged-with-clean-worktree"):
            with self.subTest(mutation=mutation):
                path.write_bytes(original + b"\n// changed source metadata\n")
                if mutation != "unstaged":
                    self.git("add", "--", str(source.PROJECT))
                if mutation == "staged-with-clean-worktree":
                    path.write_bytes(original)
                self.prepare_assets()
                for command in ("check", "publish"):
                    self.cli(command, success=False)
                    self.assertEqual(self.calls(), [])
                path.write_bytes(original)
                self.git("add", "--", str(source.PROJECT))

    def test_cli_rejects_changed_head_before_api(self):
        self.git("commit", "--allow-empty", "-qm", "HEAD changed")
        self.prepare_assets()
        self.cli("check", success=False)
        self.cli("publish", success=False)
        self.assertEqual(self.calls(), [])

    def test_cli_rejects_nonmetadata_source_and_native_configuration_dirt_before_api(self):
        for name in ("4DPlugin-GET-TEXT-INPUT.cpp", "GET TEXT INPUT.vcxproj"):
            path = self.root / name
            original = path.read_bytes()
            for staged in (False, True):
                with self.subTest(path=name, staged=staged):
                    path.write_bytes(original + b"\n// changed source\n")
                    if staged:
                        self.git("add", "--", name)
                    self.prepare_assets()
                    self.cli("check", success=False)
                    self.cli("publish", success=False)
                    self.assertEqual(self.calls(), [])
                    path.write_bytes(original)
                    self.git("add", "--", name)

    def test_preflight_still_requires_initial_clean_checkout(self):
        self.dirty_native_outputs()
        self.cli("preflight", success=False)
        self.assertEqual(self.calls(), [])

    def test_final_api_page_two_drafts_errors_and_malformed_fail_before_post(self):
        self.prepare_assets()
        for scenario in ("draft", "published", "page2draft", "page2error",
                         "page2malformed", "auth404", "network", "malformed"):
            with self.subTest(scenario=scenario):
                if self.log.exists():
                    self.log.unlink()
                self.cli("publish", scenario, success=False)
                self.assertFalse(any(call[0] == "POST" for call in self.calls()))
                if scenario.startswith("page2"):
                    self.assertEqual(len(self.calls()), 2)

    def test_absence_paginates_and_post_race_is_not_reused(self):
        self.prepare_assets()
        self.cli("check", "page2absent")
        self.assertEqual(len(self.calls()), 2)
        self.cli("publish", "race", success=False)
        self.assertEqual(len([call for call in self.calls() if call[0] == "POST"]), 1)

    def test_release_failure_retains_created_tag_without_rollback_or_retry(self):
        self.prepare_assets()
        self.cli("publish", "release-error", success=False)
        self.assertEqual(self.git("ls-remote", "--refs", "origin",
                                 f"refs/tags/v{self.version}").split()[0], self.sha)
        self.assertEqual([call[0] for call in self.calls()], ["GET", "POST", "POST"])

    def test_second_upload_failure_retains_tag_release_and_first_asset(self):
        self.prepare_assets()
        self.cli("publish", "second-upload-error", success=False)
        self.assertEqual(len(self.calls()), 5)
        self.assertEqual(self.calls()[-1][0], "POST")
        self.assertIn("/releases/73/assets?", self.calls()[-1][1])
        self.assertEqual(self.git("ls-remote", "--refs", "origin",
                                 f"refs/tags/v{self.version}").split()[0], self.sha)
