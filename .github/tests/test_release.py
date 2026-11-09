import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import release


class ReleaseTests(unittest.TestCase):
    def test_api_uses_explicit_get_post_and_exact_asset_bytes_without_retry(self):
        with patch.object(release, "run", return_value='{"id": 1}') as run:
            release.api("repos/owner/repo/releases")
            self.assertEqual(run.call_args.args,
                             ("gh", "api", "--method", "GET", "repos/owner/repo/releases"))
            release.api("repos/owner/repo/git/refs", {"ref": "refs/tags/v1.2.3", "sha": "a" * 40})
            self.assertEqual(json.loads(run.call_args.kwargs["input"]),
                             {"ref": "refs/tags/v1.2.3", "sha": "a" * 40})
            release.api("https://uploads.github.com/example", asset=Path("archive.zip"))
            self.assertEqual(run.call_args.args[-4:],
                             ("--header", "Content-Type: application/octet-stream",
                              "--input", "archive.zip"))
        with patch.object(release, "run", side_effect=subprocess.CalledProcessError(1, ["gh"])) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.api("repos/owner/repo/releases")
            self.assertEqual(run.call_count, 1)

    def test_malformed_release_pages_and_entries_fail_closed(self):
        valid = {"id": 1, "tag_name": "v0.0.1", "draft": False, "prerelease": False}
        malformed = [None, {}, "[]", [None], [valid] * 101]
        for key in valid:
            missing = dict(valid)
            del missing[key]
            malformed.append([missing])
        for key, values in {
            "id": (True, 0, -1, "1"),
            "tag_name": ("", 1, None),
            "draft": (0, "false", None),
            "prerelease": (0, "false", None),
        }.items():
            malformed.extend([{**valid, key: value}] for value in values)
        for result in malformed:
            with self.subTest(result=result), patch.object(release, "remote_ref", return_value=None), \
                    patch.object(release, "api", return_value=result):
                with self.assertRaisesRegex(ValueError, "Malformed"):
                    release.available("owner/repo", "v1.2.3")

    def test_existing_remote_tag_rejected_without_api(self):
        with patch.object(release, "remote_ref", return_value="a" * 40), \
                patch.object(release, "api") as api:
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.available("owner/repo", "v1.2.3")
            api.assert_not_called()

    def test_remote_ref_is_exact_and_network_errors_propagate(self):
        ref = "refs/tags/v1.2.3"
        for output in ("malformed", f"{'a' * 40} refs/tags/v1.2.30",
                       f"{'a' * 40} {ref}\n{'b' * 40} {ref}", f"bad {ref}"):
            with self.subTest(output=output), patch.object(release, "run", return_value=output):
                with self.assertRaises(ValueError):
                    release.remote_ref(ref)
        with patch.object(release, "run", side_effect=subprocess.CalledProcessError(1, ["git"])):
            with self.assertRaises(subprocess.CalledProcessError):
                release.remote_ref(ref)

    def test_malformed_create_responses_stop_without_rollback_or_reuse(self):
        sha, tag = "a" * 40, "v1.2.3"
        tag_response = {"ref": f"refs/tags/{tag}", "object": {"sha": sha}}
        release_response = {"id": 73, "tag_name": tag, "draft": False, "prerelease": False}
        cases = [
            [None],
            [{"ref": f"refs/tags/{tag}", "object": {"sha": "b" * 40}}],
            [tag_response, None],
            [tag_response, {**release_response, "id": True}],
            [tag_response, {**release_response, "tag_name": "v1.2.4"}],
            [tag_response, {**release_response, "draft": True}],
            [tag_response, {**release_response, "prerelease": True}],
            [tag_response, release_response, {"id": 9, "name": "wrong.zip", "size": 1,
                                              "state": "uploaded"}],
            [tag_response, release_response, {"id": 9, "name": "GET TEXT INPUT-1.2.3.zip",
                                              "size": 2, "state": "uploaded"}],
            [tag_response, release_response, {"id": 9, "name": "GET TEXT INPUT-1.2.3.zip",
                                              "size": 1, "state": "new"}],
        ]
        with tempfile.TemporaryDirectory() as temp:
            previous = Path.cwd()
            os.chdir(temp)
            try:
                assets = [Path(f"GET TEXT INPUT-1.2.3.{ext}") for ext in ("zip", "dmg")]
                for asset in assets:
                    asset.write_bytes(b"x")
                for responses in cases:
                    with self.subTest(responses=responses), \
                            patch.object(release, "context", return_value=("owner/repo", "1.2.3", tag)), \
                            patch.object(release, "available"), \
                            patch.object(release, "remote_ref", return_value=sha), \
                            patch.object(release, "api", side_effect=responses) as api, \
                            contextlib.redirect_stdout(None):
                        with self.assertRaises(ValueError):
                            release.publish(sha, assets)
                        self.assertEqual(api.call_count, len(responses))
                        self.assertFalse(any("DELETE" in str(call) or "PATCH" in str(call)
                                             for call in api.call_args_list))
            finally:
                os.chdir(previous)

    def test_wrong_missing_and_empty_assets_fail_before_remote_mutation(self):
        with patch.object(release, "context", return_value=("owner/repo", "1.2.3", "v1.2.3")), \
                patch.object(release, "api") as api, patch.object(release, "available") as available:
            for assets in ([], [Path("wrong.zip")], [Path("GET TEXT INPUT-1.2.3.zip"),
                                                   Path("GET TEXT INPUT-1.2.3.dmg")]):
                with self.subTest(assets=assets), self.assertRaises(ValueError):
                    release.publish("a" * 40, assets)
            available.assert_not_called()
            api.assert_not_called()

    def test_cli_check_is_not_the_initial_global_clean_preflight(self):
        with tempfile.NamedTemporaryFile() as output, \
                patch.dict(os.environ, {"GITHUB_OUTPUT": output.name}), \
                patch.object(release, "check", return_value=("1.2.3", "v1.2.3")) as check, \
                patch.object(release, "preflight", return_value=("1.2.3", "v1.2.3")) as preflight:
            release.main(["check", "--sha", "a" * 40])
            check.assert_called_once_with("a" * 40)
            preflight.assert_not_called()
            release.main(["preflight", "--sha", "a" * 40])
            preflight.assert_called_once_with("a" * 40)
